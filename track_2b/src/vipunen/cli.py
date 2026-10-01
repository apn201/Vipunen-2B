"""Command line: ``vipunen run``, ``vipunen models``, ``vipunen console``.

``vipunen run --demo`` is what ``make run`` calls: the public neutral chain and
the passthrough control arm over the public demo seeds, against the live endpoint.
``--echo`` runs the same pipeline offline. ``--estimate`` prices a run and exits.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from vipunen.agents.base import EchoStage
from vipunen.agents.louhi import Louhi
from vipunen.agents.vainamoinen import Vainamoinen
from vipunen.budget import Budget, BudgetExceeded, cost_usd, estimate_tokens
from vipunen.bus import ConsoleUpdate
from vipunen.chain import Chain, ChainError, LLM_OWNERS, load_chain
from vipunen.client import ApertusClient, ApertusError
from vipunen.config import MODELS, Settings, SettingsError, load_settings, require_key
from vipunen.pipeline import run_batch
from vipunen.seeds import Seed, load_seeds
from vipunen.transport import EchoTransport, LiveTransport

DEMO_CHAINS = ("config/mutations.example.yaml", "config/passthrough.yaml")
DEMO_SEEDS = "data/seeds/demo.yaml"
PING = "Mikä on Suomen pääkaupunki? Vastaa yhdellä sanalla."


def print_update(u: ConsoleUpdate) -> None:
    print(f"--- {u.seed_id} [{u.kind}]\n{u.text.strip()}\n", flush=True)


def estimate(chains: list[tuple[str, Chain]], seeds: list[Seed], s: Settings) -> tuple[int, float]:
    """Worst case: every attempt runs every stage to its max_tokens."""
    calls, usd = 0, 0.0
    for _, chain in chains:
        for seed in seeds:
            prev = estimate_tokens(seed.claim)
            for stage in chain.stages:
                if stage.kind == "swap":
                    continue
                model, out = ((s.stage_model, s.stage_max_tokens) if stage.kind == "llm"
                              else (s.target_model, s.target_max_tokens))
                t_in = estimate_tokens(stage.template or "") + prev
                usd += cost_usd(model, t_in, out) * chain.max_tries
                calls += chain.max_tries
                prev = out
    return calls, usd


def cmd_run(args: argparse.Namespace) -> int:
    chain_paths = args.chain or (list(DEMO_CHAINS) if args.demo else [])
    if not chain_paths:
        print("give --chain PATH or --demo", file=sys.stderr)
        return 2
    try:
        chains = [(p, load_chain(p)) for p in chain_paths]
    except ChainError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 1
    seeds = load_seeds(args.seeds or DEMO_SEEDS)
    if args.limit:
        seeds = seeds[:args.limit]

    settings = load_settings(endpoint=args.endpoint, target_model=args.model)
    if args.estimate:
        calls, usd = estimate(chains, seeds, settings)
        budget = Budget.from_settings(settings)
        left = settings.budget_usd - budget.usage.spend_usd
        print(f"estimate: {len(seeds)} seeds x {len(chains)} chain(s): up to {calls} calls, "
              f"<= {usd:.4f} USD worst case (target {settings.target_model}, "
              f"stages {settings.stage_model}). Budget left: {left:.4f} USD.")
        return 0 if usd <= left else 1

    return asyncio.run(_run(args, chains, seeds, settings))


async def _run(args: argparse.Namespace, chains: list[tuple[str, Chain]], seeds: list[Seed],
               settings: Settings) -> int:
    """All chains in ONE event loop: the HTTP client's connection pool is bound to it."""
    client = None
    if args.echo:
        transport, agents, budget = EchoTransport(), [EchoStage(o) for o in LLM_OWNERS], None
    else:
        require_key(settings)
        budget = Budget.from_settings(settings)
        client = ApertusClient(settings.endpoint, timeout_s=settings.timeout_s)
        transport = LiveTransport(client, budget, model=settings.target_model,
                                  evidence_dir=settings.evidence_dir,
                                  max_tokens=settings.target_max_tokens,
                                  temperature=settings.target_temperature, seed=settings.seed)
        stage_kw = dict(model=settings.stage_model, max_tokens=settings.stage_max_tokens,
                        temperature=settings.stage_temperature, seed=settings.seed)
        agents = [Vainamoinen(client, budget, **stage_kw), Louhi(client, budget, **stage_kw)]
        print(f"endpoint {settings.endpoint.name} ({settings.endpoint.base_url}), "
              f"target {settings.target_model}, budget {budget.status()}")

    failed = False
    try:
        for path, chain in chains:
            print(f"=== chain {Path(path).name}: {' -> '.join(s.id for s in chain.stages)}")
            results = await run_batch(
                chain, seeds, transport=transport, stage_agents=agents,
                target_model=transport.model_id, on_update=print_update,
                gate=budget.gate if budget else (lambda: None))
            for r in results:
                print(f"{r.seed_id}: {r.verdict}")
                failed |= r.verdict in ("refused", "error", "budget")
            if len(results) < len(seeds):
                print(f"stopped early: {budget.gate() if budget else 'gate'}", file=sys.stderr)
                failed = True
    finally:
        if client:
            await client.aclose()
    if budget:
        print(f"budget: {budget.status()}")
    return 1 if failed else 0


async def _models(settings: Settings, ping: bool, budget: Budget | None) -> int:
    client = ApertusClient(settings.endpoint, timeout_s=settings.timeout_s)
    try:
        return await _ping(client, settings, ping, budget)
    finally:
        await client.aclose()


async def _ping(client: ApertusClient, settings: Settings, ping: bool, budget: Budget | None) -> int:
    ids = await client.models()
    print(f"{settings.endpoint.name} ({settings.endpoint.base_url}): {len(ids)} models")
    for i in ids:
        print(f"  {i}")
    if not ping:
        return 0
    bad = 0
    print(f"\nping: {PING!r}")
    for model in MODELS:
        try:
            budget.authorize(model, estimate_tokens(PING), 400)
            r = await client.chat([{"role": "user", "content": PING}], model=model,
                                  max_tokens=400, temperature=0.0, seed=settings.seed)
            budget.record(model, r.usage)
            print(f"  {model:38} {r.latency_ms:6} ms  {r.tokens_in:4}+{r.tokens_out:<4} "
                  f"answer[{r.source}]={r.content[:40]!r}  reasoning={len(r.reasoning)} chars")
        except (ApertusError, BudgetExceeded) as e:
            bad += 1
            print(f"  {model:38} FAILED: {e}")
    print(f"budget: {budget.status()}")
    return 1 if bad else 0


def cmd_models(args: argparse.Namespace) -> int:
    settings = load_settings(endpoint=args.endpoint)
    require_key(settings)
    budget = Budget.from_settings(settings) if args.ping else None
    try:
        return asyncio.run(_models(settings, args.ping, budget))
    except ApertusError as e:
        print(e, file=sys.stderr)
        return 1


def cmd_pending(slice_: str):
    def run(_: argparse.Namespace) -> int:
        print(f"not implemented yet ({slice_})", file=sys.stderr)
        return 2
    return run


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Finnish on a Windows console
    p = argparse.ArgumentParser(prog="vipunen")
    sub = p.add_subparsers(dest="cmd", required=True)
    endpoint = dict(choices=("llm", "cscs", "publicai"),
                    help="llm = LLM_BASE_URL/LLM_API_KEY (default); publicai = PUBLICAI_* fallback")

    r = sub.add_parser("run", help="run seeds through a chain")
    r.add_argument("--demo", action="store_true", help="public neutral chain + control arm on demo seeds")
    r.add_argument("--chain", action="append", help="chain YAML (repeatable)")
    r.add_argument("--seeds", help=f"seed YAML (default {DEMO_SEEDS})")
    r.add_argument("--limit", type=int, help="first N seeds only")
    r.add_argument("--model", help="target model id (default LLM_NAME)")
    r.add_argument("--endpoint", **endpoint)
    r.add_argument("--echo", action="store_true", help="offline: echo stages and target, no network")
    r.add_argument("--estimate", action="store_true", help="price the run (worst case) and exit")
    r.set_defaults(func=cmd_run)

    m = sub.add_parser("models", help="list model ids at the endpoint")
    m.add_argument("--ping", action="store_true", help="one short call per known Apertus model")
    m.add_argument("--endpoint", **endpoint)
    m.set_defaults(func=cmd_models)

    sub.add_parser("console", help="operator console on localhost").set_defaults(func=cmd_pending("S6"))

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except SettingsError as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
