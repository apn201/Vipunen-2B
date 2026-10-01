"""Seeds: one claim each, with what the deterministic scorer needs to judge the answer.

A seed is a claim (usually false) in one language. Pairs of the same claim in
different languages share a ``pair_id`` so the grid can compare fi against en.

Scorer fields, all regexes (Python ``re``, case-insensitive):

- ``pass_regex``  the ground truth is present in the answer
- ``fail_regex``  the false claim is restated as fact in the answer
- ``truth_example`` a short correct sentence in the seed's language; intake
  checks that ``pass_regex`` matches it and ``fail_regex`` matches the claim,
  so a typo in a regex is caught before any money is spent.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, fields
from pathlib import Path

import yaml

CATEGORIES = ("culture", "bias", "pii", "ip", "history")
LANGS = ("fi", "en", "sv", "de", "fr", "it")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Seed:
    id: str
    claim: str
    category: str
    lang: str
    claim_is_true: bool = False
    pair_id: str = ""
    ground_truth: str = ""        # English note for humans and reports
    source_url: str = ""
    truth_example: str = ""
    pass_regex: str = ""
    fail_regex: str = ""

    @property
    def claim_hash(self) -> str:
        return sha256_text(self.claim)


class SeedError(ValueError):
    pass


def check_seed(s: Seed) -> list[str]:
    """Problems with one seed; empty if it is usable."""
    errs = []
    if s.category not in CATEGORIES:
        errs.append(f"category must be one of {CATEGORIES}")
    if s.lang not in LANGS:
        errs.append(f"lang must be one of {LANGS}")
    if not s.claim.strip():
        errs.append("empty claim")
    for name in ("pass_regex", "fail_regex"):
        rx = getattr(s, name)
        if not rx:
            continue
        try:
            re.compile(rx)
        except re.error as e:
            errs.append(f"{name} does not compile: {e}")
    if s.category in ("history", "culture"):
        if not s.pass_regex or not s.truth_example:
            errs.append("history/culture seeds need pass_regex and truth_example")
        elif not re.search(s.pass_regex, s.truth_example, re.I):
            errs.append("pass_regex does not match truth_example")
        if not s.claim_is_true:
            if not s.fail_regex:
                errs.append("a false claim needs fail_regex")
            elif not re.search(s.fail_regex, s.claim, re.I):
                errs.append("fail_regex does not match the claim itself")
            if s.pass_regex and re.search(s.pass_regex, s.claim, re.I):
                errs.append("pass_regex matches the false claim - it cannot tell them apart")
    if not s.source_url:
        errs.append("missing source_url")
    return errs


def load_seeds(path: str | Path) -> list[Seed]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    known = {f.name for f in fields(Seed)}
    seeds, problems = [], []
    for raw in data.get("seeds", []):
        unknown = set(raw) - known
        if unknown:
            problems.append(f"{raw.get('id')}: unknown keys {sorted(unknown)}")
            continue
        seed = Seed(**raw)
        problems += [f"{seed.id}: {e}" for e in check_seed(seed)]
        seeds.append(seed)
    ids = [s.id for s in seeds]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        problems.append(f"duplicate seed ids {sorted(dupes)}")
    if problems:
        raise SeedError(f"{path}:\n  " + "\n  ".join(problems))
    return seeds
