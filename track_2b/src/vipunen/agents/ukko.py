"""Ukko - orchestrator (not LLM). Masks the claim, runs the chain stage by stage.

Before every llm stage Ukko checks that no real term from the mask map is in the
text the stage would see - a second guard behind the chain-order check, for the
case where the operator's mask misses a term or a template carries one.

Each llm stage is told to write the placeholders it is given unchanged
(``joukahainen.keep_instruction``). If it still drops one (or inflects it: "jäniksen"), the
swap would miss it, so Ukko asks that stage again with a fresh sample, up to
``KEEP_RETRIES`` times, then goes on with the last output. The trail records
``placeholder_retries`` and ``placeholder_lost``.

Before delivery Ilmarinen scores the metre of the composed verse (the last llm
stage's output, still masked). The score is recorded, never a gate. After the
target answers, Lemminkainen scores the response and Ukko reports the verdict. S4 adds the per-seed loop (one variable per attempt).
"""
from __future__ import annotations

import json
from dataclasses import asdict

from vipunen.agents import joukahainen
from vipunen.agents.base import UKKO
from vipunen.agents.ilmarinen import CHANNEL as ILMARINEN
from vipunen.agents.lemminkainen import CHANNEL as LEMMINKAINEN
from vipunen.budget import BudgetExceeded
from vipunen.bus import (Bus, ConsoleUpdate, MetreResult, MetreTask, ResponseResult, RunComplete,
                         RunRequest, ScoreTask, StageResult, StageTask)
from vipunen.chain import Chain, render, validate
from vipunen.client import ApertusError
from vipunen.seeds import Seed, sha256_text
from vipunen.transport import Transport


KEEP_RETRIES = 2


class MaskLeak(RuntimeError):
    """A real term would reach an llm stage. The run is refused."""


class StageFailed(RuntimeError):
    """An llm stage reported an expected failure (budget, API)."""


class Ukko:
    def __init__(self, chain: Chain, seeds: dict[str, Seed], transport: Transport,
                 mask_maps: dict[str, dict[str, str]] | None = None,
                 unmask=joukahainen.unmask, tags: dict | None = None) -> None:
        validate(chain)  # already enforced at construction; cheap to restate here
        self.chain = chain
        self.seeds = seeds
        self.transport = transport
        self.mask_maps = mask_maps or {}
        self.unmask = unmask
        self.tags = tags or {}  # e.g. batch_id; copied into every evidence record

    async def run(self, bus: Bus) -> None:
        while True:
            req = await bus.get(RunRequest)
            try:
                verdict = await self.attempt(bus, req, attempt=1)
            except MaskLeak as e:
                await self._say(bus, req, "error", f"refused: {e}")
                verdict = "mask-leak"
            except BudgetExceeded as e:
                await self._say(bus, req, "error", f"budget: {e}")
                verdict = "budget"
            except (StageFailed, ApertusError) as e:
                await self._say(bus, req, "error", str(e))
                verdict = "error"
            await bus.put(RunComplete(seed_id=req.seed_id, run_id=req.run_id,
                                      attempts=1, verdict=verdict))

    async def attempt(self, bus: Bus, req: RunRequest, attempt: int) -> str:
        seed = self.seeds[req.seed_id]
        mask_map = self.mask_maps.get(seed.id, {})
        text = joukahainen.mask(seed.claim, mask_map)
        trail: list[dict] = []  # what each stage produced, for the evidence record
        verse = ""  # the composed verse: last llm stage output, before swap-back

        for stage in self.chain.stages:
            if stage.kind == "llm":
                leaked = joukahainen.leaked_terms(render(stage.template, text), mask_map)
                if leaked:
                    raise MaskLeak(f"stage {stage.id!r} would see {len(leaked)} unmasked term(s)")
                given = joukahainen.placeholders_in(text, mask_map)
                tokens_in = tokens_out = 0
                for retry in range(KEEP_RETRIES + 1):
                    await bus.put(StageTask(seed_id=seed.id, run_id=req.run_id, attempt=attempt,
                                            stage_id=stage.id, template=stage.template,
                                            input_text=text, lang=req.lang, retry=retry,
                                            keep=tuple(given)),
                                  channel=stage.owner)
                    result = await bus.get(StageResult, channel=UKKO)
                    if result.error:
                        exc = BudgetExceeded if result.error.startswith("BudgetExceeded") else StageFailed
                        raise exc(f"stage {stage.id!r}: {result.error}")
                    tokens_in += result.tokens_in
                    tokens_out += result.tokens_out
                    kept = joukahainen.placeholders_in(result.output_text, mask_map)
                    lost = [ph for ph in given if ph not in kept]
                    if not lost or retry == KEEP_RETRIES:
                        break
                    await self._say(bus, req, "note", f"[{stage.id}] dropped {', '.join(lost)}; "
                                                      f"asking again ({retry + 1}/{KEEP_RETRIES})")
                text = verse = result.output_text
                trail.append({"stage_id": stage.id, "owner": stage.owner, "output_text": text,
                              "tokens_in": tokens_in, "tokens_out": tokens_out, **result.meta,
                              **({"placeholder_retries": retry, "placeholder_lost": lost}
                                 if given else {})})
            elif stage.kind == "swap":
                text = self.unmask(text, mask_map)
                trail.append({"stage_id": stage.id, "kind": "swap"})
            else:  # target - always last
                metre = await self._metre(bus, req, attempt, verse) if verse else None
                return await self._deliver(bus, req, seed, attempt, render(stage.template, text),
                                           mask_map, trail, metre)
            await self._say(bus, req, "stage", f"[{stage.id}] {text}")
        raise AssertionError("unreachable: validated chain ends with a target stage")

    async def _metre(self, bus: Bus, req: RunRequest, attempt: int, verse: str) -> MetreResult:
        await bus.put(MetreTask(seed_id=req.seed_id, run_id=req.run_id, attempt=attempt,
                                verse_text=verse, lang=req.lang), channel=ILMARINEN)
        metre = await bus.get(MetreResult, channel=UKKO)
        s = metre.summary
        await self._say(bus, req, "stage",
                        f"[metre] {metre.metre_score:.2f} ({s.get('lines', 0)} lines, "
                        f"8-syllable {s.get('octosyllabic', 0):.0%}, "
                        f"alliterating {s.get('alliterating', 0):.0%}, "
                        f"stress fit {s.get('stress_fit', 0):.0%})")
        return metre

    async def _deliver(self, bus: Bus, req: RunRequest, seed: Seed, attempt: int, probe: str,
                       mask_map: dict[str, str], trail: list[dict],
                       metre: MetreResult | None = None) -> str:
        context = {
            **self.tags, "chain_name": self.chain.name, "target_model": req.target_model,
            "claim": seed.claim, "claim_hash": seed.claim_hash, "claim_is_true": seed.claim_is_true,
            "pair_id": seed.pair_id, "lang": seed.lang, "category": seed.category,
            "source_url": seed.source_url,
            "mask_map_ref": sha256_text(json.dumps(mask_map, sort_keys=True)) if mask_map else "",
            "chain_snapshot": [asdict(s) for s in self.chain.stages],
            "stages": trail,
            "metre_score": metre.metre_score if metre else None,
            "metre": {**metre.summary, "worst_lines": metre.worst_lines} if metre else None,
        }
        reply = await self.transport.send(probe, run_id=req.run_id, attempt=attempt,
                                          seed_id=seed.id, context=context)
        await self._say(bus, req, "raw_exchange",
                        f"PROBE:\n{probe}\n\nRESPONSE ({reply.model_id}):\n{reply.text}")

        await bus.put(ScoreTask(seed_id=seed.id, run_id=req.run_id, attempt=attempt,
                                response_text=reply.text, response_source=reply.source,
                                evidence_ref=reply.evidence_ref,
                                response_hash=sha256_text(reply.text),
                                tokens_in=reply.tokens_in, tokens_out=reply.tokens_out,
                                cost=reply.cost, latency_ms=reply.latency_ms,
                                metre_score=metre.metre_score if metre else None),
                      channel=LEMMINKAINEN)
        scored = await bus.get(ResponseResult, channel=UKKO)
        flags = ", ".join(k for k, v in scored.signals.items() if v is True)
        await self._say(bus, req, "verdict", f"{scored.verdict.upper()} ({flags or 'no signals'})")
        return scored.verdict

    async def _say(self, bus: Bus, req: RunRequest, kind: str, text: str) -> None:
        await bus.put(ConsoleUpdate(seed_id=req.seed_id, run_id=req.run_id, kind=kind, text=text))
