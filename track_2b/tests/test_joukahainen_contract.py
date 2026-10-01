"""Joukahainen is a pure function with a fixed public signature."""
import inspect

from vipunen.agents.joukahainen import unmask


def test_unmask_signature():
    assert list(inspect.signature(unmask).parameters) == ["text", "mask_map"]
