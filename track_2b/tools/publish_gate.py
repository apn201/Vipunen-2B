"""Publish gate for the PUBLIC 2B repo. Exit 1 blocks the push.

Scans every commit on every ref, plus the files about to be committed (tracked
and untracked-but-not-ignored), for:

- paths that must never be public: private/, .env, mask maps, evidence,
  the operator's mutations.yaml
- secret-shaped strings: HF / OpenAI-style tokens, private keys, KEY=value lines
- the literal values of the keys in the local .env files

Hits are reported by location only; the secret itself is never printed.

S1: the scan above. S9 adds: verbatim text whose hash appears in var/ledger.jsonl.
Later ported in part from Whyf check_secrets.py + check_publishable.py.

    python tools/publish_gate.py            # from track_2b/
    python tools/publish_gate.py --install  # add as the repo's pre-push hook
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

TRACK = Path(__file__).resolve().parents[1]
# Track .env, plus the shared workspace .env three levels up when it exists (dev only).
ENV_FILES = [TRACK / ".env"] + ([TRACK.parents[2] / ".env"] if len(TRACK.parents) > 2 else [])
SECRET_ENV_NAMES = ("LLM_API_KEY", "PUBLICAI_API_KEY", "HF_TOKEN")

FORBIDDEN_PATHS = [
    (re.compile(r"(^|/)private/"), "private/ tree"),
    (re.compile(r"(^|/)\.env(\.(?!example$)[^/]*)?$"), ".env file"),
    (re.compile(r"(^|/)masks?/(?!\.gitkeep$)"), "mask map"),
    (re.compile(r"(^|/)evidence/(?!\.gitkeep$)"), "verbatim evidence"),
    (re.compile(r"\.verbatim\.json$"), "verbatim evidence"),
    (re.compile(r"(^|/)mutations\.ya?ml$"), "operator chain (only *.example.yaml may ship)"),
]

SECRET_PATTERNS = [
    (re.compile(r"hf_[A-Za-z0-9]{30,}"), "Hugging Face token"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{20,}"), "sk- style API key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
    (re.compile(r"\b[A-Z0-9_]*(API_KEY|TOKEN|SECRET)[ \t]*[=:][ \t]*['\"]?[A-Za-z0-9_\-.]{16,}"),
     "KEY=value assignment"),
]


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout


def known_secret_values() -> list[str]:
    values = {os.environ.get(n, "") for n in SECRET_ENV_NAMES}
    for f in ENV_FILES:
        if f.is_file():
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                key, sep, value = line.partition("=")
                if sep and key.strip() in SECRET_ENV_NAMES:
                    values.add(value.strip().strip("'\""))
    return sorted(v for v in values if len(v) >= 8)


def scan_paths(paths: list[str]) -> list[str]:
    return [f"{p}: {why}" for p in paths for rx, why in FORBIDDEN_PATHS if rx.search(p)]


def scan_text(where: str, text: str, literals: list[str]) -> list[str]:
    hits = [f"{where}: {why}" for rx, why in SECRET_PATTERNS if rx.search(text)]
    hits += [f"{where}: value of a key from .env" for v in literals if v in text]
    return hits


def scan_repo(root: Path) -> list[str]:
    literals = known_secret_values()
    hits: list[str] = []

    history_paths = git("log", "--all", "--name-only", "--format=", cwd=root).split()
    pending = git("ls-files", "--cached", "--others", "--exclude-standard", cwd=root).splitlines()
    hits += scan_paths(sorted(set(history_paths) | set(pending)))

    for commit in git("rev-list", "--all", cwd=root).split():
        patch = git("show", "--format=", "--no-color", "-p", commit, cwd=root)
        hits += scan_text(f"commit {commit[:10]}", patch, literals)
    for path in pending:
        f = root / path
        if f.is_file():
            hits += scan_text(path, f.read_text(encoding="utf-8", errors="replace"), literals)
    return sorted(set(hits))


def install_hook(root: Path) -> None:
    hook = root / ".git" / "hooks" / "pre-push"
    rel = (TRACK / "tools" / "publish_gate.py").relative_to(root).as_posix()
    hook.write_text(f'#!/bin/sh\nexec python "{rel}"\n', encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    print(f"installed {hook}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", action="store_true", help="install as pre-push hook")
    args = ap.parse_args()
    root = Path(git("rev-parse", "--show-toplevel", cwd=TRACK).strip())
    if args.install:
        install_hook(root)
        return 0
    hits = scan_repo(root)
    for h in hits:
        print(f"BLOCKED {h}", file=sys.stderr)
    print(f"publish_gate: {'FAIL' if hits else 'clean'} ({len(hits)} issue(s))")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
