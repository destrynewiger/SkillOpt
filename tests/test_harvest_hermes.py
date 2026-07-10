"""Tests for the Hermes session harvester."""
from __future__ import annotations

import json
import os

from skillopt_sleep.harvest_hermes import (
    _strip_leading_bracket_block,
    digest_hermes_session,
    harvest_hermes,
)

CRON_PREAMBLE = (
    "[IMPORTANT: You are running as a scheduled cron job. DELIVERY: deliver "
    "automatically. SILENT: respond with exactly [SILENT] if nothing new. "
    "Never combine [SILENT] with content.] Summarize today's Shopify orders."
)


def _write_session(dirpath, name, *, platform, messages, sid=None, start="2026-05-20T10:00:00"):
    payload = {
        "session_id": sid or name,
        "model": "claude",
        "platform": platform,
        "session_start": start,
        "last_updated": start,
        "system_prompt": "SECRET SYSTEM PROMPT — never harvested",
        "tools": ["terminal", {"name": "send_message"}],
        "message_count": len(messages),
        "messages": messages,
    }
    path = os.path.join(dirpath, f"session_{name}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return path


def test_strip_leading_bracket_block_handles_nested_brackets():
    out = _strip_leading_bracket_block(CRON_PREAMBLE)
    assert out == "Summarize today's Shopify orders."
    # No leading bracket → returned unchanged (trimmed).
    assert _strip_leading_bracket_block("just a normal ask") == "just a normal ask"
    # Truncated/unbalanced preamble → nothing usable.
    assert _strip_leading_bracket_block("[IMPORTANT: cut off with no close") == ""


def test_digest_extracts_user_assistant_and_tools(tmp_path):
    path = _write_session(
        tmp_path, "cli_1", platform="cli",
        messages=[
            {"role": "user", "content": "fix my phone number setup"},
            {"role": "assistant", "content": "done", "tool_calls": [
                {"function": {"name": "terminal"}},
            ]},
            {"role": "tool", "name": "terminal", "content": "ok"},
        ],
    )
    d = digest_hermes_session(path, project="/proj")
    assert d is not None
    assert d.project == "/proj"
    assert d.user_prompts == ["fix my phone number setup"]
    assert d.assistant_finals == ["done"]
    assert "terminal" in d.tools_used
    assert d.n_user_turns == 1 and d.n_assistant_turns == 1
    # System prompt is never copied into any harvested field.
    assert all("SECRET SYSTEM PROMPT" not in p for p in d.user_prompts)


def test_cron_sessions_skipped_by_default_but_optin_works(tmp_path):
    _write_session(
        tmp_path, "cron_a", platform="cron", sid="cron_abc_1",
        messages=[{"role": "user", "content": CRON_PREAMBLE}],
    )
    _write_session(
        tmp_path, "human_b", platform="telegram", sid="20260520_x",
        messages=[{"role": "user", "content": "what's my calendar today?"}],
    )
    default = harvest_hermes(str(tmp_path), invoked_project="/proj")
    assert len(default) == 1
    assert default[0].user_prompts == ["what's my calendar today?"]

    with_cron = harvest_hermes(str(tmp_path), invoked_project="/proj", include_cron=True)
    assert len(with_cron) == 2
    # The cron preamble is stripped, leaving the real instruction.
    cron_digest = next(d for d in with_cron if d.session_id.startswith("cron"))
    assert cron_digest.user_prompts == ["Summarize today's Shopify orders."]


def test_since_iso_filters_old_sessions(tmp_path):
    _write_session(
        tmp_path, "old", platform="cli", start="2026-01-01T00:00:00",
        messages=[{"role": "user", "content": "old task"}],
    )
    _write_session(
        tmp_path, "new", platform="cli", start="2026-06-01T00:00:00",
        messages=[{"role": "user", "content": "new task"}],
    )
    recent = harvest_hermes(str(tmp_path), invoked_project="/proj", since_iso="2026-03-01T00:00:00")
    assert [d.user_prompts[0] for d in recent] == ["new task"]


def test_missing_dir_returns_empty():
    assert harvest_hermes("/nonexistent/hermes/sessions") == []
