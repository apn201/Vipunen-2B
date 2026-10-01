"""Base classes for llm-stage agents (Vainamoinen, Louhi).

A stage agent reads ``StageTask`` on its own channel, composes the stage output
from masked input, and answers Ukko with a ``StageResult``.
"""
from __future__ import annotations

from vipunen.agents import joukahainen
from vipunen.budget import Budget, BudgetExceeded, estimate_tokens
from vipunen.bus import Bus, StageResult, StageTask
from vipunen.chain import render
from vipunen.client import ApertusClient, ApertusError, ChatResult

UKKO = "ukko"


def stage_prompt(task: StageTask) -> str:
    """The rendered template, plus the keep-the-placeholder instruction when there is one."""
    return render(task.template, task.input_text) + joukahainen.keep_instruction(task.keep)


def retry_seed(seed: int | None, task: StageTask, tries: int = 0) -> int | None:
    """A fresh sample for each placeholder retry, else the retry repeats the same text."""
    return None if seed is None else seed + 100 * task.retry + tries


class StageAgent:
    owner: str = ""

    async def compose(self, task: StageTask) -> StageResult:
        raise NotImplementedError

    async def run(self, bus: Bus) -> None:
        while True:
            task = await bus.get(StageTask, channel=self.owner)
            try:
                result = await self.compose(task)
            except (BudgetExceeded, ApertusError) as e:  # expected failures go back to Ukko
                result = StageResult(seed_id=task.seed_id, run_id=task.run_id, attempt=task.attempt,
                                     stage_id=task.stage_id, output_text="",
                                     error=f"{type(e).__name__}: {e}")
            await bus.put(result, channel=UKKO)


class EchoStage(StageAgent):
    """Offline stand-in for an llm stage: returns the rendered prompt itself."""

    def __init__(self, owner: str) -> None:
        self.owner = owner

    async def compose(self, task: StageTask) -> StageResult:
        return StageResult(seed_id=task.seed_id, run_id=task.run_id, attempt=task.attempt,
                           stage_id=task.stage_id,
                           output_text=render(task.template, task.input_text))


class ApertusStage(StageAgent):
    """An llm stage on Apertus: the rendered template is the whole user message.

    Sees only masked text - Ukko checks that before the task is sent.
    """

    def __init__(self, client: ApertusClient, budget: Budget, *, model: str,
                 max_tokens: int, temperature: float, seed: int | None) -> None:
        self.client = client
        self.budget = budget
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.seed = seed

    async def call(self, prompt: str, seed: int | None) -> ChatResult:
        self.budget.authorize(self.model, estimate_tokens(prompt), self.max_tokens)
        result = await self.client.chat([{"role": "user", "content": prompt}], model=self.model,
                                        max_tokens=self.max_tokens, temperature=self.temperature,
                                        seed=seed)
        self.budget.record(self.model, result.usage)
        return result

    async def compose(self, task: StageTask) -> StageResult:
        result = await self.call(stage_prompt(task), retry_seed(self.seed, task))
        return StageResult(seed_id=task.seed_id, run_id=task.run_id, attempt=task.attempt,
                           stage_id=task.stage_id, output_text=result.text,
                           tokens_in=result.tokens_in, tokens_out=result.tokens_out)
