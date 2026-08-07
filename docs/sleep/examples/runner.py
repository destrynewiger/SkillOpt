#!/usr/bin/env python3
"""Reference launcher for running SkillOpt-Sleep against an OpenAI-compatible
endpoint (DeepSeek shown here), plus an Antigravity `session-end` hook.

This is a *sanitized example*, not a supported entry point. Adapt the paths and
provider details to your environment. No API keys are hardcoded — the key is read
from an .env file or the process environment.

Usage:
    python runner.py run           # run a full sleep cycle against DeepSeek
    python runner.py dry-run       # harvest + replay, report only
    python runner.py session-end   # Antigravity Stop-hook: append rollout evidence
"""
import os
import re
import sys
import json
import subprocess
import datetime
from pathlib import Path

# --- Configure these for your environment -----------------------------------
# Path to a file containing your provider key as `sk-...` (kept out of source).
PROVIDER_ENV_FILE = Path(os.environ.get("SKILLOPT_PROVIDER_ENV_FILE", "provider.env"))
# Endpoint + model for the OpenAI-compatible provider.
PROVIDER_ENDPOINT = os.environ.get("SKILLOPT_PROVIDER_ENDPOINT", "https://api.deepseek.com")
PROVIDER_MODEL = os.environ.get("SKILLOPT_PROVIDER_MODEL", "deepseek-v4-pro")
# Project whose SKILL.md files the sleep cycle should evolve.
PROJECT_DIR = os.environ.get("SKILLOPT_PROJECT_DIR", os.getcwd())
# Where the session-end hook appends rollout evidence.
ROLLOUT_LOG = Path(os.environ.get("SKILLOPT_ROLLOUT_LOG", "brain/rollout-evidence.jsonl"))
# ----------------------------------------------------------------------------


def load_provider_key(env: dict) -> None:
    """Ensure DEEPSEEK_API_KEY is set, reading it from PROVIDER_ENV_FILE if needed."""
    if env.get("DEEPSEEK_API_KEY"):
        return
    try:
        text = PROVIDER_ENV_FILE.read_text(encoding="utf-8")
    except OSError:
        return
    m = re.search(r"sk-[A-Za-z0-9]+", text)
    if m:
        env["DEEPSEEK_API_KEY"] = m.group(0)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: runner.py [dry-run|run|status|adopt|session-end]")
        sys.exit(1)

    command = sys.argv[1]

    # Antigravity Stop-hook: enrich future nights with task-outcome metadata.
    if command == "session-end":
        ROLLOUT_LOG.parent.mkdir(parents=True, exist_ok=True)
        outcome = {
            "timestamp": datetime.datetime.now().isoformat(),
            "event": "SessionEnd",
            "metadata": "Appended task outcome metadata",
        }
        with open(ROLLOUT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(outcome) + "\n")
        print("Rollout evidence metadata appended.")
        return

    env = os.environ.copy()
    load_provider_key(env)

    if env.get("DEEPSEEK_API_KEY"):
        # OpenAI-compatible path — see docs/sleep/openai-compatible-endpoints.md
        backend = "azure_openai"
        env["PYTHONIOENCODING"] = "utf-8"
        env["AZURE_OPENAI_AUTH_MODE"] = "openai_compatible"
        env["AZURE_OPENAI_ENDPOINT"] = PROVIDER_ENDPOINT
        env["AZURE_OPENAI_API_KEY"] = env["DEEPSEEK_API_KEY"]
        # Provider-specific request fields are opt-in, never inferred from the
        # model name. For DeepSeek reasoning models, enable the thinking channel:
        env.setdefault("SKILLOPT_SLEEP_CHAT_EXTRA_BODY",
                       json.dumps({"thinking": {"type": "enabled"}}))
        env.setdefault("SKILLOPT_SLEEP_COMPAT_MAX_TOKENS", "8192")
    else:
        # OPTIONAL, UNVERIFIED fallback: route the `claude` CLI backend through a
        # local Anthropic-compatible proxy (e.g. LiteLLM) to reach Gemini. There
        # is no native Gemini backend; this path was not validated. See the doc.
        backend = "claude"
        if "ANTHROPIC_API_KEY" not in env and "GEMINI_API_KEY" in env:
            env["ANTHROPIC_API_KEY"] = env["GEMINI_API_KEY"]
        env.setdefault("ANTHROPIC_BASE_URL", "http://127.0.0.1:4000")

    args = ["skillopt-sleep", command]
    if command in ("run", "dry-run"):
        args = ["skillopt-sleep", command, "--backend", backend,
                "--model", PROVIDER_MODEL, "--project", PROJECT_DIR]

    print(f"Running: {' '.join(args)}")
    # Propagate the child's exit code so supervisors (watchdog.py, systemd,
    # Task Scheduler) see a failed sleep run as a failure, not a success.
    proc = subprocess.run(args, env=env, check=False)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
