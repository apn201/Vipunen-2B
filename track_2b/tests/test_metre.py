"""S4: Kalevala metre heuristics (syllables, stress positions, alliteration)."""
import pytest

from vipunen.agents.ilmarinen import Ilmarinen
from vipunen.bus import MetreTask
from vipunen.metre import alliteration, is_heavy, score_line, score_verse, syllables

KALEVALA = """Mieleni minun tekevi,
aivoni ajattelevi
lähteäni laulamahan,
saa'ani sanelemahan,
sukuvirttä suoltamahan,
lajivirttä laulamahan."""


@pytest.mark.parametrize("word, expected", [
    ("Väinämöinen", ["väi", "nä", "möi", "nen"]),
    ("karhu", ["kar", "hu"]),
    ("maailma", ["maa", "il", "ma"]),
    ("kauan", ["kau", "an"]),
    ("hioa", ["hi", "o", "a"]),
    ("suoltamahan", ["suol", "ta", "ma", "han"]),
    ("lähteäni", ["läh", "te", "ä", "ni"]),
    ("sukuvirttä", ["su", "ku", "virt", "tä"]),
    ("saa'ani", ["saa", "a", "ni"]),
    ("ve'en", ["ve", "en"]),
    ("Kristus", ["kris", "tus"]),
    ("hm", []),
])
def test_syllables(word, expected):
    assert syllables(word) == expected


def test_heavy_and_light():
    assert is_heavy("van") and is_heavy("väi") and is_heavy("saa")
    assert not is_heavy("va") and not is_heavy("mi")


def test_alliteration_strength():
    assert alliteration(["vaka", "vanha", "Väinämöinen"]) == "strong"
    assert alliteration(["sukuvirttä", "sanelemahan"]) == "weak"
    assert alliteration(["aivoni", "ajattelevi"]) == "strong"
    assert alliteration(["lähden", "kotiin"]) == "none"


def test_canonical_line_is_clean():
    line = score_line("Vaka vanha Väinämöinen")
    assert line.syllables == 8 and line.alliteration == "strong"
    assert line.stress_ok == line.stress_checked and not line.violations
    assert line.score == 1.0


def test_light_initial_on_a_rise_is_flagged():
    # "sanoi" (light "sa") lands on position 5, a rise.
    line = score_line("Vaka vanha sanoi aina")
    assert line.violations == ["sanoi: light initial on position 5"]
    assert score_line("Siellä vanha sanoi: mies").final_monosyllable


def test_kalevala_opening_scores_high_and_prose_low():
    verse = score_verse(KALEVALA)
    assert len(verse.lines) == 6 and verse.octosyllabic == 1.0
    assert verse.stress_fit == 1.0 and verse.score > 0.85
    prose = score_verse("Suomi itsenäistyi vuonna 1917 joulukuun kuudentena päivänä.")
    assert prose.score < 0.5


def test_empty_and_blank_lines():
    assert score_verse("").score == 0.0
    assert len(score_verse("Vaka vanha Väinämöinen\n\n  \n---\n").lines) == 1
    assert score_verse(KALEVALA).summary()["lines"] == 6


def test_ilmarinen_reports_score_and_worst_lines():
    verse = KALEVALA + "\nSuomi itsenäistyi vuonna joulukuussa."
    r = Ilmarinen().score(MetreTask(seed_id="s", run_id="r", attempt=1, verse_text=verse, lang="fi"))
    assert r.summary["lines"] == 7 and r.metre_score == round(score_verse(verse).score, 3)
    assert r.worst_lines[0][0] == "Suomi itsenäistyi vuonna joulukuussa."
    assert r.worst_lines[0][2]  # violations listed
