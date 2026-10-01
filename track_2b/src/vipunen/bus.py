"""Typed message bus: frozen dataclasses over asyncio queues.

All inter-agent traffic goes through the bus; Joukahainen is the one direct-call
exception. Each (message type, channel) pair has its own queue, so a consumer
asks for exactly what it handles - e.g. Louhi reads ``StageTask`` on channel
``"louhi"``.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TypeVar


@dataclass(frozen=True)
class RunRequest:            # Pohjanakka -> Ukko
    seed_id: str
    claim_hash: str
    category: str
    lang: str
    target_model: str
    max_tries: int
    run_id: str


@dataclass(frozen=True)
class StageTask:             # Ukko -> an llm-stage agent; input already masked
    seed_id: str
    run_id: str
    attempt: int
    stage_id: str
    template: str
    input_text: str
    lang: str


@dataclass(frozen=True)
class StageResult:           # llm-stage agent -> Ukko
    seed_id: str
    run_id: str
    attempt: int
    stage_id: str
    output_text: str
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass(frozen=True)
class ResponseResult:        # Lemminkainen -> Ukko
    seed_id: str
    run_id: str
    attempt: int
    response_hash: str
    evidence_ref: str
    verdict: str
    score: float
    count: int
    delta_signal: dict = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    latency_ms: int = 0


@dataclass(frozen=True)
class RunComplete:           # Ukko -> Pohjanakka, so it can pace the next run
    seed_id: str
    run_id: str
    attempts: int
    verdict: str


@dataclass(frozen=True)
class ConsoleUpdate:         # anyone -> console / CLI trace
    seed_id: str
    run_id: str
    kind: str                # 'stage' | 'raw_exchange' | 'verdict' | 'tuning_note' | 'error'
    text: str


@dataclass(frozen=True)
class BudgetExhausted:       # Pohjanakka -> everyone
    reason: str


Message = (RunRequest | StageTask | StageResult | ResponseResult | RunComplete
           | ConsoleUpdate | BudgetExhausted)
MESSAGE_TYPES = Message.__args__

M = TypeVar("M")


class Bus:
    def __init__(self) -> None:
        self._queues: dict[tuple[type, str | None], asyncio.Queue] = defaultdict(asyncio.Queue)

    def _queue(self, kind: type, channel: str | None) -> asyncio.Queue:
        return self._queues[(kind, channel)]

    async def put(self, msg: Message, channel: str | None = None) -> None:
        if not isinstance(msg, MESSAGE_TYPES):
            raise TypeError(f"not a bus message: {type(msg).__name__}")
        await self._queue(type(msg), channel).put(msg)

    async def get(self, kind: type[M], channel: str | None = None) -> M:
        return await self._queue(kind, channel).get()

    def drain(self, kind: type[M], channel: str | None = None) -> list[M]:
        """Take everything currently queued, without waiting."""
        q = self._queue(kind, channel)
        out = []
        while not q.empty():
            out.append(q.get_nowait())
        return out
