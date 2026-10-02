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

# A carrier stage often adds a case ending to a placeholder ("Juha" -> "Juhan",
# "Juhalle", "JUHALLE"). The swap tolerates that: a placeholder matches itself
# plus a short run of trailing word characters (the ending is dropped on swap).
# A following word is safe because whitespace/punctuation breaks the run. This
# does NOT catch a stem change where the placeholder is no longer even a prefix
# (Finnish "jänis" -> "jäniksen"); that still triggers a keep-retry.
MAX_ENDING = 12


def inflected_re(placeholder: str) -> re.Pattern:
    """Match ``placeholder`` (any case) plus an optional case ending, at a word start."""
    return re.compile(rf"(?<!\w)({re.escape(placeholder)})\w{{0,{MAX_ENDING}}}", re.I)


def mask(text: str, mask_map: dict[str, str]) -> str:
    """Replace real terms in ``text`` with their placeholders. Longest term first."""
    for placeholder, real in sorted(mask_map.items(), key=lambda kv: -len(kv[1])):
        text = text.replace(real, placeholder)
    return text


def leaked_terms(text: str, mask_map: dict[str, str]) -> list[str]:
    """Real terms still present in ``text``. Must be empty for anything an llm stage sees."""
    return [real for real in mask_map.values() if real and real in text]


def placeholders_in(text: str, mask_map: dict[str, str]) -> list[str]:
    """Placeholders present in ``text``, tolerating a lowercase case ending (``inflected_re``).
    A stem change that drops the placeholder prefix ("jänis" -> "jäniksen") does not count,
    so it still triggers a keep-retry."""
    return [ph for ph in mask_map if inflected_re(ph).search(text)]


def _shape(sample: str, real: str) -> str:
    """Give ``real`` the case shape of ``sample`` (the matched placeholder text)."""
    if sample.isupper() and len(sample) > 1:
        return real.upper()
    if sample[:1].islower():
        return real[:1].lower() + real[1:]
    return real[:1].upper() + real[1:]


def swap_inflected(text: str, mask_map: dict[str, str]) -> str:
    """Replace each placeholder (and a trailing lowercase case ending) with its real
    term, dropping the ending and keeping the placeholder's case shape. Longest
    placeholder first, so a longer one wins over a shorter prefix of it."""
    for placeholder, real in sorted(mask_map.items(), key=lambda kv: -len(kv[0])):
        text = inflected_re(placeholder).sub(lambda m: _shape(m.group(1), real), text)
    return text


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
