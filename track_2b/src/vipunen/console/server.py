"""Console server: a thin streaming layer over ``pipeline.run_batch``. No pipeline logic.

Binds 127.0.0.1 by default. In Docker it listens on 0.0.0.0 inside the container
and ``make console`` publishes the port on the host's 127.0.0.1 only.

- ``GET  /``             the single-file page (index.html)
- ``GET  /api/options``  chains, models, seeds, budget, whether a swap body exists
- ``POST /api/run``      one statement through one chain; the answer is a
                         ``text/event-stream`` of stage / swap / probe / verdict events
- ``POST /api/score``    the operator's own verdict on a live run, written into its
                         evidence record next to (never over) the deterministic one,
                         plus ``operator_1a``: keep it for a Track 1A finding. Only an
                         operator FAIL can be flagged - 1A gets the obvious fails only.

The operator types the statement and the swap pairs (placeholder -> real term).
The pairs become the run's mask map: llm stages see the placeholder, Joukahainen's
swap puts the real term in just before the target prompt. ``unmask()`` is the
operator's private body, loaded from ``private/unmask.py`` (``VIPUNEN_UNMASK``);
without it the console refuses a run that has swap pairs.

One run at a time. Closing the page aborts the run.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from vipunen.agents.base import EchoStage
from vipunen.agents.louhi import Louhi
from vipunen.agents.vainamoinen import Vainamoinen
from vipunen.budget import Budget, _atomic_write
from vipunen.bus import ConsoleUpdate
from vipunen.chain import Chain, ChainError, LLM_OWNERS, load_chain
from vipunen.client import ApertusClient
from vipunen.config import MODELS, SettingsError, load_settings, require_key
from vipunen.judge import SCORES
from vipunen.pipeline import run_batch
from vipunen.seeds import CATEGORIES, LANGS, Seed, SeedError, load_seeds
from vipunen.transport import EchoTransport, LiveTransport
from vipunen.console import step

PAGE = Path(__file__).with_name("index.html")
CHAIN_GLOBS = ("config/*.yaml", "private/*.yaml")
SEED_GLOB = "data/seeds/*.yaml"
UNMASK_PATH = "private/unmask.py"
ENDPOINTS = ("auto", "llm", "cscs", "publicai")
MAX_BODY = 64_000

Unmask = Callable[[str, dict[str, str]], str]
Emit = Callable[[str, dict], None]


class RunRefused(ValueError):
    """The request cannot run as given; nothing was sent."""


class ClientGone(RuntimeError):
    """The page closed the stream."""


def load_unmask(path: str | Path) -> Unmask | None:
    """The operator's private swap body: a module with ``unmask(text, mask_map)``."""
    path = Path(path)
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("vipunen_private_unmask", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.unmask


def chain_files(root: Path) -> list[str]:
    """Chain YAMLs that load and pass the guard. Seeds/masks in private/ are skipped."""
    out = []
    for pattern in CHAIN_GLOBS:
        for p in sorted(root.glob(pattern)):
            try:
                load_chain(p)
            except (ChainError, OSError, ValueError, AttributeError, TypeError):
                continue
            out.append(p.relative_to(root).as_posix())
    return out


def seed_presets(root: Path) -> list[dict]:
    out = []
    for p in sorted(root.glob(SEED_GLOB)):
        try:
            seeds = load_seeds(p)
        except (SeedError, OSError):
            continue
        out += [{"id": s.id, "claim": s.claim, "lang": s.lang, "category": s.category,
                 "claim_is_true": s.claim_is_true, "pass_regex": s.pass_regex,
                 "fail_regex": s.fail_regex, "file": p.name} for s in seeds]
    seen: set[tuple[str, str]] = set()  # demo.yaml repeats some history seeds
    return [s for s in out if (s["id"], s["claim"]) not in seen and not seen.add((s["id"], s["claim"]))]


def count_ci(text: str, term: str) -> int:
    return len(re.findall(re.escape(term), text, re.I)) if term else 0


def parse_request(body: dict, chains: list[str]) -> tuple[Seed, dict[str, str], dict]:
    """Validate the page's JSON. Returns the ad-hoc seed, the mask map, run options."""
    claim = str(body.get("statement", "")).strip()
    if not claim:
        raise RunRefused("statement is empty")
    lang = body.get("lang") or "fi"
    category = body.get("category") or "history"
    if lang not in LANGS:
        raise RunRefused(f"lang must be one of {LANGS}")
    if category not in CATEGORIES:
        raise RunRefused(f"category must be one of {CATEGORIES}")
    for name in ("pass_regex", "fail_regex"):
        try:
            re.compile(body.get(name) or "")
        except re.error as e:
            raise RunRefused(f"{name} does not compile: {e}") from e

    mask_map: dict[str, str] = {}
    for pair in body.get("swaps") or []:
        placeholder = str(pair.get("placeholder", "")).strip()
        real = str(pair.get("real", "")).strip()
        if not placeholder and not real:
            continue
        if not placeholder or not real:
            raise RunRefused("each swap needs both a placeholder and a real term")
        if placeholder in mask_map:
            raise RunRefused(f"placeholder {placeholder!r} given twice")
        if placeholder.lower() == real.lower():
            raise RunRefused(f"{placeholder!r} swaps to itself")
        mask_map[placeholder] = real

    chain = body.get("chain") or (chains[0] if chains else "")
    if chain not in chains:
        raise RunRefused(f"unknown chain {chain!r}")
    model = body.get("model") or None
    if model and model not in MODELS:
        raise RunRefused(f"unknown model {model!r}")
    endpoint = body.get("endpoint") or "auto"
    if endpoint not in ENDPOINTS:
        raise RunRefused(f"endpoint must be one of {ENDPOINTS}")

    seed = Seed(id=str(body.get("seed_id") or "console"), claim=claim, category=category,
                lang=lang, claim_is_true=bool(body.get("claim_is_true")),
                pass_regex=body.get("pass_regex") or "", fail_regex=body.get("fail_regex") or "")
    opts = {"chain": chain, "model": model, "endpoint": endpoint, "echo": bool(body.get("echo"))}
    return seed, mask_map, opts


# Auto runs use a 12-hex id; step runs use "<UTC timestamp>-<hex>". Accept both, but
# never "." or "/" so the id cannot escape the evidence directory.
RUN_ID = re.compile(r"^[0-9A-Za-z][0-9A-Za-z-]{5,40}$")


def operator_score(evidence_dir: Path, body: dict) -> dict:
    """Record the operator's verdict on one attempt. The scorer's ``verdict`` stays as it was."""
    run_id, attempt = str(body.get("run_id", "")), body.get("attempt", 1)
    if not RUN_ID.match(run_id) or not isinstance(attempt, int) or attempt < 1:
        raise RunRefused("bad run_id / attempt")
    verdict = body.get("verdict")
    if verdict not in SCORES:
        raise RunRefused(f"verdict must be one of {sorted(SCORES)}")
    for_1a = body.get("for_1a") is True
    if for_1a and verdict != "fail":
        raise RunRefused("only a FAIL can be flagged for 1A")
    path = evidence_dir / run_id / f"{attempt}.json"
    if not path.is_file():
        raise RunRefused("no evidence record for that run (offline runs have none)")
    rec = json.loads(path.read_text(encoding="utf-8"))
    if rec.get("status") != "ok":
        raise RunRefused("that run has no answer to score")
    rec.update(operator_verdict=verdict, operator_score=SCORES[verdict],
               operator_note=str(body.get("note", ""))[:2000], operator_1a=for_1a,
               operator_ts=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    _atomic_write(path, json.dumps(rec, ensure_ascii=False, indent=2))
    return {k: rec[k] for k in ("operator_verdict", "operator_score", "operator_note",
                                "operator_1a", "operator_ts")}


def capture_1a(evidence_dir: Path, body: dict) -> dict:
    """Save an arbitrary prompt+answer exchange as a 1A-flagged evidence record.

    For the case where the exchange worth keeping is not a chain's final target:
    an intermediate stage, or a one-off prompt the operator wants in the findings.
    ``make collect`` picks it up like any other operator_1a record.
    """
    prompt = str(body.get("prompt", "")).strip()
    answer = str(body.get("answer", "")).strip()
    if not prompt or not answer:
        raise RunRefused("both a prompt and an answer are required")
    now = datetime.now(timezone.utc)
    run_id = "cap-" + now.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:4]
    rec = {
        "run_id": run_id, "attempt": 1, "status": "ok",
        "ts": now.isoformat(timespec="milliseconds"), "ts_done": now.isoformat(timespec="milliseconds"),
        "mode": "capture", "source": "console-capture",
        "claim": str(body.get("claim", ""))[:4000],
        "category": body.get("category") or "history", "lang": body.get("lang") or "fi",
        "target_model": str(body.get("model", ""))[:200], "served_model": str(body.get("model", ""))[:200],
        "endpoint": str(body.get("endpoint", ""))[:40], "stage_id": str(body.get("label", ""))[:60],
        "probe_text": prompt[:60000], "response_text": answer[:60000],
        "reasoning_text": str(body.get("reasoning", ""))[:60000], "response_source": "content",
        "operator_1a": True, "operator_verdict": "fail", "operator_score": SCORES["fail"],
        "operator_note": str(body.get("note", ""))[:2000],
        "operator_ts": now.isoformat(timespec="seconds"),
    }
    path = evidence_dir / run_id / "1.json"
    _atomic_write(path, json.dumps(rec, ensure_ascii=False, indent=2))
    return {"run_id": run_id, "evidence_ref": path.as_posix()}


def evidence_summary(path: Path) -> dict | None:
    """What the page shows from the verbatim record after the run."""
    if not path.is_file():
        return None
    rec = json.loads(path.read_text(encoding="utf-8"))
    keys = ("run_id", "status", "endpoint", "target_model", "served_model", "probe_text",
            "response_text", "response_source", "reasoning_text", "finish_reason", "tokens_in",
            "tokens_out", "cost_usd", "latency_ms", "verdict", "score", "signals", "stages",
            "attempt", "operator_verdict", "operator_note", "operator_1a")
    return {k: rec.get(k) for k in keys} | {"evidence_ref": path.as_posix()}


async def run_console(seed: Seed, mask_map: dict[str, str], opts: dict, *, root: Path,
                      unmask: Unmask | None, emit: Emit, env: dict | None = None) -> str:
    """One statement through one chain. Emits events; returns the verdict."""
    chain: Chain = load_chain(root / opts["chain"])
    if mask_map and unmask is None:
        raise RunRefused(f"swap pairs given but no swap body: put unmask(text, mask_map) "
                         f"in {UNMASK_PATH} (or set VIPUNEN_UNMASK)")

    swap_id = chain.stages[chain.swap_index].id
    swap_report: list[dict] = []  # sent just before the swap stage's own update, so in order

    def swap(text: str, mm: dict[str, str]) -> str:
        found = {ph: count_ci(text, ph) for ph in mm}
        out = unmask(text, mm) if mm else text
        swap_report.append({"pairs": [{"placeholder": ph, "real": real, "found": found[ph],
                                       "left": count_ci(out, ph), "real_after": count_ci(out, real)}
                                      for ph, real in mm.items()]})
        return out

    settings = load_settings(env, endpoint=opts["endpoint"], target_model=opts["model"])
    client = budget = None
    try:
        if opts["echo"]:
            transport, agents, ep_name = EchoTransport(), [EchoStage(o) for o in LLM_OWNERS], "echo"
        else:
            require_key(settings)
            budget = Budget.from_settings(settings)
            client = ApertusClient(settings.endpoint, timeout_s=settings.timeout_s)
            ep_name = settings.endpoint.name
            transport = LiveTransport(client, budget, model=settings.target_model,
                                      evidence_dir=root / settings.evidence_dir,
                                      max_tokens=settings.target_max_tokens,
                                      temperature=settings.target_temperature, seed=settings.seed)
            stage_kw = dict(model=settings.stage_model, max_tokens=settings.stage_max_tokens,
                            temperature=settings.stage_temperature, seed=settings.seed)
            agents = [Vainamoinen(client, budget, **stage_kw), Louhi(client, budget, **stage_kw)]

        emit("start", {"chain": opts["chain"], "stages": [{"id": s.id, "kind": s.kind, "owner": s.owner}
                                                          for s in chain.stages],
                       "endpoint": ep_name, "target_model": transport.model_id,
                       "stage_model": None if opts["echo"] else settings.stage_model,
                       "budget": budget.status() if budget else "offline (echo)"})

        def on_update(u: ConsoleUpdate) -> None:
            if u.kind == "stage" and u.text.startswith(f"[{swap_id}]") and swap_report:
                emit("swap", swap_report.pop(0))
            emit("update", {"kind": u.kind, "text": u.text, "run_id": u.run_id})

        batch_id = "console-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        results = await run_batch(chain, [seed], transport=transport, stage_agents=agents,
                                  target_model=transport.model_id,
                                  mask_maps={seed.id: mask_map} if mask_map else None,
                                  on_update=on_update,
                                  gate=budget.gate if budget else (lambda: None),
                                  unmask=swap, tags={"batch_id": batch_id, "endpoint": ep_name,
                                                     "source": "console"})
        verdict = results[0].verdict if results else "budget"
        record = None
        if results and not opts["echo"]:
            record = evidence_summary(transport.record_path(results[0].run_id, 1))
        emit("done", {"verdict": verdict, "record": record,
                      "budget": budget.status() if budget else "offline (echo)"})
        return verdict
    finally:
        if client:
            await client.aclose()


class ConsoleHandler(BaseHTTPRequestHandler):
    server: "ConsoleServer"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # quiet; the page is the log
        if self.server.verbose:
            super().log_message(fmt, *args)

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: dict) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/options":
            self._json(200, self.server.options())
        else:
            self._json(404, {"error": "not found"})

    STEP_PATHS = ("/api/step/start", "/api/step/next", "/api/step/cancel")

    def do_POST(self) -> None:
        if self.path not in ("/api/run", "/api/score", "/api/capture", *self.STEP_PATHS):
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._json(413, {"error": "request too large"})
            return
        if self.path in ("/api/score", "/api/capture"):
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                evidence = self.server.root / load_settings(self.server.env).evidence_dir
                fn = operator_score if self.path == "/api/score" else capture_1a
                self._json(200, fn(evidence, body))
            except (json.JSONDecodeError, RunRefused, SettingsError, AttributeError, TypeError) as e:
                self._json(400, {"error": str(e)})
            return
        if self.path in self.STEP_PATHS:
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError as e:
                self._json(400, {"error": str(e)})
                return
            status, data = self.server.handle_step(self.path, body)
            self._json(status, data)
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            seed, mask_map, opts = parse_request(body, chain_files(self.server.root))
        except (json.JSONDecodeError, RunRefused, AttributeError, TypeError) as e:
            self._json(400, {"error": str(e)})
            return
        if not self.server.busy.acquire(blocking=False):
            self._json(HTTPStatus.CONFLICT, {"error": "a run is already in progress"})
            return
        try:
            self._stream(seed, mask_map, opts)
        finally:
            self.server.busy.release()

    def _stream(self, seed: Seed, mask_map: dict[str, str], opts: dict) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        def emit(event: str, data: dict) -> None:
            chunk = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            try:
                self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()
            except OSError as e:
                raise ClientGone("page closed the stream") from e

        try:
            asyncio.run(run_console(seed, mask_map, opts, root=self.server.root,
                                    unmask=self.server.unmask(), emit=emit, env=self.server.env))
        except ClientGone:
            pass
        except RuntimeError as e:  # run_batch: an agent stopped (incl. the relay when the page left)
            if not isinstance(e.__cause__, ClientGone):
                self._try_emit(emit, f"{e}: {e.__cause__!r}")
        except (RunRefused, SettingsError, ChainError, OSError) as e:
            self._try_emit(emit, str(e))
        except Exception as e:  # show it on the page instead of a silently dead stream
            self._try_emit(emit, f"{type(e).__name__}: {e}")
            raise

    @staticmethod
    def _try_emit(emit: Emit, message: str) -> None:
        try:
            emit("fail", {"error": message})
        except ClientGone:
            pass


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr: tuple[str, int], *, root: Path = Path("."),
                 unmask_path: str | Path = UNMASK_PATH, unmask: Unmask | None = None,
                 env: dict | None = None, verbose: bool = False) -> None:
        super().__init__(addr, ConsoleHandler)
        self.root = Path(root)
        self.unmask_path = Path(unmask_path)
        self._unmask = unmask      # tests inject one; otherwise loaded per run, so edits apply
        self.env = env
        self.verbose = verbose
        self.busy = threading.Lock()
        self._step: step.StepSession | None = None  # one advanced-mode session, holds self.busy

    def handle_step(self, path: str, body: dict) -> tuple[int, dict]:
        """Advanced mode. start acquires self.busy and keeps it; next runs one stage;
        cancel (or the target stage finishing) releases it. One session at a time."""
        if path == "/api/step/start":
            if not self.busy.acquire(blocking=False):
                return HTTPStatus.CONFLICT, {"error": "a run is already in progress"}
            try:
                session = self._build_step(body)
            except (RunRefused, SettingsError, ChainError, OSError, ValueError) as e:
                self.busy.release()
                return 400, {"error": str(e)}
            self._step = session
            return 200, step.view(session)

        if self._step is None:
            return HTTPStatus.CONFLICT, {"error": "no advanced run in progress; start one first"}
        if path == "/api/step/cancel":
            self._end_step()
            return 200, {"cancelled": True}
        if body.get("session") != self._step.id:
            return HTTPStatus.CONFLICT, {"error": "stale session; the run was replaced or cancelled"}
        try:
            result = step.run_one(self._step, str(body.get("prompt", "")))
        except step.StepError as e:
            return 400, {"error": str(e)}  # nothing advanced; the operator can retry this stage
        except (SettingsError, OSError) as e:
            self._end_step()
            return 400, {"error": str(e)}
        if self._step.done:
            self._end_step()
        return 200, result

    def _build_step(self, body: dict) -> "step.StepSession":
        seed, mask_map, opts = parse_request(body, chain_files(self.root))
        chain = load_chain(self.root / opts["chain"])
        unmask = self.unmask()
        if mask_map and unmask is None:
            raise RunRefused(f"swap pairs given but no swap body: put unmask(text, mask_map) "
                             f"in {UNMASK_PATH} (or set VIPUNEN_UNMASK)")
        settings = load_settings(self.env, endpoint=opts["endpoint"], target_model=opts["model"])
        if not opts["echo"]:
            require_key(settings)
        sid = uuid.uuid4().hex
        return step.new_session(sid, chain, seed, mask_map, settings, unmask=unmask,
                                echo=opts["echo"], root=self.root,
                                endpoint_name=settings.endpoint.name,
                                tags={"source": "console-step"})

    def _end_step(self) -> None:
        self._step = None
        try:
            self.busy.release()
        except RuntimeError:
            pass

    def unmask(self) -> Unmask | None:
        if self._unmask is not None:
            return self._unmask
        p = self.unmask_path if self.unmask_path.is_absolute() else self.root / self.unmask_path
        return load_unmask(p)

    def options(self) -> dict:
        try:
            settings = load_settings(self.env)
            default_model, budget = settings.target_model, Budget.from_settings(settings).status()
            has_key = bool(settings.endpoint.api_key)
        except (SettingsError, OSError, ValueError) as e:
            default_model, budget, has_key = "", f"unavailable: {e}", False
        return {"chains": chain_files(self.root), "models": list(MODELS),
                "default_model": default_model, "endpoints": list(ENDPOINTS),
                "langs": list(LANGS), "categories": list(CATEGORIES),
                "seeds": seed_presets(self.root), "budget": budget, "has_key": has_key,
                "swap_body": self.unmask() is not None, "swap_path": self.unmask_path.as_posix()}


def serve(host: str = "127.0.0.1", port: int = 8000, root: Path = Path(".")) -> None:
    server = ConsoleServer((host, port), root=root)
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    print(f"Vipunen console on http://{shown}:{port}/  (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

