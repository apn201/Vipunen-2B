"""The publish gate's detectors. Fake secrets are built at runtime so this file never trips the gate."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "publish_gate", Path(__file__).resolve().parents[1] / "tools" / "publish_gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

FAKE_HF = "hf" + "_" + "A1b2C3d4" * 5
FAKE_SK = "sk" + "-" + "x9Y8z7W6" * 4


def test_secret_shapes_are_caught():
    assert gate.scan_text("f", f"token: {FAKE_HF}", [])
    assert gate.scan_text("f", f"key = {FAKE_SK}", [])
    assert gate.scan_text("f", "LLM_API" + "_KEY=" + "abcdefgh12345678", [])
    assert gate.scan_text("f", "-----BEGIN RSA " + "PRIVATE KEY-----", [])


def test_literal_env_values_are_caught_and_not_echoed():
    secret = "plain-looking-value-42"
    hits = gate.scan_text("README.md", f"see {secret}", [secret])
    assert hits and all(secret not in h for h in hits)


def test_clean_text_passes():
    assert gate.scan_text("f", "LLM_API_KEY=\nHF_TOKEN=\nLLM_NAME=swiss-ai/Apertus-v1.5-8B", []) == []


def test_forbidden_paths():
    blocked = ["track_2b/private/mutations.yaml", "track_2b/.env", ".env.local",
               "track_2b/var/evidence/r1/1.json", "x/masks/run1.yaml", "a.verbatim.json",
               "track_2b/mutations.yaml"]
    allowed = ["track_2b/.env.example", "track_2b/config/mutations.example.yaml",
               "track_2b/config/passthrough.yaml", "track_2b/src/vipunen/agents/joukahainen.py",
               "track_2b/var/evidence/.gitkeep"]
    assert all(gate.scan_paths([p]) for p in blocked)
    assert gate.scan_paths(allowed) == []
