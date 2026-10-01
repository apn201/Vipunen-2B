"""Vainamoinen - verse stage (Apertus). Masked claim -> 500+ char Kalevala-metre verse.

Pure literary prompt over masked text: it writes about rabbits. On top of the
plain Apertus call it checks the verse is long enough to be a carrier and retries
(with a different seed, so the retry is not the same sample) when it is short or
refused. A refusal or a short verse after all retries is passed on, not hidden -
the trail records ``verse_chars``, ``verse_retries``, ``verse_ok``. Metre is scored
separately by Ilmarinen (S4) and never gates.
"""
from __future__ import annotations

from vipunen.agents.base import ApertusStage
from vipunen.bus import StageResult, StageTask
from vipunen.chain import render
from vipunen.judge import REFUSAL

MIN_CHARS = 500
MAX_RETRIES = 2


def strip_preamble(text: str) -> str:
    """Drop a prose lead-in that ends with a colon - 'Tässä runo:', or a few sentences
    about Kalevala metre ending in '...seuraavasti:' - when the verse follows it."""
    text = text.strip()
    lines = text.splitlines()
    for i, line in enumerate(lines[:4]):
        head = "\n".join(lines[:i + 1])
        rest = "\n".join(lines[i + 1:]).strip()
        if line.rstrip().endswith(":") and len(head) < 600 and rest:
            return rest
    return text


def is_refusal(text: str) -> bool:
    head = text[:200]
    return bool(REFUSAL["fi"].search(head) or REFUSAL["en"].search(head))


class Vainamoinen(ApertusStage):
    owner = "vainamoinen"
    min_chars = MIN_CHARS
    max_retries = MAX_RETRIES

    async def compose(self, task: StageTask) -> StageResult:
        prompt = render(task.template, task.input_text)
        tokens_in = tokens_out = 0
        for tries in range(self.max_retries + 1):
            seed = None if self.seed is None else self.seed + tries
            result = await self.call(prompt, seed)
            tokens_in += result.tokens_in
            tokens_out += result.tokens_out
            verse = strip_preamble(result.text)
            ok = len(verse) >= self.min_chars and not is_refusal(verse)
            if ok:
                break
        meta = {"verse_chars": len(verse), "verse_retries": tries, "verse_ok": ok,
                "verse_refused": is_refusal(verse)}
        return StageResult(seed_id=task.seed_id, run_id=task.run_id, attempt=task.attempt,
                           stage_id=task.stage_id, output_text=verse,
                           tokens_in=tokens_in, tokens_out=tokens_out, meta=meta)
