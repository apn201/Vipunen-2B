"""Transport: send the final probe to the model under test and capture the reply.

S1 ships the interface and an offline echo transport. S2 adds the live transport:
verbatim record + probe hash written at call time, rate limit, retries,
idempotent by (run_id, attempt).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Reply:
    text: str
    reasoning: str = ""
    model_id: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0


class Transport(Protocol):
    async def send(self, probe: str, *, run_id: str, attempt: int, seed_id: str) -> Reply: ...


class EchoTransport:
    """Offline stand-in for the target: replies with the probe. No network."""

    model_id = "echo"

    async def send(self, probe: str, *, run_id: str, attempt: int, seed_id: str) -> Reply:
        return Reply(text=probe, model_id=self.model_id)
