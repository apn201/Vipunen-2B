"""Joukahainen - mask and swap. NOT an LLM: pure functions, no model call, no I/O, no state.

The one component no Apertus call touches. ``mask_map`` maps placeholder -> real
term (e.g. ``{"jänis": "<real term>"}``); it is entered per run and never stored
in this repo.

``mask()`` is public. ``unmask()`` ships as a stub: it passes text through when
there is nothing to swap (control arm, neutral demo) and otherwise expects the
operator's private body.
"""


def mask(text: str, mask_map: dict[str, str]) -> str:
    """Replace real terms in ``text`` with their placeholders. Longest term first."""
    for placeholder, real in sorted(mask_map.items(), key=lambda kv: -len(kv[1])):
        text = text.replace(real, placeholder)
    return text


def leaked_terms(text: str, mask_map: dict[str, str]) -> list[str]:
    """Real terms still present in ``text``. Must be empty for anything an llm stage sees."""
    return [real for real in mask_map.values() if real and real in text]


def unmask(text: str, mask_map: dict[str, str]) -> str:
    """Replace placeholders in ``text`` with real terms from ``mask_map``."""
    if not mask_map:
        return text
    raise NotImplementedError("operator supplies unmask() privately")
