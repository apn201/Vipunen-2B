"""S2: client, budget, transport - offline, against a fake OpenAI SDK."""
import asyncio
import json
from types import SimpleNamespace as NS

import httpx2 as httpx  # openai 3.x transport
import openai
import pytest

from vipunen.budget import Budget, BudgetExceeded, cost_usd
from vipunen.client import ApertusClient, ApertusError, _extract
from vipunen.config import Endpoint, load_settings, model_id_for
from vipunen.transport import LiveTransport

CSCS = Endpoint("cscs", "https://example.invalid/v1", "k" * 40)
PAI = Endpoint("publicai", "https://example.invalid/v1", "k" * 40)


def msg(content=None, **extra):
    return NS(content=content, model_extra=extra.pop("model_extra", {}), **extra)


class FakeSDK:
    def __init__(self, message=None, error=None, on_call=None):
        self.calls = []
        self.message = message or msg("Helsinki")
        self.error = error
        self.on_call = on_call
        self.chat = NS(completions=NS(create=self._create))

    async def _create(self, **params):
        self.calls.append(params)
        if self.on_call:
            self.on_call()
        if self.error:
            raise self.error
        usage = NS(model_dump=lambda: {"prompt_tokens": 100, "completion_tokens": 50})
        return NS(choices=[NS(message=self.message, finish_reason="stop")],
                  usage=usage, model=params["model"])


# --- extraction ----------------------------------------------------------------

@pytest.mark.parametrize("message, text, source, reasoning", [
    (msg("Helsinki"), "Helsinki", "content", ""),
    (msg("Helsinki", reasoning="user asks capital"), "Helsinki", "content", "user asks capital"),
    (msg("", reasoning_content="only thought"), "only thought", "reasoning", "only thought"),
    (msg(None, model_extra={"reasoning_content": "parked"}), "parked", "reasoning", "parked"),
    (msg("<|inner_prefix|>think<|inner_suffix|>Helsinki"), "Helsinki", "content", "think"),
    (msg("<|inner_prefix|>cut off mid-thought"), "cut off mid-thought", "reasoning", "cut off mid-thought"),
    (msg(""), "", "empty", ""),
])
def test_extract(message, text, source, reasoning):
    got_text, got_source, _, got_reasoning = _extract(message)
    assert (got_text, got_source, got_reasoning) == (text, source, reasoning)


def test_publicai_ids_are_mapped():
    assert model_id_for(PAI, "swiss-ai/Apertus-v1.5-8B-thinking") == "swiss-ai/apertus-v1.5-8b-thinking"
    assert model_id_for(PAI, "swiss-ai/Apertus-8B-Instruct-2509") == "swiss-ai/apertus-8b-instruct"
    assert model_id_for(CSCS, "swiss-ai/Apertus-v1.5-8B") == "swiss-ai/Apertus-v1.5-8B"


def test_client_sends_seed_and_mapped_id():
    sdk = FakeSDK()
    r = asyncio.run(ApertusClient(PAI, sdk=sdk).chat(
        [{"role": "user", "content": "x"}], model="swiss-ai/Apertus-v1.5-8B",
        max_tokens=10, temperature=0.0, seed=7))
    assert sdk.calls[0]["model"] == "swiss-ai/apertus-v1.5-8b" and sdk.calls[0]["seed"] == 7
    assert (r.text, r.tokens_in, r.tokens_out) == ("Helsinki", 100, 50)


def test_real_sdk_client_sets_user_agent():
    c = ApertusClient(Endpoint("publicai", "https://example.invalid/v1", "k" * 40, "vipunen-test"))
    assert c.sdk.default_headers["User-Agent"] == "vipunen-test"


def test_errors_are_explained_without_the_key():
    req = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    err = openai.AuthenticationError("bad key", response=httpx.Response(401, request=req), body=None)
    with pytest.raises(ApertusError) as e:
        asyncio.run(ApertusClient(CSCS, sdk=FakeSDK(error=err)).chat(
            [], model="m", max_tokens=1, temperature=0))
    assert "401" in str(e.value) and "k" * 40 not in str(e.value)


# --- budget --------------------------------------------------------------------

def budget(tmp_path, ceiling=1.0, **kw):
    return Budget(ceiling, tmp_path / "usage.json", kill_switch_path=tmp_path / "KILL", **kw)


def test_budget_records_on_send_and_persists(tmp_path):
    b = budget(tmp_path)
    b.authorize("swiss-ai/Apertus-v1.5-8B", 100, 100)
    assert budget(tmp_path).usage.calls == 1          # counted before any answer
    b.record("swiss-ai/Apertus-v1.5-8B", {"prompt_tokens": 1_000_000, "completion_tokens": 0})
    again = budget(tmp_path)
    assert again.usage.spend_usd == pytest.approx(0.10)


def test_budget_blocks_worst_case_over_ceiling(tmp_path):
    b = budget(tmp_path, ceiling=0.001)
    with pytest.raises(BudgetExceeded, match="ceiling"):
        b.authorize("swiss-ai/Apertus-v1.5-70B", 1000, 1_000_000)
    assert b.usage.calls == 0


def test_budget_kill_switches(tmp_path):
    with pytest.raises(BudgetExceeded, match="VIPUNEN_KILL"):
        budget(tmp_path, killed_by_env=True).authorize("m", 1, 1)
    (tmp_path / "KILL").touch()
    assert "kill switch" in budget(tmp_path).gate()


def test_corrupt_usage_file_fails_closed(tmp_path):
    (tmp_path / "usage.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(BudgetExceeded, match="unreadable"):
        budget(tmp_path).authorize("swiss-ai/Apertus-v1.5-8B", 1, 1)


def test_unknown_model_priced_as_dearest():
    assert cost_usd("self-hosted-alias", 0, 1_000_000) == cost_usd("swiss-ai/Apertus-v1.5-70B", 0, 1_000_000)


def test_call_cap(tmp_path):
    b = budget(tmp_path, max_calls=1)
    b.authorize("swiss-ai/Apertus-v1.5-8B", 1, 1)
    with pytest.raises(BudgetExceeded, match="cap"):
        b.authorize("swiss-ai/Apertus-v1.5-8B", 1, 1)


# --- transport -----------------------------------------------------------------

def transport(tmp_path, sdk, ceiling=1.0):
    return LiveTransport(ApertusClient(CSCS, sdk=sdk), budget(tmp_path, ceiling),
                         model="swiss-ai/Apertus-v1.5-8B", evidence_dir=tmp_path / "ev",
                         max_tokens=100, temperature=0.0, seed=1)


def test_record_written_before_call_and_completed_after(tmp_path):
    path = tmp_path / "ev" / "r1" / "1.json"
    seen = {}
    sdk = FakeSDK(message=msg("Helsinki", reasoning="thought"),
                  on_call=lambda: seen.update(json.loads(path.read_text(encoding="utf-8"))))
    t = transport(tmp_path, sdk)
    reply = asyncio.run(t.send("PROBE", run_id="r1", attempt=1, seed_id="s",
                               context={"claim": "c", "lang": "fi"}))
    assert seen["status"] == "sent" and seen["probe_text"] == "PROBE"
    rec = json.loads(path.read_text(encoding="utf-8"))
    assert rec["status"] == "ok" and rec["claim"] == "c" and rec["reasoning_text"] == "thought"
    assert rec["probe_hash"] and rec["response_hash"] and rec["cost_usd"] > 0
    assert reply.text == "Helsinki" and reply.evidence_ref.endswith("r1/1.json")


def test_send_is_idempotent(tmp_path):
    sdk = FakeSDK()
    t = transport(tmp_path, sdk)
    for _ in range(2):
        asyncio.run(t.send("P", run_id="r", attempt=1, seed_id="s"))
    assert len(sdk.calls) == 1


def test_error_is_recorded(tmp_path):
    req = httpx.Request("POST", "https://example.invalid")
    err = openai.InternalServerError("boom", response=httpx.Response(500, request=req), body=None)
    t = transport(tmp_path, FakeSDK(error=err))
    with pytest.raises(ApertusError):
        asyncio.run(t.send("P", run_id="r", attempt=1, seed_id="s"))
    rec = json.loads((tmp_path / "ev" / "r" / "1.json").read_text(encoding="utf-8"))
    assert rec["status"] == "error" and "500" in rec["error"]


def test_budget_refusal_sends_nothing(tmp_path):
    sdk = FakeSDK()
    with pytest.raises(BudgetExceeded):
        asyncio.run(transport(tmp_path, sdk, ceiling=0.0).send("P", run_id="r", attempt=1, seed_id="s"))
    assert sdk.calls == [] and not (tmp_path / "ev" / "r" / "1.json").exists()


# --- settings ------------------------------------------------------------------

def test_settings_defaults_and_fallback():
    s = load_settings({"LLM_API_KEY": "x"})
    assert s.endpoint.name == "cscs" and s.target_model == "swiss-ai/Apertus-v1.5-8B"
    p = load_settings({"PUBLICAI_API_KEY": "y"}, endpoint="publicai")
    assert p.endpoint.name == "publicai" and p.endpoint.api_key == "y"
    assert "y" not in repr(p.endpoint)
