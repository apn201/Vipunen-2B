"""Kalevala metre heuristics (trochaic tetrameter, alliteration, syllable count) used by Ilmarinen.

Pure functions, no model call. A line of Kalevala metre (kalevalamitta) is checked for:

- **syllables**  8 is the norm; 9-10 are allowed (extra syllables go in the first foot), 7 is rare.
- **stress**     in feet 2-4 (positions 3-8) a word-initial *heavy* syllable sits on a rise
                 (odd position) and a word-initial *light* syllable on a fall (even position).
                 The first foot is free. Monosyllables are free. A monosyllable must not end
                 the line.
- **alliteration** two words sharing an initial consonant (strong if the first vowel matches
                 too: "vaka vanha"), or two vowel-initial words (strong if the same vowel).

Syllabification follows the Finnish rule: a boundary falls before a consonant followed by a
vowel; vowel pairs stay together when long (aa) or a diphthong (ai, au, ...; ie/uo/yö only in
the first syllable). An apostrophe ("saa'ani", "ve'en") marks a boundary. Compound-word
stress is not detected, so the score is a heuristic, recorded and never a gate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

VOWELS = set("aeiouyäöå")
DIPHTHONGS = {"ai", "ei", "oi", "ui", "yi", "äi", "öi", "au", "eu", "iu", "ou",
              "ey", "iy", "äy", "öy"}
FIRST_SYLLABLE_DIPHTHONGS = {"ie", "uo", "yö"}
WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.U)

# Line score weights. Stress fit is what separates metre from merely short lines.
W_SYLLABLES, W_STRESS, W_ALLITERATION = 0.3, 0.4, 0.3
SYLLABLE_FIT = {8: 1.0, 9: 0.75, 10: 0.75, 7: 0.5}
ALLITERATION_FIT = {"strong": 1.0, "weak": 0.5, "none": 0.0}


def _nuclei(part: str) -> list[tuple[int, int]]:
    """(start, end) spans of vowel nuclei in a lowercase part of a word."""
    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(part):
        if part[i] not in VOWELS:
            i += 1
            continue
        end = i + 1
        if end < len(part) and part[end] in VOWELS:
            pair = part[i:end + 1]
            if (pair[0] == pair[1] or pair in DIPHTHONGS
                    or (pair in FIRST_SYLLABLE_DIPHTHONGS and not spans)):
                end += 1
        spans.append((i, end))
        i = end
    return spans


def _syllabify_part(part: str, first: bool) -> list[str]:
    spans = _nuclei(part)
    if not first and spans:
        # ie/uo/yö after an apostrophe are not word-initial, so they split.
        s, e = spans[0]
        if part[s:e] in FIRST_SYLLABLE_DIPHTHONGS:
            spans[0:1] = [(s, s + 1), (s + 1, e)]
    if not spans:
        return [part] if part else []
    cuts = [0]
    for (_, prev_end), (next_start, _) in zip(spans, spans[1:]):
        consonants = next_start - prev_end
        cuts.append(next_start - 1 if consonants else next_start)
    cuts.append(len(part))
    return [part[a:b] for a, b in zip(cuts, cuts[1:])]


def syllables(word: str) -> list[str]:
    """Split one Finnish word into syllables. A word with no vowel is one syllable-less chunk -> []."""
    out: list[str] = []
    for n, part in enumerate(re.split(r"['’]", word.lower())):
        out.extend(_syllabify_part(part, first=n == 0))
    return [s for s in out if any(c in VOWELS for c in s)]


def is_heavy(syllable: str) -> bool:
    """Heavy = long vowel or diphthong, or closed by a consonant."""
    s = syllable.lower()
    if s and s[-1] not in VOWELS:
        return True
    return any(a in VOWELS and b in VOWELS for a, b in zip(s, s[1:]))


def _onset(word: str) -> tuple[str, str]:
    """(initial consonant or '', first vowel). v/w count as one sound."""
    w = word.lower().replace("w", "v")
    consonant = "" if w[0] in VOWELS else w[0]
    vowel = next((c for c in w if c in VOWELS), "")
    return consonant, vowel


def alliteration(words: list[str]) -> str:
    """'strong', 'weak' or 'none' for the best-alliterating pair of words in a line."""
    onsets = [_onset(w) for w in words if syllables(w)]
    best = "none"
    for i, (c1, v1) in enumerate(onsets):
        for c2, v2 in onsets[i + 1:]:
            if c1 != c2:
                continue
            if v1 == v2:
                return "strong"
            best = "weak"
    return best


@dataclass(frozen=True)
class LineScore:
    text: str
    syllables: int
    stress_checked: int
    stress_ok: int
    final_monosyllable: bool
    alliteration: str
    score: float
    violations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MetreScore:
    score: float                  # mean line score, 0..1; this is the trail's ``metre_score``
    lines: list[LineScore]
    octosyllabic: float           # share of lines with exactly 8 syllables
    alliterating: float           # share of lines with any alliteration
    stress_fit: float             # stress positions obeyed / checked, over all lines

    def summary(self) -> dict:
        return {"metre_score": round(self.score, 3), "lines": len(self.lines),
                "octosyllabic": round(self.octosyllabic, 3),
                "alliterating": round(self.alliterating, 3),
                "stress_fit": round(self.stress_fit, 3)}


def score_line(line: str) -> LineScore | None:
    """Score one verse line, or None when it holds no syllables."""
    words = WORD.findall(line)
    split = [syllables(w) for w in words]
    split = [(w, s) for w, s in zip(words, split) if s]
    n = sum(len(s) for _, s in split)
    if not n:
        return None
    shift = n - 8  # align from the line end: extra or missing syllables belong to foot 1
    violations: list[str] = []
    checked = ok = 0
    j = 0
    for word, syl in split:
        pos = j - shift + 1
        if len(syl) > 1 and pos >= 3:
            checked += 1
            heavy = is_heavy(syl[0])
            if heavy == (pos % 2 == 1):
                ok += 1
            else:
                violations.append(f"{word}: {'heavy' if heavy else 'light'} initial on position {pos}")
        j += len(syl)
    final_mono = len(split[-1][1]) == 1
    if final_mono:
        violations.append(f"{split[-1][0]}: monosyllable ends the line")
    if n not in (8, 9, 10):
        violations.append(f"{n} syllables")
    allit = alliteration([w for w, _ in split])
    stress = (ok / checked if checked else 1.0) * (0.5 if final_mono else 1.0)
    score = (W_SYLLABLES * SYLLABLE_FIT.get(n, 0.0) + W_STRESS * stress
             + W_ALLITERATION * ALLITERATION_FIT[allit])
    return LineScore(line.strip(), n, checked, ok, final_mono, allit, round(score, 4), violations)


def score_verse(text: str) -> MetreScore:
    """Score a verse line by line. Blank and letterless lines are skipped; empty verse scores 0."""
    lines = [s for s in (score_line(l) for l in text.splitlines()) if s]
    if not lines:
        return MetreScore(0.0, [], 0.0, 0.0, 0.0)
    k = len(lines)
    checked = sum(l.stress_checked for l in lines)
    return MetreScore(
        score=sum(l.score for l in lines) / k,
        lines=lines,
        octosyllabic=sum(l.syllables == 8 for l in lines) / k,
        alliterating=sum(l.alliteration != "none" for l in lines) / k,
        stress_fit=sum(l.stress_ok for l in lines) / checked if checked else 1.0,
    )
