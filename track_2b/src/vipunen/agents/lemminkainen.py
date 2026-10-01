"""Lemminkainen - verdict (deterministic). Reads the response, applies the category's
scorer, annotates the verbatim record, answers Ukko with a ``ResponseResult``.

No model call in the verdict. S5 adds the hash-only ledger row, the optional
Apertus severity opinion and the end-of-loop tuning note.
"""
from __future__ import annotations

import json
from pathlib import Path

from vipunen import judge
from vipunen.agents.base import UKKO
from vipunen.budget import _atomic_write
from vipunen.bus import Bus, ResponseResult, ScoreTask
from vipunen.seeds import Seed

CHANNEL = "lemminkainen"


class Lemminkainen:
    owner = CHANNEL

    def __init__(self, seeds: dict[str, Seed]) -> None:
        self.seeds = seeds

    def verdict(self, task: ScoreTask) -> judge.Verdict:
        v = judge.score(self.seeds[task.seed_id], task.response_text, task.response_source)
        if task.evidence_ref and Path(task.evidence_ref).is_file():
            path = Path(task.evidence_ref)
            rec = json.loads(path.read_text(encoding="utf-8"))
            rec.update(verdict=v.label, score=v.score, signals=v.signals)
            _atomic_write(path, json.dumps(rec, ensure_ascii=False, indent=2))
        return v

    async def run(self, bus: Bus) -> None:
        while True:
            task = await bus.get(ScoreTask, channel=CHANNEL)
            v = self.verdict(task)
            signals = dict(v.signals)
            if task.metre_score is not None:
                signals["metre_score"] = task.metre_score
            await bus.put(ResponseResult(
                seed_id=task.seed_id, run_id=task.run_id, attempt=task.attempt,
                response_hash=task.response_hash, evidence_ref=task.evidence_ref,
                verdict=v.label, score=v.score, count=0, delta_signal=signals,
                tokens_in=task.tokens_in, tokens_out=task.tokens_out, cost=task.cost,
                latency_ms=task.latency_ms), channel=UKKO)
