"""Wire the agents onto one bus and run a batch of seeds through a chain.

Shared by the CLI, the console (S6) and the batch grid (S7). Pohjanakka drives;
every other agent is a long-lived task that is cancelled when Pohjanakka is done.
If any agent crashes, the whole run fails loudly instead of hanging.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable

from vipunen.agents import joukahainen
from vipunen.agents.base import StageAgent
from vipunen.agents.ilmarinen import Ilmarinen
from vipunen.agents.lemminkainen import Lemminkainen
from vipunen.agents.pohjanakka import Pohjanakka
from vipunen.agents.ukko import Ukko
from vipunen.bus import Bus, ConsoleUpdate, RunComplete
from vipunen.chain import Chain
from vipunen.seeds import Seed
from vipunen.transport import Transport


async def _relay(bus: Bus, sink: Callable[[ConsoleUpdate], None]) -> None:
    while True:
        sink(await bus.get(ConsoleUpdate))


async def run_batch(chain: Chain, seeds: Iterable[Seed], *, transport: Transport,
                    stage_agents: Iterable[StageAgent], target_model: str,
                    mask_maps: dict[str, dict[str, str]] | None = None,
                    on_update: Callable[[ConsoleUpdate], None] | None = None,
                    gate: Callable[[], str | None] = lambda: None,
                    unmask: Callable[[str, dict[str, str]], str] = joukahainen.unmask,
                    tags: dict | None = None) -> list[RunComplete]:
    seeds = list(seeds)
    bus = Bus()
    pohjanakka = Pohjanakka(seeds, target_model=target_model, gate=gate)
    by_id = {s.id: s for s in seeds}
    ukko = Ukko(chain, by_id, transport, mask_maps=mask_maps, unmask=unmask, tags=tags)
    lemminkainen = Lemminkainen(by_id)

    workers = [asyncio.create_task(ukko.run(bus), name="ukko"),
               asyncio.create_task(lemminkainen.run(bus), name="lemminkainen"),
               asyncio.create_task(Ilmarinen().run(bus), name="ilmarinen")]
    workers += [asyncio.create_task(a.run(bus), name=a.owner) for a in stage_agents]
    if on_update:
        workers.append(asyncio.create_task(_relay(bus, on_update), name="console"))
    driver = asyncio.create_task(pohjanakka.run(bus), name="pohjanakka")

    try:
        done, _ = await asyncio.wait([driver, *workers], return_when=asyncio.FIRST_COMPLETED)
        if driver in done:
            return driver.result()
        task = done.pop()
        raise RuntimeError(f"agent {task.get_name()} stopped") from task.exception()
    finally:
        for task in [driver, *workers]:
            task.cancel()
        await asyncio.gather(driver, *workers, return_exceptions=True)
        if on_update:  # flush anything the relay had not printed yet
            for update in bus.drain(ConsoleUpdate):
                on_update(update)
