"""Seeds: one claim each, with what the deterministic scorer needs to judge the answer.

S1 has the record and the loader. S3 adds intake from benchmarks and per-category checks.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

CATEGORIES = ("culture", "bias", "pii", "ip", "history")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Seed:
    id: str
    claim: str
    category: str
    lang: str
    ground_truth: str = ""
    source_url: str = ""
    pass_regex: str = ""      # ground truth present in the answer
    fail_regex: str = ""      # false claim affirmed in the answer

    @property
    def claim_hash(self) -> str:
        return sha256_text(self.claim)


def load_seeds(path: str | Path) -> list[Seed]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    seeds = [Seed(**raw) for raw in data.get("seeds", [])]
    ids = [s.id for s in seeds]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate seed ids in {path}")
    for s in seeds:
        if s.category not in CATEGORIES:
            raise ValueError(f"seed {s.id!r}: category must be one of {CATEGORIES}")
    return seeds
