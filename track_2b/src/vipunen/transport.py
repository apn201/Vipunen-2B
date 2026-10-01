"""Transport: send the final probe to the model under test and capture the reply.

The verbatim record for an attempt is written at the earliest capture point:
BEFORE the request goes out (status ``sent``), then completed with the response
(status ``ok``) or the error (status ``error``). A crash mid-call still leaves the
probe on disk. Records live in ``private/evidence/{run_id}/{attempt}.json`` -
gitignored, dockerignored, never in the public repo.

Idempotent by (run_id, attempt): a completed record is returned as-is, no new call.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from vipunen.budget import Budget, _atomic_write, estimate_tokens
from vipunen.client import ApertusClient
from vipunen.seeds import sha256_text


@dataclass(frozen=True)
class Reply:
    text: str
    reasoning: str = ""
    model_id: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cost: float = 0.0
    source: str = "content"
    finish_reason: str = ""
    evidence_ref: str = ""


class Transport(Protocol):
    async def send(self, probe: str, *, run_id: str, attempt: int, seed_id: str,
                   context: dict[str, Any] | None = None) -> Reply: ...


class EchoTransport:
    """Offline stand-in for the target: replies with the probe. No network."""

    model_id = "echo"

    async def send(self, probe: str, *, run_id: str, attempt: int, seed_id: str,
                   context: dict[str, Any] | None = None) -> Reply:
        return Reply(text=probe, model_id=self.model_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class LiveTransport:
    def __init__(self, client: ApertusClient, budget: Budget, *, model: str, evidence_dir: Path,
                 max_tokens: int, temperature: float, seed: int | None) -> None:
        self.client = client
        self.budget = budget
        self.model_id = model
        self.evidence_dir = Path(evidence_dir)
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.seed = seed

    def record_path(self, run_id: str, attempt: int) -> Path:
        return self.evidence_dir / run_id / f"{attempt}.json"

    async def send(self, probe: str, *, run_id: str, attempt: int, seed_id: str,
                   context: dict[str, Any] | None = None) -> Reply:
        path = self.record_path(run_id, attempt)
        if path.exists():
            done = json.loads(path.read_text(encoding="utf-8"))
            if done.get("status") == "ok":
                return self._reply(done, path)

        messages = [{"role": "user", "content": probe}]
        params = {"model": self.model_id, "endpoint": self.client.endpoint.name,
                  "max_tokens": self.max_tokens, "temperature": self.temperature, "seed": self.seed}
        rec: dict[str, Any] = {
            "run_id": run_id, "attempt": attempt, "seed_id": seed_id, "ts": _now(),
            "status": "sent", **(context or {}),
            "model_id": self.model_id, "request_params": params, "messages": messages,
            "probe_text": probe, "probe_hash": sha256_text(probe),
        }
        self.budget.authorize(self.model_id, estimate_tokens(probe), self.max_tokens)
        _atomic_write(path, json.dumps(rec, ensure_ascii=False, indent=2))

        try:
            result = await self.client.chat(messages, model=self.model_id, max_tokens=self.max_tokens,
                                            temperature=self.temperature, seed=self.seed)
        except Exception as e:
            rec.update(status="error", error=str(e), ts_done=_now())
            _atomic_write(path, json.dumps(rec, ensure_ascii=False, indent=2))
            raise

        cost = self.budget.record(self.model_id, result.usage)
        rec.update(
            status="ok", ts_done=_now(), served_model=result.model,
            response_text=result.text, response_hash=sha256_text(result.text),
            response_source=result.source, content=result.content, reasoning_text=result.reasoning,
            finish_reason=result.finish_reason, usage=result.usage,
            tokens_in=result.tokens_in, tokens_out=result.tokens_out,
            cost_usd=round(cost, 8), latency_ms=result.latency_ms,
        )
        _atomic_write(path, json.dumps(rec, ensure_ascii=False, indent=2))
        return self._reply(rec, path)

    @staticmethod
    def _reply(rec: dict[str, Any], path: Path) -> Reply:
        return Reply(text=rec["response_text"], reasoning=rec.get("reasoning_text", ""),
                     model_id=rec.get("served_model") or rec["model_id"],
                     tokens_in=rec.get("tokens_in", 0), tokens_out=rec.get("tokens_out", 0),
                     latency_ms=rec.get("latency_ms", 0), cost=rec.get("cost_usd", 0.0),
                     source=rec.get("response_source", "content"),
                     finish_reason=rec.get("finish_reason", ""), evidence_ref=path.as_posix())
