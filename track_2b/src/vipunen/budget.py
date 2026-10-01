# Provenance: ported from apn201/Virta virta/cost_control.py @ 4d85c72 (2026-09-23),
# adapted for Vipunen 2026-10-01: kept the kill switch, hard caps, persisted usage,
# record-on-send and fail-closed rules; dropped the situation hash and debounce.
# Same author.
"""Budget gate: decides whether the next Apertus call may happen, and records it.

Layers, in the order the gate applies them:
  1. Kill switch     - VIPUNEN_KILL=1, or the file var/KILL exists. Stops everything.
  2. Spend ceiling   - VIPUNEN_BUDGET_USD, cumulative across runs (persisted), and
                       the projected cost of THIS call must fit under it too.
  3. Call cap        - optional hard cap on the number of calls.

Fails closed everywhere:
- a call is recorded when it is SENT, before the answer, so a crash mid-call still counts
- a corrupt usage file blocks all calls until a human looks at it
- an unknown model is priced like the dearest known one

Prices are Public AI list prices; CSCS may be cheaper or free. That only makes the
gate conservative.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vipunen.config import Settings, model_info


class BudgetExceeded(RuntimeError):
    """The gate said no. Nothing was sent."""


@dataclass
class Usage:
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    spend_usd: float = 0.0
    first_call: str = ""
    last_call: str = ""


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    m = model_info(model)
    return tokens_in / 1e6 * m.usd_in_per_m + tokens_out / 1e6 * m.usd_out_per_m


def _atomic_write(path: Path, text: str) -> None:
    """Write via temp file + rename. The rename is retried: on a Windows host folder
    that a sync client (Dropbox) watches, the target is briefly locked after each write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        for i in range(8):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if i == 7:
                    raise
                time.sleep(0.05 * 2 ** i)  # up to ~6 s in total
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class Budget:
    def __init__(self, ceiling_usd: float, usage_path: Path, *, kill_switch_path: Path,
                 killed_by_env: bool = False, max_calls: int | None = None) -> None:
        self.ceiling_usd = ceiling_usd
        self.usage_path = Path(usage_path)
        self.kill_switch_path = Path(kill_switch_path)
        self.killed_by_env = killed_by_env
        self.max_calls = max_calls
        self._lock = threading.Lock()
        self.corrupt = ""
        self.usage = self._load()

    @classmethod
    def from_settings(cls, s: Settings, max_calls: int | None = None) -> "Budget":
        return cls(s.budget_usd, s.usage_path, kill_switch_path=s.kill_switch_path,
                   killed_by_env=s.killed_by_env, max_calls=max_calls)

    def _load(self) -> Usage:
        try:
            return Usage(**json.loads(self.usage_path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return Usage()
        except (ValueError, TypeError, OSError) as e:
            self.corrupt = f"{self.usage_path} unreadable ({type(e).__name__}); fix or delete it"
            return Usage()

    def _save(self) -> None:
        _atomic_write(self.usage_path, json.dumps(asdict(self.usage), indent=2))

    # --- the gate -----------------------------------------------------------
    def stop_reason(self, projected_usd: float = 0.0) -> str | None:
        """None if a call costing ``projected_usd`` may go ahead, else why not."""
        if self.killed_by_env:
            return "kill switch: VIPUNEN_KILL is set"
        if self.kill_switch_path.exists():
            return f"kill switch: {self.kill_switch_path} exists"
        if self.corrupt:
            return self.corrupt
        if self.max_calls is not None and self.usage.calls >= self.max_calls:
            return f"call cap reached ({self.usage.calls}/{self.max_calls})"
        if self.usage.spend_usd + projected_usd > self.ceiling_usd:
            return (f"spend ceiling: {self.usage.spend_usd:.4f} spent + {projected_usd:.4f} projected"
                    f" > {self.ceiling_usd:g} USD (VIPUNEN_BUDGET_USD)")
        return None

    def authorize(self, model: str, tokens_in_est: int, max_tokens_out: int) -> None:
        """Gate one call at its worst case (full max_tokens) and record it as sent."""
        with self._lock:
            reason = self.stop_reason(cost_usd(model, tokens_in_est, max_tokens_out))
            if reason:
                raise BudgetExceeded(reason)
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.usage.calls += 1
            self.usage.first_call = self.usage.first_call or now
            self.usage.last_call = now
            self._save()

    def record(self, model: str, usage: dict[str, Any]) -> float:
        """Attach the real token usage of a call already authorized. Returns its cost."""
        t_in = int(usage.get("prompt_tokens", 0) or 0)
        t_out = int(usage.get("completion_tokens", 0) or 0)
        cost = cost_usd(model, t_in, t_out)
        with self._lock:
            self.usage.tokens_in += t_in
            self.usage.tokens_out += t_out
            self.usage.spend_usd += cost
            self._save()
        return cost

    def gate(self) -> str | None:
        """For Pohjanakka: may the next run start at all?"""
        return self.stop_reason()

    def status(self) -> str:
        u = self.usage
        return (f"{u.calls} calls, {u.tokens_in}+{u.tokens_out} tokens, "
                f"{u.spend_usd:.4f} / {self.ceiling_usd:g} USD")


def estimate_tokens(text: str) -> int:
    """Rough and generous: ~3 chars per token for Finnish and English alike."""
    return len(text) // 3 + 1
