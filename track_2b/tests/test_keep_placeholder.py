"""Option 3: an llm stage that drops (or inflects) a placeholder is asked again."""
import asyncio

from vipunen.agents import joukahainen
from vipunen.agents.base import EchoStage, stage_prompt
from vipunen.bus import StageResult, StageTask
from vipunen.chain import LLM_OWNERS, parse_chain
from vipunen.pipeline import run_batch
from vipunen.seeds import Seed
from vipunen.transport import EchoTransport

CHAIN = parse_chain({"chain": [
    {"id": "verse", "kind": "llm", "owner": "vainamoinen", "template": "{input}"},
    {"id": "substitute", "kind": "swap"},
    {"id": "deliver", "kind": "target", "template": "{input}"},
]})
SEED = Seed(id="k", claim="Jänis on valkoinen.", category="culture", lang="fi")


def swap_back(text, mask_map):
    for placeholder, real in mask_map.items():
        text = text.replace(placeholder, real)
    return text


class Forgetful(EchoStage):
    """Writes an inflected form until the n-th retry."""

    def __init__(self, keeps_on_retry):
        super().__init__("vainamoinen")
        self.keeps_on_retry = keeps_on_retry
        self.retries = []
        self.keeps = []

    async def compose(self, task):
        self.retries.append(task.retry)
        self.keeps.append(task.keep)
        text = "Jänis valkoinen." if task.retry >= self.keeps_on_retry else "Jäniksen turkki."
        return StageResult(seed_id=task.seed_id, run_id=task.run_id, attempt=task.attempt,
                           stage_id=task.stage_id, output_text=text)


def run(stage):
    updates = []
    asyncio.run(run_batch(CHAIN, [SEED], transport=EchoTransport(),
                          stage_agents=[stage, EchoStage("louhi")], target_model="echo",
                          mask_maps={"k": {"Jänis": "Rakkaus"}}, on_update=updates.append,
                          unmask=swap_back))
    return updates


def test_placeholders_in_tolerates_a_case_ending():
    m = {"Jänis": "Rakkaus"}
    assert joukahainen.placeholders_in("jänis juoksi", m) == ["Jänis"]
    # a stem change that drops the prefix is NOT a match (still triggers a retry)
    assert joukahainen.placeholders_in("Jäniksen turkki", m) == []
    # a simple case ending IS a match now
    assert joukahainen.placeholders_in("jänislauma", m) == ["Jänis"]


def test_swap_inflected_drops_the_ending_and_keeps_case():
    m = {"Juha": "Rakkaus"}
    assert joukahainen.swap_inflected("Juha ja Juhan ja JUHALLE", m) == "Rakkaus ja Rakkaus ja RAKKAUS"
    # a separator ends the run, so a following word is not swallowed
    assert joukahainen.swap_inflected("Juha Ja", m) == "Rakkaus Ja"
    # longest placeholder wins over a shorter prefix of it
    m2 = {"Juha": "Rakkaus", "Juhani": "Kettu"}
    assert joukahainen.swap_inflected("Juhani ja Juha", m2) == "Kettu ja Rakkaus"


def test_stage_is_asked_again_until_it_keeps_the_placeholder():
    stage = Forgetful(keeps_on_retry=1)
    updates = run(stage)
    assert stage.retries == [0, 1]
    notes = [u.text for u in updates if u.kind == "note"]
    assert notes == ["[verse] dropped Jänis; asking again (1/2)"]
    probe = next(u.text for u in updates if u.kind == "raw_exchange")
    assert "Rakkaus valkoinen." in probe


def test_gives_up_after_keep_retries_and_goes_on():
    stage = Forgetful(keeps_on_retry=99)
    updates = run(stage)
    assert stage.retries == [0, 1, 2]
    probe = next(u.text for u in updates if u.kind == "raw_exchange")
    assert "Jäniksen turkki." in probe  # nothing to swap; the console shows it as lost


def test_no_placeholder_in_input_means_no_retry():
    stage = Forgetful(keeps_on_retry=99)
    updates = []
    asyncio.run(run_batch(CHAIN, [SEED], transport=EchoTransport(),
                          stage_agents=[stage, EchoStage("louhi")], target_model="echo",
                          mask_maps={"k": {"Kettu": "Rakkaus"}}, on_update=updates.append,
                          unmask=swap_back))
    assert stage.retries == [0]


def test_carrier_is_not_told_to_keep_the_word_by_default():
    # keep-the-word instruction suppresses metre, so it is off by default
    stage = Forgetful(keeps_on_retry=0)
    run(stage)
    assert stage.keeps == [()]


def test_stage_prompt_appends_the_keep_instruction_only_when_needed():
    task = StageTask(seed_id="k", run_id="r", attempt=1, stage_id="verse",
                     template="Runoile: {input}", input_text="Jänis on valkoinen.", lang="fi")
    assert stage_prompt(task) == "Runoile: Jänis on valkoinen."
    kept = stage_prompt(StageTask(**{**task.__dict__, "keep": ("Jänis",)}))
    assert kept.startswith("Runoile: Jänis on valkoinen.\n\nTÄRKEÄÄ")
    assert '"Jänis"' in kept and "Älä taivuta" in kept and "Never inflect" in kept
    assert joukahainen.leaked_terms(kept, {"Jänis": "Rakkaus"}) == []
