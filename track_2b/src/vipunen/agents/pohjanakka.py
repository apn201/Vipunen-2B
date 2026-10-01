"""Pohjanakka - scheduler and budget governor (not LLM).

Emits one ``RunRequest`` at a time and waits for its ``RunComplete`` before the
next, so pacing and the budget gate sit in one place. S2 adds the real budget
(``budget.py``): spend ceiling, token count, rate limit; it fails closed.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable

from vipunen.bus import Bus, BudgetExhausted, RunComplete, RunRequest
from vipunen.seeds import Seed


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


class Pohjanakka:
    def __init__(self, seeds: Iterable[Seed], target_model: str, max_tries: int,
                 gate: Callable[[], str | None] = lambda: None) -> None:
        """``gate()`` returns None to allow the next run, or a reason string to stop."""
        self.seeds = list(seeds)
        self.target_model = target_model
        self.max_tries = max_tries
        self.gate = gate
        self.completed: list[RunComplete] = []

    async def run(self, bus: Bus) -> list[RunComplete]:
        for seed in self.seeds:
            reason = self.gate()
            if reason:
                await bus.put(BudgetExhausted(reason=reason))
                break
            await bus.put(RunRequest(seed_id=seed.id, claim_hash=seed.claim_hash,
                                     category=seed.category, lang=seed.lang,
                                     target_model=self.target_model,
                                     max_tries=self.max_tries, run_id=new_run_id()))
            self.completed.append(await bus.get(RunComplete))
        return self.completed
