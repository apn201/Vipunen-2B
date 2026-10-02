"""Advanced mode: run a chain one stage at a time, letting the operator edit the
prompt that is about to be sent before each step.

The automatic runner (``pipeline.run_batch``) streams a whole chain over the agent
bus. Step mode instead keeps a tiny amount of state between HTTP calls and runs
exactly one stage per call, so the operator sees - and can rewrite - the prompt
going to Apertus at every stage:

  start        -> show the first stage's prompt (nothing sent yet)
  step(prompt) -> run that stage with the operator's prompt, show the next one
  ... until the target stage answers; then the operator scores it as usual.

The mask invariant still holds: a prompt for an ``llm`` stage is refused if it
contains a real term from the mask map, exactly as in automatic mode. The swap
stage is non-LLM and shown read-only. Target prompts may contain real terms by
design - that is the point of the swap - so they are not mask-checked.

One step session at a time (the server's busy lock). Each call makes its own
Apertus client: interactive stepping is one call per click, so a per-call client
keeps every request on its own event loop without a long-lived connection pool.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vipunen.agents import joukahainen
from vipunen.agents.vainamoinen import MAX_RETRIES, MIN_CHARS, is_refusal, strip_preamble
from vipunen.budget import Budget, BudgetExceeded, estimate_tokens
from vipunen.chain import Chain, render
from vipunen.client import ApertusClient, ApertusError
from vipunen.config import Settings
from vipunen.judge import score
from vipunen.seeds import Seed, sha256_text
from vipunen.transport import EchoTransport, LiveTransport


class StepError(RuntimeError):
    """The step cannot run as asked (mask leak, budget, API). Nothing advanced."""


@dataclass
class StepSession:
    """Plain state carried between HTTP calls for one stepped run."""
    id: str
    run_id: str
    chain: Chain
    seed: Seed
    mask_map: dict[str, str]
    settings: Settings
    unmask: Any                       # callable(text, mask_map) -> text
    echo: bool
    root: Path
    endpoint_name: str
    text: str = ""                    # the running (masked until swap) text
    index: int = 0                    # stage about to run
    trail: list[dict] = field(default_factory=list)
    tags: dict = field(default_factory=dict)
    done: bool = False

    @property
    def stage(self):
        return self.chain.stages[self.index] if self.index < len(self.chain.stages) else None


def default_prompt(session: StepSession) -> str:
    """The prompt the automatic pipeline sends for the current stage. It must match
    what auto mode sends, or advanced mode would not reproduce an auto result. Auto
    keeps insist_word_form off (the keep-the-word instruction suppresses the carrier),
    so step does not add it either; the operator can still type it in by hand."""
    stage = session.stage
    if stage is None or stage.kind == "swap":
        return ""  # swap is non-LLM, shown read-only
    return render(stage.template, session.text)


def view(session: StepSession) -> dict:
    """What the page shows before the operator runs the current stage."""
    stage = session.stage
    if stage is None:
        return {"done": True}
    base = {"session": session.id, "done": False,
            "stage": {"id": stage.id, "kind": stage.kind, "owner": stage.owner,
                      "index": session.index, "total": len(session.chain.stages)}}
    if stage.kind == "swap":
        pairs = [{"placeholder": ph, "real": real, "found": _count(session.text, ph)}
                 for ph, real in session.mask_map.items()]
        return base | {"editable": False, "swap_preview": pairs,
                       "note": "Non-LLM substitution. Placeholders are replaced with their real "
                               "terms. The next prompt (to the target) is then editable."}
    base["editable"] = True
    base["prompt"] = default_prompt(session)
    if stage.kind == "llm":
        base["note"] = ("This prompt goes to Apertus as the carrier stage. It must not contain a "
                        "real term from the swap map; the real term is put in later, at the swap stage.")
    else:  # target
        base["note"] = "This is the final prompt to the model under test. It may contain real terms."
    return base


async def _chat(session: StepSession, prompt: str, owner: str | None = None) -> dict:
    """One carrier-stage call. For the verse stage (Vainamoinen) it matches auto mode:
    resample with a fresh seed while the output is a refusal or shorter than the verse
    minimum, up to MAX_RETRIES, and strip a prose preamble. Otherwise a single call.
    Without this, a verse that happens to refuse at the base seed is carried forward and
    the whole chain refuses, while auto mode would have retried past it."""
    s = session.settings
    is_verse = owner == "vainamoinen"
    tries_max = MAX_RETRIES if is_verse else 0
    client = ApertusClient(s.endpoint, timeout_s=s.timeout_s)
    budget = Budget.from_settings(s)
    tokens_in = tokens_out = 0
    try:
        for tries in range(tries_max + 1):
            seed = None if s.seed is None else s.seed + tries
            budget.authorize(s.stage_model, estimate_tokens(prompt), s.stage_max_tokens)
            r = await client.chat([{"role": "user", "content": prompt}], model=s.stage_model,
                                  max_tokens=s.stage_max_tokens, temperature=s.stage_temperature,
                                  seed=seed)
            budget.record(s.stage_model, r.usage)
            tokens_in += r.tokens_in
            tokens_out += r.tokens_out
            text = strip_preamble(r.text) if is_verse else r.text
            if not is_verse or (len(text) >= MIN_CHARS and not is_refusal(text)):
                break
    finally:
        await client.aclose()
    return {"output_text": text, "tokens_in": tokens_in, "tokens_out": tokens_out,
            "source": r.source, "budget": budget.status(), "retries": tries}


async def _deliver(session: StepSession, probe: str) -> dict:
    """The target stage: send the final probe, capture evidence, score it."""
    s = session.settings
    if session.echo:
        transport = EchoTransport()
        budget = None
    else:
        client = ApertusClient(s.endpoint, timeout_s=s.timeout_s)
        budget = Budget.from_settings(s)
        transport = LiveTransport(client, budget, model=s.target_model,
                                  evidence_dir=session.root / s.evidence_dir,
                                  max_tokens=s.target_max_tokens, temperature=s.target_temperature,
                                  seed=s.seed)
    context = {**session.tags, "chain_name": session.chain.name, "target_model": s.target_model,
               "claim": session.seed.claim, "claim_hash": session.seed.claim_hash,
               "claim_is_true": session.seed.claim_is_true, "lang": session.seed.lang,
               "category": session.seed.category, "mode": "step",
               "mask_map_ref": sha256_text(repr(sorted(session.mask_map))) if session.mask_map else "",
               "stages": session.trail}
    try:
        reply = await transport.send(probe, run_id=session.run_id, attempt=1,
                                     seed_id=session.seed.id, context=context)
    finally:
        if not session.echo:
            await transport.client.aclose()
    verdict = score(session.seed, reply.text, reply.source)
    record_ref = ""
    if not session.echo:
        path = transport.record_path(session.run_id, 1)
        if path.is_file():  # annotate with the deterministic verdict, like Lemminkainen does
            import json
            from vipunen.budget import _atomic_write
            rec = json.loads(path.read_text(encoding="utf-8"))
            rec.update(verdict=verdict.label, score=verdict.score, signals=verdict.signals)
            _atomic_write(path, json.dumps(rec, ensure_ascii=False, indent=2))
            record_ref = path.as_posix()
    return {"probe": probe, "answer": reply.text, "model_id": reply.model_id,
            "verdict": verdict.label, "score": verdict.score, "signals": verdict.signals,
            "source": reply.source, "evidence_ref": record_ref, "run_id": session.run_id,
            "tokens_in": reply.tokens_in, "tokens_out": reply.tokens_out,
            "cost": reply.cost, "budget": budget.status() if budget else "offline (echo)"}


def run_one(session: StepSession, prompt: str) -> dict:
    """Run the current stage with the operator's prompt; advance. Returns the result."""
    stage = session.stage
    if stage is None or session.done:
        raise StepError("the run is already finished")

    if stage.kind == "llm":
        leaked = joukahainen.leaked_terms(prompt, session.mask_map)
        if leaked:
            raise StepError(f"the prompt still contains {len(leaked)} real term(s) from the swap "
                            "map; a carrier stage must not see them. Edit it or use the safe word.")
        try:
            out = asyncio.run(_echo_stage(prompt)) if session.echo else asyncio.run(_chat(session, prompt, stage.owner))
        except (ApertusError, BudgetExceeded) as e:
            raise StepError(f"{type(e).__name__}: {e}") from e
        session.text = out["output_text"]
        session.trail.append({"stage_id": stage.id, "owner": stage.owner, "prompt": prompt,
                              "output_text": session.text, "tokens_in": out.get("tokens_in", 0),
                              "tokens_out": out.get("tokens_out", 0), "edited": True})
        session.index += 1
        return {"kind": "llm", "stage_id": stage.id, "output": session.text,
                "budget": out.get("budget", ""), "next": view(session)}

    if stage.kind == "swap":
        before = session.text
        session.text = session.unmask(session.text, session.mask_map) if session.mask_map else session.text
        session.trail.append({"stage_id": stage.id, "kind": "swap", "output_text": session.text})
        session.index += 1
        pairs = [{"placeholder": ph, "real": real,
                  "left": _count(session.text, ph), "real_in": _count(session.text, real)}
                 for ph, real in session.mask_map.items()]
        return {"kind": "swap", "stage_id": stage.id, "before": before, "output": session.text,
                "pairs": pairs, "next": view(session)}

    # target
    try:
        out = asyncio.run(_deliver(session, prompt))
    except (ApertusError, BudgetExceeded) as e:
        raise StepError(f"{type(e).__name__}: {e}") from e
    session.index += 1
    session.done = True
    return {"kind": "target", "stage_id": stage.id, **out, "next": {"done": True}}


async def _echo_stage(prompt: str) -> dict:
    return {"output_text": prompt, "tokens_in": 0, "tokens_out": 0, "source": "content",
            "budget": "offline (echo)"}


def _count(text: str, term: str) -> int:
    return text.count(term) if term else 0


def new_session(sid: str, chain: Chain, seed: Seed, mask_map: dict[str, str], settings: Settings,
                *, unmask, echo: bool, root: Path, endpoint_name: str, tags: dict | None = None,
                ) -> StepSession:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + sid[:6]
    session = StepSession(id=sid, run_id=run_id, chain=chain, seed=seed, mask_map=mask_map,
                          settings=settings, unmask=unmask, echo=echo, root=Path(root),
                          endpoint_name=endpoint_name, tags=tags or {})
    session.text = joukahainen.mask(seed.claim, mask_map)
    return session
