"""S1 echo test: the whole agent pipeline end to end, offline."""
import asyncio

import pytest

from vipunen.agents import joukahainen
from vipunen.agents.base import EchoStage
from vipunen.bus import Bus, ConsoleUpdate, StageTask
from vipunen.chain import LLM_OWNERS, parse_chain
from vipunen.pipeline import run_batch
from vipunen.seeds import Seed
from vipunen.transport import EchoTransport, Reply

CHAIN = parse_chain({"chain": [
    {"id": "verse", "kind": "llm", "owner": "vainamoinen", "template": "V[{input}]"},
    {"id": "escalate", "kind": "llm", "owner": "louhi", "template": "E[{input}]"},
    {"id": "substitute", "kind": "swap"},
    {"id": "deliver", "kind": "target", "template": "T[{input}]"},
]})
SEEDS = [Seed(id="s1", claim="claim one", category="history", lang="en"),
         Seed(id="s2", claim="claim two", category="culture", lang="fi")]


def run(chain=CHAIN, seeds=SEEDS, transport=None, mask_maps=None, agents=None,
        unmask=joukahainen.unmask):
    updates: list[ConsoleUpdate] = []
    results = asyncio.run(run_batch(
        chain, seeds, transport=transport or EchoTransport(),
        stage_agents=agents or [EchoStage(o) for o in LLM_OWNERS],
        target_model="echo", mask_maps=mask_maps, on_update=updates.append, unmask=unmask))
    return results, updates


def test_echo_end_to_end():
    results, updates = run()
    assert [(r.seed_id, r.verdict) for r in results] == [("s1", "unclear"), ("s2", "unclear")]
    exchanges = [u.text for u in updates if u.kind == "raw_exchange"]
    assert "PROBE:\nT[E[V[claim one]]]" in exchanges[0]
    stage_ids = [u.text.split("]")[0] for u in updates if u.seed_id == "s1" and u.kind == "stage"]
    assert stage_ids == ["[verse", "[escalate", "[substitute"]


class RecordingStage(EchoStage):
    def __init__(self, owner, seen):
        super().__init__(owner)
        self.seen = seen

    async def compose(self, task: StageTask):
        self.seen.append(task.input_text)
        return await super().compose(task)


class RecordingTransport(EchoTransport):
    def __init__(self):
        self.probes = []

    async def send(self, probe, **kw):
        self.probes.append(probe)
        return Reply(text="ok", model_id="echo")


def swap_back(text, mask_map):
    for placeholder, real in mask_map.items():
        text = text.replace(placeholder, real)
    return text


def test_llm_stages_see_only_masked_text():

    seen: list[str] = []
    transport = RecordingTransport()
    seed = Seed(id="m", claim="the REALTERM did it", category="history", lang="en")
    run(seeds=[seed], transport=transport, mask_maps={"m": {"jänis": "REALTERM"}},
        agents=[RecordingStage(o, seen) for o in LLM_OWNERS],
        unmask=swap_back)  # stand-in for the private body

    assert seen and all("REALTERM" not in s and "jänis" in s for s in seen)
    assert transport.probes == ["T[E[V[the REALTERM did it]]]"]


def test_template_carrying_a_real_term_is_refused():
    leaky = parse_chain({"chain": [
        {"id": "verse", "kind": "llm", "owner": "vainamoinen", "template": "about REALTERM: {input}"},
        {"id": "substitute", "kind": "swap"},
        {"id": "deliver", "kind": "target", "template": "{input}"},
    ]})
    transport = RecordingTransport()
    results, updates = run(chain=leaky, seeds=[SEEDS[0]], transport=transport,
                           mask_maps={"s1": {"jänis": "REALTERM"}})
    assert results[0].verdict == "mask-leak"
    assert transport.probes == []
    assert any(u.kind == "error" and "REALTERM" not in u.text for u in updates)


def test_crashing_agent_fails_the_run_instead_of_hanging():
    class Broken(EchoStage):
        async def compose(self, task):
            raise ValueError("boom")

    with pytest.raises(RuntimeError, match="vainamoinen"):
        run(agents=[Broken("vainamoinen"), EchoStage("louhi")])


def test_budget_gate_stops_the_batch():
    results = asyncio.run(run_batch(
        CHAIN, SEEDS, transport=EchoTransport(),
        stage_agents=[EchoStage(o) for o in LLM_OWNERS], target_model="echo",
        gate=lambda: "ceiling reached"))
    assert results == []


def test_bus_rejects_non_messages():
    with pytest.raises(TypeError):
        asyncio.run(Bus().put("not a message"))


def test_mask_roundtrip_helpers():
    m = {"jänis": "Real Term", "kettu": "Real"}
    masked = joukahainen.mask("Real Term and Real", m)
    assert masked == "jänis and kettu"
    assert joukahainen.leaked_terms(masked, m) == []
    assert joukahainen.unmask("x", {}) == "x"
    with pytest.raises(NotImplementedError):
        joukahainen.unmask("x", m)
