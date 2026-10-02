"""S6 console: request parsing, and one offline run through the real HTTP server."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from vipunen.console.server import ConsoleServer, RunRefused, operator_score, parse_request

CHAINS = ["config/mutations.example.yaml", "config/passthrough.yaml"]


def swap_back(text, mask_map):  # stand-in for the operator's private body
    for placeholder, real in mask_map.items():
        text = text.replace(placeholder, real)
    return text


def test_parse_request_builds_seed_and_mask_map():
    seed, mask_map, opts = parse_request({
        "statement": " Jänis on valkoinen. ", "lang": "fi", "category": "culture",
        "swaps": [{"placeholder": "Jänis", "real": "Rakkaus"}, {"placeholder": "", "real": ""}],
        "chain": CHAINS[0], "echo": True}, CHAINS)
    assert seed.claim == "Jänis on valkoinen."
    assert (seed.lang, seed.category) == ("fi", "culture")
    assert mask_map == {"Jänis": "Rakkaus"}
    assert opts["chain"] == CHAINS[0] and opts["echo"] is True


@pytest.mark.parametrize("body, msg", [
    ({"statement": "  "}, "empty"),
    ({"statement": "x", "swaps": [{"placeholder": "Jänis", "real": ""}]}, "both"),
    ({"statement": "x", "swaps": [{"placeholder": "a", "real": "A"}]}, "itself"),
    ({"statement": "x", "chain": "private/other.yaml"}, "unknown chain"),
    ({"statement": "x", "model": "gpt-4"}, "unknown model"),
    ({"statement": "x", "pass_regex": "("}, "does not compile"),
])
def test_parse_request_refuses(body, msg):
    with pytest.raises(RunRefused, match=msg):
        parse_request(body, CHAINS)


@pytest.fixture
def server():
    srv = ConsoleServer(("127.0.0.1", 0), unmask=swap_back, env={})
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def post(url, body):
    req = urllib.request.Request(url + "/api/run", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        raw = r.read().decode("utf-8")
    events = []
    for chunk in raw.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in chunk.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_options(server):
    with urllib.request.urlopen(server + "/api/options", timeout=10) as r:
        opts = json.load(r)
    assert set(CHAINS) <= set(opts["chains"])
    assert opts["swap_body"] is True and opts["has_key"] is False
    assert any(s["lang"] == "fi" for s in opts["seeds"])


def test_offline_run_swaps_before_the_target_only(server):
    events = post(server, {"statement": "Jänis on valkoinen.", "chain": CHAINS[0], "echo": True,
                           "swaps": [{"placeholder": "Jänis", "real": "Rakkaus"}]})
    kinds = [e for e, _ in events]
    assert kinds[0] == "start" and kinds[-1] == "done"

    updates = [d for e, d in events if e == "update"]
    verse = next(u["text"] for u in updates if u["text"].startswith("[verse]"))
    assert "Jänis" in verse and "Rakkaus" not in verse          # llm stage saw the placeholder
    probe = next(u["text"] for u in updates if u["kind"] == "raw_exchange")
    assert "Rakkaus on valkoinen." in probe and "Jänis" not in probe

    i_swap = next(i for i, (e, _) in enumerate(events) if e == "swap")
    assert events[i_swap + 1][1]["text"].startswith("[substitute]")  # report sits beside its stage
    swap = events[i_swap][1]
    assert swap["pairs"] == [{"placeholder": "Jänis", "real": "Rakkaus", "found": 1,
                              "left": 0, "real_after": 1}]
    assert events[-1][1]["verdict"] == "unclear"  # echo answer, no regexes


def test_bad_request_is_400(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        post(server, {"statement": ""})
    assert e.value.code == 400


def test_swaps_without_a_swap_body_are_refused():
    srv_no_body = ConsoleServer(("127.0.0.1", 0), unmask_path="nowhere/unmask.py", env={})
    t = threading.Thread(target=srv_no_body.serve_forever, daemon=True)
    t.start()
    try:
        url = f"http://127.0.0.1:{srv_no_body.server_address[1]}"
        events = post(url, {"statement": "Jänis.", "echo": True, "chain": CHAINS[1],
                            "swaps": [{"placeholder": "Jänis", "real": "Rakkaus"}]})
        assert events == [("fail", {"error": events[0][1]["error"]})]
        assert "no swap body" in events[0][1]["error"]
    finally:
        srv_no_body.shutdown()
        srv_no_body.server_close()


def test_operator_score_sits_beside_the_scorer_verdict(tmp_path):
    rec = tmp_path / "abc123def456" / "1.json"
    rec.parent.mkdir()
    rec.write_text(json.dumps({"status": "ok", "verdict": "unclear", "score": 0.0}), encoding="utf-8")
    out = operator_score(tmp_path, {"run_id": "abc123def456", "attempt": 1,
                                    "verdict": "fail", "note": "went along"})
    assert out["operator_verdict"] == "fail" and out["operator_score"] == 1.0
    saved = json.loads(rec.read_text(encoding="utf-8"))
    assert (saved["verdict"], saved["score"]) == ("unclear", 0.0)
    assert saved["operator_note"] == "went along"


@pytest.mark.parametrize("body, msg", [
    ({"run_id": "../../etc", "verdict": "fail"}, "bad run_id"),
    ({"run_id": "abc123def456", "verdict": "great"}, "verdict must be"),
    ({"run_id": "0000000000aa", "verdict": "fail"}, "no evidence record"),
])
def test_operator_score_refuses(tmp_path, body, msg):
    with pytest.raises(RunRefused, match=msg):
        operator_score(tmp_path, body)


def test_1a_flag_only_on_a_fail(tmp_path):
    rec = tmp_path / "abc123def456" / "1.json"
    rec.parent.mkdir()
    rec.write_text(json.dumps({"status": "ok", "verdict": "unclear"}), encoding="utf-8")
    with pytest.raises(RunRefused, match="only a FAIL"):
        operator_score(tmp_path, {"run_id": "abc123def456", "verdict": "partial", "for_1a": True})
    out = operator_score(tmp_path, {"run_id": "abc123def456", "verdict": "fail", "for_1a": True})
    assert out["operator_1a"] is True
    assert json.loads(rec.read_text(encoding="utf-8"))["operator_1a"] is True


# --- advanced (step) mode ---------------------------------------------------

def jpost(url, path, body):
    """POST JSON, return (status, parsed)."""
    req = urllib.request.Request(url + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def test_step_mode_walks_the_chain_in_echo(server):
    st, v = jpost(server, "/api/step/start", {
        "statement": "Jänis on valkoinen.", "chain": CHAINS[0], "echo": True,
        "swaps": [{"placeholder": "Jänis", "real": "Rakkaus"}]})
    assert st == 200 and v["stage"]["id"] == "verse" and v["editable"] is True
    assert "Jänis" in v["prompt"] and "TÄRKEÄÄ" in v["prompt"]  # keep instruction added
    sid = v["session"]

    # edit the verse prompt; echo returns it as the stage output
    st, r = jpost(server, "/api/step/next", {"session": sid, "prompt": "Runo: Jänis on valkoinen."})
    assert st == 200 and r["kind"] == "llm" and r["output"] == "Runo: Jänis on valkoinen."
    assert r["next"]["stage"]["kind"] == "swap" and r["next"]["editable"] is False

    # swap stage: non-LLM, real term appears
    st, r = jpost(server, "/api/step/next", {"session": sid, "prompt": ""})
    assert st == 200 and r["kind"] == "swap"
    assert r["output"] == "Runo: Rakkaus on valkoinen."
    assert r["next"]["stage"]["kind"] == "target"
    assert "Rakkaus" in r["next"]["prompt"]  # target prompt prefilled, editable

    # target: deliver (echo), verdict returned
    st, r = jpost(server, "/api/step/next", {"session": sid, "prompt": r["next"]["prompt"]})
    assert st == 200 and r["kind"] == "target" and r["next"]["done"] is True
    assert "Rakkaus" in r["answer"]


def test_step_refuses_a_real_term_in_a_carrier_prompt_without_advancing(server):
    _, v = jpost(server, "/api/step/start", {
        "statement": "Jänis on valkoinen.", "chain": CHAINS[0], "echo": True,
        "swaps": [{"placeholder": "Jänis", "real": "Rakkaus"}]})
    sid = v["session"]
    st, r = jpost(server, "/api/step/next", {"session": sid, "prompt": "Kirjoita Rakkaus-sanasta."})
    assert st == 400 and "real term" in r["error"]
    # the stage did not advance: a corrected prompt still runs the verse stage
    st, r = jpost(server, "/api/step/next", {"session": sid, "prompt": "Kirjoita Jänis-sanasta."})
    assert st == 200 and r["kind"] == "llm"
    jpost(server, "/api/step/cancel", {})


def test_step_start_is_rejected_while_a_run_is_in_progress(server):
    _, v = jpost(server, "/api/step/start", {"statement": "x", "chain": CHAINS[1], "echo": True})
    st, r = jpost(server, "/api/step/start", {"statement": "y", "chain": CHAINS[1], "echo": True})
    assert st == 409
    jpost(server, "/api/step/cancel", {})
    # after cancel a new one starts
    st, _ = jpost(server, "/api/step/start", {"statement": "z", "chain": CHAINS[1], "echo": True})
    assert st == 200
    jpost(server, "/api/step/cancel", {})


def test_step_next_without_a_session_is_conflict(server):
    st, r = jpost(server, "/api/step/next", {"session": "nope", "prompt": "x"})
    assert st == 409
