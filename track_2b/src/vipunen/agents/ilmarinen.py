"""Ilmarinen - metre scorer (not LLM; optional Apertus second opinion). Records, never gates.

Reads ``MetreTask`` on its own channel: the composed verse, still masked (placeholders
keep the syllable count the poet wrote; swap-back may break it, and that is fine).
Scores it with ``metre.score_verse`` and answers Ukko with a ``MetreResult``. Ukko
writes the score into the evidence record and ``delta_signal``; nothing branches on it.

The optional Apertus plausibility cross-check is not wired yet.
"""
from __future__ import annotations

from vipunen.agents.base import UKKO
from vipunen.bus import Bus, MetreResult, MetreTask
from vipunen.metre import score_verse

CHANNEL = "ilmarinen"
WORST_LINES = 3


class Ilmarinen:
    owner = CHANNEL

    def score(self, task: MetreTask) -> MetreResult:
        m = score_verse(task.verse_text)
        worst = sorted(m.lines, key=lambda l: l.score)[:WORST_LINES]
        return MetreResult(seed_id=task.seed_id, run_id=task.run_id, attempt=task.attempt,
                           metre_score=round(m.score, 3), summary=m.summary(),
                           worst_lines=[(l.text, l.score, l.violations) for l in worst])

    async def run(self, bus: Bus) -> None:
        while True:
            task = await bus.get(MetreTask, channel=CHANNEL)
            await bus.put(self.score(task), channel=UKKO)
