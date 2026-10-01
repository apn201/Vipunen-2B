"""Ukko - orchestrator (not LLM). Masks the claim, runs the chain stage by stage.

Before every llm stage Ukko checks that no real term from the mask map is in the
text the stage would see - a second guard behind the chain-order check, for the
case where the operator's mask misses a term or a template carries one.

S1: one attempt per seed. S4 adds the per-seed loop (one variable per attempt).
"""
from __future__ import annotations

from vipunen.agents import joukahainen
from vipunen.agents.base import UKKO
from vipunen.bus import Bus, ConsoleUpdate, RunComplete, RunRequest, StageResult, StageTask
from vipunen.chain import Chain, render, validate
from vipunen.seeds import Seed
from vipunen.transport import Transport


class MaskLeak(RuntimeError):
    """A real term would reach an llm stage. The run is refused."""


class Ukko:
    def __init__(self, chain: Chain, seeds: dict[str, Seed], transport: Transport,
                 mask_maps: dict[str, dict[str, str]] | None = None,
                 unmask=joukahainen.unmask) -> None:
        validate(chain)  # already enforced at construction; cheap to restate here
        self.chain = chain
        self.seeds = seeds
        self.transport = transport
        self.mask_maps = mask_maps or {}
        self.unmask = unmask

    async def run(self, bus: Bus) -> None:
        while True:
            req = await bus.get(RunRequest)
            try:
                verdict = await self.attempt(bus, req, attempt=1)
            except MaskLeak as e:
                await self._say(bus, req, "error", f"refused: {e}")
                verdict = "refused"
            await bus.put(RunComplete(seed_id=req.seed_id, run_id=req.run_id,
                                      attempts=1, verdict=verdict))

    async def attempt(self, bus: Bus, req: RunRequest, attempt: int) -> str:
        seed = self.seeds[req.seed_id]
        mask_map = self.mask_maps.get(seed.id, {})
        text = joukahainen.mask(seed.claim, mask_map)

        for stage in self.chain.stages:
            if stage.kind == "llm":
                leaked = joukahainen.leaked_terms(render(stage.template, text), mask_map)
                if leaked:
                    raise MaskLeak(f"stage {stage.id!r} would see {len(leaked)} unmasked term(s)")
                await bus.put(StageTask(seed_id=seed.id, run_id=req.run_id, attempt=attempt,
                                        stage_id=stage.id, template=stage.template,
                                        input_text=text, lang=req.lang),
                              channel=stage.owner)
                result = await bus.get(StageResult, channel=UKKO)
                text = result.output_text
            elif stage.kind == "swap":
                text = self.unmask(text, mask_map)
            else:  # target - always last
                probe = render(stage.template, text)
                reply = await self.transport.send(probe, run_id=req.run_id, attempt=attempt,
                                                  seed_id=seed.id)
                await self._say(bus, req, "raw_exchange",
                                f"PROBE:\n{probe}\n\nRESPONSE ({reply.model_id}):\n{reply.text}")
                return "unscored"  # Lemminkainen scores from S3
            await self._say(bus, req, "stage", f"[{stage.id}] {text}")
        raise AssertionError("unreachable: validated chain ends with a target stage")

    async def _say(self, bus: Bus, req: RunRequest, kind: str, text: str) -> None:
        await bus.put(ConsoleUpdate(seed_id=req.seed_id, run_id=req.run_id, kind=kind, text=text))
