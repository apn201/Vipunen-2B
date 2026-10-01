"""Command line: ``vipunen run``, ``vipunen models``, ``vipunen console``.

``vipunen run --demo`` is what ``make run`` calls: the public neutral chain and
the passthrough control arm over the public demo seeds.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from vipunen.agents.base import EchoStage
from vipunen.bus import ConsoleUpdate
from vipunen.chain import ChainError, LLM_OWNERS, load_chain
from vipunen.pipeline import run_batch
from vipunen.seeds import load_seeds
from vipunen.transport import EchoTransport

DEMO_CHAINS = ("config/mutations.example.yaml", "config/passthrough.yaml")
DEMO_SEEDS = "data/seeds/demo.yaml"


def print_update(u: ConsoleUpdate) -> None:
    print(f"--- {u.seed_id} [{u.kind}]\n{u.text.strip()}\n", flush=True)


def cmd_run(args: argparse.Namespace) -> int:
    if args.estimate:
        print("--estimate lands in S2 (budget.py)", file=sys.stderr)
        return 2
    if not args.echo:
        print("live endpoint lands in S2; use --echo for the offline pipeline", file=sys.stderr)
        return 2

    chains = args.chain or (list(DEMO_CHAINS) if args.demo else [])
    if not chains:
        print("give --chain PATH or --demo", file=sys.stderr)
        return 2
    seeds = load_seeds(args.seeds or DEMO_SEEDS)
    if args.limit:
        seeds = seeds[:args.limit]

    failed = False
    for path in chains:
        try:
            chain = load_chain(path)
        except ChainError as e:
            print(f"REFUSED {path}: {e}", file=sys.stderr)
            return 1
        print(f"=== chain {Path(path).name}: {' -> '.join(s.id for s in chain.stages)}")
        results = asyncio.run(run_batch(
            chain, seeds, transport=EchoTransport(),
            stage_agents=[EchoStage(owner) for owner in LLM_OWNERS],
            target_model="echo", on_update=print_update))
        for r in results:
            print(f"{r.seed_id}: {r.verdict} ({r.attempts} attempt)")
            failed |= r.verdict in ("refused", "error")
    return 1 if failed else 0


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

    r = sub.add_parser("run", help="run seeds through a chain")
    r.add_argument("--demo", action="store_true", help="public neutral chain + control arm on demo seeds")
    r.add_argument("--chain", action="append", help="chain YAML (repeatable)")
    r.add_argument("--seeds", help=f"seed YAML (default {DEMO_SEEDS})")
    r.add_argument("--limit", type=int, help="first N seeds only")
    r.add_argument("--echo", action="store_true", help="offline: echo stages and target, no network")
    r.add_argument("--estimate", action="store_true", help="price the run and exit")
    r.set_defaults(func=cmd_run)

    sub.add_parser("models", help="list model ids at the endpoint").set_defaults(func=cmd_pending("S2"))
    sub.add_parser("console", help="operator console on localhost").set_defaults(func=cmd_pending("S6"))

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
