"""The core invariant: no llm stage may run after the swap stage.

Every llm stage must only ever see masked text. These tests pin that the
invariant cannot be broken by config, by constructing a Chain directly, or by
any stage order.
"""
from pathlib import Path

import pytest

from vipunen.chain import Chain, ChainError, Stage, load_chain, parse_chain, render

CONFIG = Path(__file__).resolve().parents[1] / "config"


def stages(*specs):
    return {"chain": list(specs)}


VERSE = {"id": "verse", "kind": "llm", "owner": "vainamoinen", "template": "V {input}"}
ESC = {"id": "escalate", "kind": "llm", "owner": "louhi", "template": "E {input}", "mutates": True}
SWAP = {"id": "substitute", "kind": "swap"}
TARGET = {"id": "deliver", "kind": "target", "template": "T {input}"}


def test_valid_chain_parses():
    chain = parse_chain(stages(VERSE, ESC, SWAP, TARGET))
    assert [s.id for s in chain.stages] == ["verse", "escalate", "substitute", "deliver"]
    assert chain.swap_index == 2


def test_llm_after_swap_is_refused():
    with pytest.raises(ChainError, match="after the swap"):
        parse_chain(stages(VERSE, SWAP, ESC, TARGET))


def test_llm_after_swap_refused_even_when_constructed_directly():
    bad = (
        Stage(id="substitute", kind="swap"),
        Stage(id="verse", kind="llm", owner="vainamoinen", template="{input}"),
        Stage(id="deliver", kind="target", template="{input}"),
    )
    with pytest.raises(ChainError, match="after the swap"):
        Chain(stages=bad)


@pytest.mark.parametrize("order", [
    (VERSE, ESC, SWAP, TARGET),
    (VERSE, SWAP, TARGET),
    (SWAP, TARGET),
])
def test_every_llm_stage_precedes_swap(order):
    chain = parse_chain(stages(*order))
    for i, s in enumerate(chain.stages):
        if s.kind == "llm":
            assert i < chain.swap_index


def test_missing_swap_is_refused():
    # Without a swap the real terms would never enter - or worse, be in the
    # masked text already. Either way the chain is meaningless.
    with pytest.raises(ChainError, match="exactly one swap"):
        parse_chain(stages(VERSE, TARGET))


def test_two_swaps_are_refused():
    with pytest.raises(ChainError, match="exactly one swap"):
        parse_chain(stages(VERSE, SWAP, {"id": "s2", "kind": "swap"}, TARGET))


def test_target_must_be_last_and_unique():
    with pytest.raises(ChainError, match="target"):
        parse_chain(stages(VERSE, SWAP))
    with pytest.raises(ChainError, match="last"):
        parse_chain(stages(VERSE, TARGET, SWAP))


@pytest.mark.parametrize("bad, msg", [
    ({**VERSE, "owner": "ukko"}, "owner"),
    ({**VERSE, "template": "no placeholder"}, "{input}"),
    ({**VERSE, "kind": "shell"}, "kind"),
    ({"id": "verse", "kind": "llm", "owner": "vainamoinen"}, "template"),
])
def test_bad_llm_stage(bad, msg):
    with pytest.raises(ChainError, match=msg):
        parse_chain(stages(bad, SWAP, TARGET))


def test_swap_takes_no_template():
    with pytest.raises(ChainError, match="template"):
        parse_chain(stages(VERSE, {**SWAP, "template": "{input}"}, TARGET))


def test_duplicate_ids_refused():
    with pytest.raises(ChainError, match="duplicate"):
        parse_chain(stages(VERSE, {**ESC, "id": "verse"}, SWAP, TARGET))


def test_variants_must_target_mutating_stages():
    data = stages(VERSE, ESC, SWAP, TARGET)
    data["variants"] = {"escalate": ["A {input}"]}
    assert parse_chain(data).variants == {"escalate": ("A {input}",)}
    data["variants"] = {"verse": ["A {input}"]}
    with pytest.raises(ChainError, match="mutates"):
        parse_chain(data)
    data["variants"] = {"escalate": ["no placeholder"]}
    with pytest.raises(ChainError, match="{input}"):
        parse_chain(data)



def test_render_leaves_other_braces_alone():
    assert render("a {x} {input} }", "TEXT") == "a {x} TEXT }"


@pytest.mark.parametrize("name", ["mutations.example.yaml", "passthrough.yaml"])
def test_shipped_configs_are_valid(name):
    load_chain(CONFIG / name)
