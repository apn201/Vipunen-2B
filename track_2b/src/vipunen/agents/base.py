"""Base class for llm-stage agents (Vainamoinen, Louhi).

A stage agent reads ``StageTask`` on its own channel, composes the stage output
from masked input, and answers Ukko with a ``StageResult``.
"""
from __future__ import annotations

from vipunen.bus import Bus, StageResult, StageTask
from vipunen.chain import render

UKKO = "ukko"


class StageAgent:
    owner: str = ""

    async def compose(self, task: StageTask) -> StageResult:
        raise NotImplementedError

    async def run(self, bus: Bus) -> None:
        while True:
            task = await bus.get(StageTask, channel=self.owner)
            await bus.put(await self.compose(task), channel=UKKO)


class EchoStage(StageAgent):
    """Offline stand-in for an llm stage: returns the rendered prompt itself."""

    def __init__(self, owner: str) -> None:
        self.owner = owner

    async def compose(self, task: StageTask) -> StageResult:
        return StageResult(seed_id=task.seed_id, run_id=task.run_id, attempt=task.attempt,
                           stage_id=task.stage_id,
                           output_text=render(task.template, task.input_text))
