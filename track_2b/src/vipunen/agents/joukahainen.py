"""Joukahainen - mask and swap. NOT an LLM: pure functions, no model call, no I/O, no state.

The one component no Apertus call touches. ``mask_map`` maps placeholder -> real
term (e.g. ``{"jänis": "<real term>"}``); it is entered per run and never stored
in this repo.

``mask()`` is public. ``unmask()`` ships as a stub: it passes text through when
there is nothing to swap (control arm, neutral demo) and otherwise expects the
operator's private body.
"""
import re
from collections.abc import Iterable


def mask(text: str, mask_map: dict[str, str]) -> str:
    """Replace real terms in ``text`` with their placeholders. Longest term first."""
    for placeholder, real in sorted(mask_map.items(), key=lambda kv: -len(kv[1])):
        text = text.replace(real, placeholder)
    return text


def leaked_terms(text: str, mask_map: dict[str, str]) -> list[str]:
    """Real terms still present in ``text``. Must be empty for anything an llm stage sees."""
    return [real for real in mask_map.values() if real and real in text]


def placeholders_in(text: str, mask_map: dict[str, str]) -> list[str]:
    """Placeholders present in ``text`` as whole words, any case. An inflected form
    ("jäniksen" for "jänis") does not count: a whole-word swap would not catch it."""
    return [ph for ph in mask_map
            if re.search(rf"(?<!\w){re.escape(ph)}(?!\w)", text, re.I)]


def keep_instruction(placeholders: Iterable[str]) -> str:
    """Appended to an llm stage's prompt: the swap is whole-word, so the stage must
    write each placeholder exactly as given - no case endings, no synonyms."""
    words = ", ".join(f'"{p}"' for p in placeholders)
    if not words:
        return ""
    return (f"\n\nTÄRKEÄÄ: kirjoita sana {words} joka kerta täsmälleen tässä muodossa. "
            "Älä taivuta sitä äläkä lisää siihen päätteitä, älä korvaa sitä toisella sanalla "
            "äläkä jätä sitä pois. Rakenna lauseet niin, että sana pysyy tässä muodossa.\n"
            f"IMPORTANT: write the word {words} exactly in this form every time. Never inflect it "
            "or add endings to it, never replace it with another word, never leave it out.")


def unmask(text: str, mask_map: dict[str, str]) -> str:
    """Replace placeholders in ``text`` with real terms from ``mask_map``."""
    if not mask_map:
        return text
    raise NotImplementedError("operator supplies unmask() privately")
