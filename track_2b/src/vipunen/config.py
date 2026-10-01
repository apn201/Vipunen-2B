"""Settings from the environment, and the Apertus model table.

Env names follow the HackApertus template (``LLM_NAME``, ``LLM_BASE_URL``,
``LLM_API_KEY``). The endpoint is the only external dependency: point
``LLM_BASE_URL`` at CSCS (sovereign Swiss cloud), Public AI, or a self-hosted
vLLM Apertus (on-prem / air-gapped) and nothing else changes.

Model ids are canonical in CSCS casing; ``model_id_for`` maps them to what an
endpoint calls them (Public AI uses lowercase ids and drops the -2509 suffix).
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

CSCS_URL = "https://api.inference.cscs.ch/v1"
PUBLICAI_URL = "https://api.publicai.co/v1"
DEFAULT_USER_AGENT = "vipunen/0.1 (Hack Apertus 2026)"


@dataclass(frozen=True)
class ModelInfo:
    id: str                  # canonical (CSCS) id
    publicai_id: str
    usd_in_per_m: float      # list price per 1M tokens (Public AI); used as a conservative ceiling everywhere
    usd_out_per_m: float
    thinking: bool = False
    on_cscs: bool = True     # the hackathon CSCS key is not entitled to the v1.0 models (HTTP 403)


MODELS: dict[str, ModelInfo] = {m.id: m for m in [
    ModelInfo("swiss-ai/Apertus-v1.5-8B", "swiss-ai/apertus-v1.5-8b", 0.10, 0.20),
    ModelInfo("swiss-ai/Apertus-v1.5-8B-thinking", "swiss-ai/apertus-v1.5-8b-thinking", 0.10, 0.20, True),
    ModelInfo("swiss-ai/Apertus-v1.5-70B", "swiss-ai/apertus-v1.5-70b", 0.82, 2.92),
    ModelInfo("swiss-ai/Apertus-v1.5-70B-thinking", "swiss-ai/apertus-v1.5-70b-thinking", 0.82, 2.92, True),
    ModelInfo("swiss-ai/Apertus-8B-Instruct-2509", "swiss-ai/apertus-8b-instruct", 0.10, 0.20, on_cscs=False),
    ModelInfo("swiss-ai/Apertus-70B-Instruct-2509", "swiss-ai/apertus-70b-instruct", 0.82, 2.92, on_cscs=False),
]}
# Unknown models (e.g. a self-hosted alias) are priced like the dearest known one: fail closed.
FALLBACK_PRICE = ModelInfo("unknown", "unknown", 0.82, 2.92)


def model_info(model: str) -> ModelInfo:
    if model in MODELS:
        return MODELS[model]
    for m in MODELS.values():
        if model.lower() == m.publicai_id:
            return m
    return FALLBACK_PRICE


def model_id_for(endpoint: "Endpoint", model: str) -> str:
    info = MODELS.get(model)
    if info and endpoint.name == "publicai":
        return info.publicai_id
    return model


@dataclass(frozen=True)
class Endpoint:
    name: str                # 'cscs' | 'publicai' | 'custom'
    base_url: str
    api_key: str = field(repr=False)
    user_agent: str = DEFAULT_USER_AGENT

    @property
    def redacted_key(self) -> str:
        k = self.api_key
        return f"{k[:4]}...{k[-2:]} ({len(k)} chars)" if len(k) > 8 else "(missing or too short)"


@dataclass(frozen=True)
class Settings:
    endpoint: Endpoint
    target_model: str
    stage_model: str                 # model the llm stages (Vainamoinen, Louhi) run on
    budget_usd: float
    target_temperature: float = 0.0
    stage_temperature: float = 0.8
    seed: int | None = 1234
    target_max_tokens: int = 1500
    stage_max_tokens: int = 1500
    timeout_s: float = 180.0
    evidence_dir: Path = Path("private/evidence")
    usage_path: Path = Path("var/usage.json")
    kill_switch_path: Path = Path("var/KILL")
    killed_by_env: bool = False


class SettingsError(RuntimeError):
    pass


def _endpoint(env: Mapping[str, str], which: str) -> Endpoint:
    ua = env.get("VIPUNEN_USER_AGENT") or DEFAULT_USER_AGENT
    if which == "publicai":
        return Endpoint("publicai", env.get("PUBLICAI_BASE_URL") or PUBLICAI_URL,
                        env.get("PUBLICAI_API_KEY", ""), ua)
    base = env.get("LLM_BASE_URL") or CSCS_URL
    name = "cscs" if "cscs.ch" in base else "publicai" if "publicai" in base else "custom"
    return Endpoint(name, base, env.get("LLM_API_KEY", ""), ua)


def load_settings(env: Mapping[str, str] | None = None, *, endpoint: str | None = None,
                  target_model: str | None = None) -> Settings:
    """``endpoint='auto'`` uses LLM_* unless that is CSCS and the model is not served there."""
    env = os.environ if env is None else env
    which = endpoint or env.get("VIPUNEN_ENDPOINT") or "llm"
    if which not in ("llm", "cscs", "publicai", "auto"):
        raise SettingsError(f"endpoint must be llm|cscs|publicai|auto, got {which!r}")
    if which == "auto":
        model = target_model or env.get("LLM_NAME") or ""
        on_cscs = MODELS[model].on_cscs if model in MODELS else True
        which = "llm" if on_cscs or _endpoint(env, "llm").name != "cscs" else "publicai"
    ep = _endpoint(env, "publicai" if which == "publicai" else "llm")

    def num(name: str, default: float) -> float:
        raw = env.get(name)
        try:
            return float(raw) if raw not in (None, "") else default
        except ValueError as e:
            raise SettingsError(f"{name} must be a number, got {raw!r}") from e

    default_model = env.get("LLM_NAME") or "swiss-ai/Apertus-v1.5-8B"
    target = target_model or default_model
    seed_raw = env.get("VIPUNEN_SEED", "1234")
    return Settings(
        endpoint=ep,
        target_model=target,
        # The carrier is written by ONE fixed model whatever the target, so a grid over
        # targets compares targets, not verse writers.
        stage_model=env.get("VIPUNEN_STAGE_MODEL") or default_model,
        # Thinking models spend tokens on the trace first; 1500 cut one off mid-thought.
        target_max_tokens=4000 if model_info(target).thinking else 1500,
        budget_usd=num("VIPUNEN_BUDGET_USD", 5.0),
        target_temperature=num("VIPUNEN_TEMPERATURE", 0.0),
        seed=int(seed_raw) if seed_raw.strip() else None,
        evidence_dir=Path(env.get("VIPUNEN_EVIDENCE_DIR") or "private/evidence"),
        killed_by_env=env.get("VIPUNEN_KILL", "") not in ("", "0"),
    )


def require_key(settings: Settings) -> None:
    if not settings.endpoint.api_key:
        var = "PUBLICAI_API_KEY" if settings.endpoint.name == "publicai" else "LLM_API_KEY"
        raise SettingsError(f"{var} is not set. Copy .env.example to .env and fill it in, "
                            "or run offline with --echo.")
