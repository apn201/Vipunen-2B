"""Load and validate the mutation chain.

A chain is an ordered list of stages, run top to bottom once per attempt:

- ``llm``    sent to Apertus by its owner agent (vainamoinen = verse, louhi = escalation)
- ``swap``   Joukahainen's non-LLM placeholder-to-real substitution; no template
- ``target`` the final wrapper; Transport sends its output to the model under test

The invariant: **no llm stage may come after the swap stage**, so every Apertus
text-shaping call sees only masked text. It is enforced in ``Chain.__post_init__``,
so no ``Chain`` object that breaks it can exist, however it was built.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

INPUT = "{input}"
KINDS = ("llm", "swap", "target")
LLM_OWNERS = ("vainamoinen", "louhi")


class ChainError(ValueError):
    """The chain config is invalid. Ukko refuses to run it."""


@dataclass(frozen=True)
class Stage:
    id: str
    kind: str
    owner: str | None = None
    template: str | None = None
    mutates: bool = False


@dataclass(frozen=True)
class Chain:
    stages: tuple[Stage, ...]
    variants: dict[str, tuple[str, ...]] = field(default_factory=dict)
    name: str = ""

    def __post_init__(self) -> None:
        validate(self)

    @property
    def swap_index(self) -> int:
        return next(i for i, s in enumerate(self.stages) if s.kind == "swap")

    def stage(self, stage_id: str) -> Stage:
        return next(s for s in self.stages if s.id == stage_id)


def render(template: str, text: str) -> str:
    """Fill ``{input}``. Plain replace, not str.format: templates may hold other braces."""
    return template.replace(INPUT, text)


def validate(chain: Chain) -> None:
    stages = chain.stages
    if not stages:
        raise ChainError("chain is empty")

    seen: set[str] = set()
    for s in stages:
        if not s.id:
            raise ChainError("every stage needs an id")
        if s.id in seen:
            raise ChainError(f"duplicate stage id {s.id!r}")
        seen.add(s.id)
        if s.kind not in KINDS:
            raise ChainError(f"stage {s.id!r}: kind must be one of {KINDS}, got {s.kind!r}")
        if s.kind == "llm" and s.owner not in LLM_OWNERS:
            raise ChainError(f"stage {s.id!r}: llm owner must be one of {LLM_OWNERS}, got {s.owner!r}")
        if s.kind == "swap":
            if s.template is not None:
                raise ChainError(f"stage {s.id!r}: swap takes no template")
        else:
            if not s.template:
                raise ChainError(f"stage {s.id!r}: {s.kind} stage needs a template")
            if INPUT not in s.template:
                raise ChainError(f"stage {s.id!r}: template must contain {INPUT}")

    swaps = [i for i, s in enumerate(stages) if s.kind == "swap"]
    if len(swaps) != 1:
        raise ChainError(f"chain needs exactly one swap stage, found {len(swaps)}")
    swap_at = swaps[0]

    # THE invariant. Real terms enter at swap; nothing after it may be an LLM call.
    late = [s.id for s in stages[swap_at + 1:] if s.kind == "llm"]
    if late:
        raise ChainError(f"llm stage(s) {late} come after the swap stage - refused: "
                         "every llm stage must see only masked text")

    targets = [i for i, s in enumerate(stages) if s.kind == "target"]
    if len(targets) != 1:
        raise ChainError(f"chain needs exactly one target stage, found {len(targets)}")
    if targets[0] != len(stages) - 1:
        raise ChainError("the target stage must be last")

    by_id = {s.id: s for s in stages}
    for stage_id, templates in chain.variants.items():
        if stage_id not in by_id:
            raise ChainError(f"variants for unknown stage {stage_id!r}")
        if not by_id[stage_id].mutates:
            raise ChainError(f"variants given for {stage_id!r}, which is not mutates: true")
        for t in templates:
            if INPUT not in t:
                raise ChainError(f"variant for {stage_id!r} must contain {INPUT}")


def _ref(data: dict[str, Any], stage_id: str, key: str, want: type) -> Any:
    if key not in data:
        raise ChainError(f"stage {stage_id!r}: reference {key!r} not found at top level")
    value = data[key]
    if value is None and want is list:
        return []
    if not isinstance(value, want):
        raise ChainError(f"stage {stage_id!r}: {key!r} must be a {want.__name__}")
    return value


def parse_chain(data: dict[str, Any], name: str = "") -> Chain:
    """Templates and variants may sit inline, or at top level referenced by
    ``template_ref`` / ``variants_ref`` (keeps long operator templates in one place)."""
    if not isinstance(data, dict) or not isinstance(data.get("chain"), list):
        raise ChainError("config must be a mapping with a 'chain' list")
    stages = []
    variants = {k: list(v or ()) for k, v in (data.get("variants") or {}).items()}
    for raw in data["chain"]:
        if not isinstance(raw, dict):
            raise ChainError(f"stage must be a mapping, got {raw!r}")
        stage_id = str(raw.get("id", ""))
        unknown = set(raw) - {"id", "kind", "owner", "template", "mutates", "template_ref", "variants_ref"}
        if unknown:
            raise ChainError(f"stage {stage_id!r}: unknown keys {sorted(unknown)}")
        template = raw.get("template")
        if "template_ref" in raw:
            if template is not None:
                raise ChainError(f"stage {stage_id!r}: give template or template_ref, not both")
            template = _ref(data, stage_id, raw["template_ref"], str)
        if "variants_ref" in raw:
            variants[stage_id] = variants.get(stage_id, []) + _ref(data, stage_id, raw["variants_ref"], list)
        stages.append(Stage(
            id=stage_id,
            kind=raw.get("kind"),
            owner=raw.get("owner"),
            template=template,
            mutates=bool(raw.get("mutates", False)),
        ))
    # A ``loop:`` block (per-seed retry loop) was planned (S4) but cut; it is ignored
    # if present, so older operator configs still load. See the technical report.
    return Chain(stages=tuple(stages),
                 variants={k: tuple(v) for k, v in variants.items() if v}, name=name)


def load_chain(path: str | Path) -> Chain:
    with open(path, encoding="utf-8") as f:
        return parse_chain(yaml.safe_load(f), name=Path(path).stem)
