"""Tests for the pi (pi-coding-agent) transcript harvester."""
from __future__ import annotations

import json
import os

from skillopt_sleep.harvest_pi import _redact_secrets, digest_pi_session, harvest_pi


def _write_session(tmp_path, slug, name, entries):
    d = tmp_path / slug
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.jsonl"
    with open(p, "w") as f:
        for rec in entries:
            f.write(json.dumps(rec) + "\n")
    return str(p)


PI_SESSION = [
    {"type": "session", "version": 3, "id": "s1", "timestamp": "2026-06-23T11:52:04.333Z", "cwd": "/home/u/proj"},
    {"type": "model_change", "id": "m", "parentId": None, "timestamp": "2026-06-23T11:52:05.000Z", "modelId": "gpt-x"},
    {"type": "message", "id": "a1", "parentId": "m", "timestamp": "2026-06-23T11:52:06.000Z",
     "message": {"role": "user", "content": [{"type": "text", "text": "fix the failing tests"}]}},
    {"type": "message", "id": "a2", "parentId": "a1", "timestamp": "2026-06-23T11:52:07.000Z",
     "message": {"role": "assistant", "content": [
         {"type": "thinking", "thinking": "private reasoning"},
         {"type": "text", "text": "Running the suite now."},
         {"type": "toolCall", "id": "call_1", "name": "bash", "arguments": {"command": "pytest"}},
     ]}},
    {"type": "message", "id": "a3", "parentId": "a2", "timestamp": "2026-06-23T11:52:08.000Z",
     "message": {"role": "toolResult", "toolCallId": "call_1", "toolName": "bash",
                 "content": [{"type": "text", "text": "1 failed"}], "isError": True}},
    {"type": "message", "id": "a4", "parentId": "a3", "timestamp": "2026-06-23T11:52:30.000Z",
     "message": {"role": "user", "content": "thanks, that works now"}},
    {"type": "message", "id": "a5", "parentId": "a4", "timestamp": "2026-06-23T11:52:31.000Z",
     "message": {"role": "assistant", "content": [{"type": "text", "text": "Glad it's fixed."}]}},
]


def test_digest_extracts_fields():
    d = digest_pi_session("/tmp/abc-123.jsonl")  # missing file -> None
    assert d is None


def test_digest_full_session(tmp_path):
    p = _write_session(tmp_path, "--home-u-proj", "abc-123", PI_SESSION)
    d = digest_pi_session(p)
    assert d is not None
    assert d.project == "/home/u/proj"
    assert d.n_user_turns == 2
    assert d.n_assistant_turns == 2
    assert "bash" in d.tools_used                 # from toolCall block
    assert any("works" in f for f in d.feedback_signals)   # pos feedback
    assert all(
        not f.startswith("neg:tool_error") for f in d.feedback_signals
    )  # isError deliberately NOT surfaced (recovered errors ≠ task failure)
    assert all("private reasoning" not in f for f in d.feedback_signals)
    # thinking blocks must not leak into finals
    assert "private reasoning" not in " ".join(d.assistant_finals)
    assert d.started_at.startswith("2026-06-23T11:52:04")
    assert d.ended_at.startswith("2026-06-23T11:52:31")


def test_harvest_scope_filter(tmp_path):
    _write_session(tmp_path, "--home-u-proj", "abc-123", PI_SESSION)
    other = list(PI_SESSION)
    other[0] = dict(PI_SESSION[0], cwd="/other/place")
    _write_session(tmp_path, "--other", "xyz", other)

    all_scope = harvest_pi(str(tmp_path), scope="all")
    assert len(all_scope) == 2
    invoked = harvest_pi(str(tmp_path), scope="invoked", invoked_project="/home/u/proj")
    assert len(invoked) == 1
    assert invoked[0].project == "/home/u/proj"


def test_harvest_skips_session_removed_during_discovery(tmp_path, monkeypatch):
    p = _write_session(tmp_path, "vanishing-project", "vanishing", PI_SESSION)
    real_getmtime = os.path.getmtime

    def remove_then_stat(path):
        if os.fspath(path) == os.fspath(p):
            os.unlink(path)
        return real_getmtime(path)

    monkeypatch.setattr(os.path, "getmtime", remove_then_stat)

    assert harvest_pi(str(tmp_path), scope="all") == []


def test_secret_redaction():
    out = _redact_secrets("Authorization: Bearer sk-1234567890abcdefghij")
    assert "sk-1234567890abcdefghij" not in out
    assert "[REDACTED_OPENAI_KEY]" in out


def test_shared_secret_redaction_covers_github_tokens():
    token = "ghp_1234567890abcdefghijklmnop"
    out = _redact_secrets(f"token leaked in output: {token}")
    assert token not in out
    assert "[REDACTED_GITHUB_TOKEN]" in out


def test_v1_session_remains_linear(tmp_path):
    entries = [
        {"type": "session", "version": 1, "id": "v1", "timestamp": "2026-06-23T12:00:00Z", "cwd": "/v1/project"},
        {"type": "message", "timestamp": "2026-06-23T12:00:01Z", "message": {"role": "user", "content": "first request"}},
        {"type": "message", "timestamp": "2026-06-23T12:00:02Z", "message": {"role": "assistant", "content": "first answer"}},
        {"type": "message", "timestamp": "2026-06-23T12:00:03Z", "message": {"role": "user", "content": "second request"}},
        {"type": "message", "timestamp": "2026-06-23T12:00:04Z", "message": {"role": "assistant", "content": "second answer"}},
    ]
    p = _write_session(tmp_path, "v1-project", "linear", entries)

    d = digest_pi_session(p)

    assert d is not None
    assert d.user_prompts == ["first request", "second request"]
    assert d.assistant_finals == ["first answer", "second answer"]


def test_unknown_session_version_fails_closed(tmp_path):
    entries = [
        {"type": "session", "version": 4, "id": "future", "timestamp": "2026-06-23T12:00:00Z", "cwd": "/future/project"},
        {"type": "message", "id": "u", "parentId": None, "timestamp": "2026-06-23T12:00:01Z", "message": {"role": "user", "content": "future request"}},
        {"type": "message", "id": "a", "parentId": "u", "timestamp": "2026-06-23T12:00:02Z", "message": {"role": "assistant", "content": "future answer"}},
    ]
    p = _write_session(tmp_path, "future-project", "unknown", entries)

    assert digest_pi_session(p) is None


def test_v3_harvests_only_the_active_branch(tmp_path):
    entries = [
        {"type": "session", "version": 3, "id": "session", "timestamp": "2026-06-23T12:00:00Z", "cwd": "/branch/project"},
        {"type": "message", "id": "root", "parentId": None, "timestamp": "2026-06-23T12:00:01Z", "message": {"role": "user", "content": "shared request"}},
        {"type": "message", "id": "old-user", "parentId": "root", "timestamp": "2026-06-23T12:00:02Z", "message": {"role": "user", "content": "abandoned prompt, this is wrong"}},
        {"type": "message", "id": "old-answer", "parentId": "old-user", "timestamp": "2026-06-23T12:00:03Z", "message": {"role": "assistant", "content": [{"type": "text", "text": "abandoned final"}, {"type": "toolCall", "name": "abandoned/tool"}]}},
        {"type": "message", "id": "old-result", "parentId": "old-answer", "timestamp": "2026-06-23T12:00:04Z", "message": {"role": "toolResult", "toolName": "abandoned result", "content": "old output"}},
        {"type": "message", "id": "new-user", "parentId": "root", "timestamp": "2026-06-23T12:00:05Z", "message": {"role": "user", "content": "active prompt"}},
        {"type": "message", "id": "new-answer", "parentId": "new-user", "timestamp": "2026-06-23T12:00:06Z", "message": {"role": "assistant", "content": [{"type": "text", "text": "active final"}, {"type": "toolCall", "name": "active-tool"}]}},
    ]
    p = _write_session(tmp_path, "branch-project", "branching", entries)

    d = digest_pi_session(p)

    assert d is not None
    assert d.user_prompts == ["shared request", "active prompt"]
    assert d.assistant_finals == ["active final"]
    assert d.tools_used == ["active-tool"]
    assert not d.feedback_signals
    assert d.started_at == "2026-06-23T12:00:00Z"
    assert d.ended_at == "2026-06-23T12:00:06Z"


def test_v3_cycle_fails_closed(tmp_path):
    entries = [
        {"type": "session", "version": 3, "id": "cycle", "timestamp": "2026-06-23T12:00:00Z", "cwd": "/cycle/project"},
        {"type": "message", "id": "a", "parentId": "b", "timestamp": "2026-06-23T12:00:01Z", "message": {"role": "user", "content": "cycle request"}},
        {"type": "message", "id": "b", "parentId": "a", "timestamp": "2026-06-23T12:00:10Z", "message": {"role": "assistant", "content": "cycle answer"}},
    ]
    p = _write_session(tmp_path, "cycle-project", "cycle", entries)

    assert digest_pi_session(p) is None


def test_v3_missing_parent_fails_closed(tmp_path):
    entries = [
        {"type": "session", "version": 3, "id": "missing", "timestamp": "2026-06-23T12:00:00Z", "cwd": "/missing/project"},
        {"type": "message", "id": "unrelated", "parentId": None, "timestamp": "2026-06-23T12:00:01Z", "message": {"role": "user", "content": "abandoned prompt"}},
        {"type": "message", "id": "reachable-user", "parentId": "missing", "timestamp": "2026-06-23T12:00:05Z", "message": {"role": "user", "content": "reachable request"}},
        {"type": "message", "id": "leaf", "parentId": "reachable-user", "timestamp": "2026-06-23T12:00:10Z", "message": {"role": "assistant", "content": "reachable answer"}},
    ]
    p = _write_session(tmp_path, "missing-project", "missing", entries)

    assert digest_pi_session(p) is None


def test_v2_harvests_only_the_active_branch(tmp_path):
    entries = [
        {"type": "session", "version": 2, "id": "v2", "timestamp": "2026-06-23T12:00:00Z", "cwd": "/v2/project"},
        {"type": "message", "id": "root", "parentId": None, "timestamp": "2026-06-23T12:00:01Z", "message": {"role": "user", "content": "shared"}},
        {"type": "message", "id": "old", "parentId": "root", "timestamp": "2026-06-23T12:00:02Z", "message": {"role": "assistant", "content": "abandoned"}},
        {"type": "message", "id": "active", "parentId": "root", "timestamp": "2026-06-23T12:00:05Z", "message": {"role": "assistant", "content": "active"}},
    ]
    p = _write_session(tmp_path, "v2-project", "branching", entries)

    d = digest_pi_session(p)

    assert d is not None
    assert d.user_prompts == ["shared"]
    assert d.assistant_finals == ["active"]


def test_tree_session_requires_first_unique_header(tmp_path):
    misplaced = [
        {"type": "message", "id": "u", "parentId": None, "message": {"role": "user", "content": "request"}},
        {"type": "session", "version": 3, "id": "misplaced", "cwd": "/bad/project"},
    ]
    duplicate = [PI_SESSION[0], dict(PI_SESSION[0]), *PI_SESSION[1:]]

    p1 = _write_session(tmp_path, "bad-project", "misplaced", misplaced)
    p2 = _write_session(tmp_path, "bad-project", "duplicate", duplicate)

    assert digest_pi_session(p1) is None
    assert digest_pi_session(p2) is None


def test_tree_session_rejects_invalid_ids_and_parents(tmp_path):
    base_header = {"type": "session", "version": 3, "id": "bad", "cwd": "/bad/project"}
    cases = {
        "missing-id": [{"type": "message", "parentId": None, "message": {"role": "user", "content": "request"}}],
        "duplicate-id": [
            {"type": "message", "id": "same", "parentId": None, "message": {"role": "user", "content": "request"}},
            {"type": "message", "id": "same", "parentId": None, "message": {"role": "assistant", "content": "answer"}},
        ],
        "missing-parent-field": [{"type": "message", "id": "u", "message": {"role": "user", "content": "request"}}],
        "empty-parent": [{"type": "message", "id": "u", "parentId": "", "message": {"role": "user", "content": "request"}}],
        "non-string-parent": [{"type": "message", "id": "u", "parentId": 7, "message": {"role": "user", "content": "request"}}],
    }

    for name, body in cases.items():
        p = _write_session(tmp_path, f"bad-{name}", name, [base_header, *body])
        assert digest_pi_session(p) is None, name


def test_tree_session_rejects_complete_forward_reference(tmp_path):
    entries = [
        {"type": "session", "version": 3, "id": "forward", "cwd": "/forward/project"},
        {"type": "message", "id": "old-child", "parentId": "old-root", "message": {"role": "assistant", "content": "abandoned answer"}},
        {"type": "message", "id": "old-root", "parentId": None, "message": {"role": "user", "content": "abandoned request"}},
        {"type": "message", "id": "active-root", "parentId": None, "message": {"role": "user", "content": "active request"}},
        {"type": "message", "id": "active-leaf", "parentId": "active-root", "message": {"role": "assistant", "content": "active answer"}},
    ]
    p = _write_session(tmp_path, "forward-project", "forward", entries)

    assert digest_pi_session(p) is None


def test_corrupt_abandoned_branch_fails_closed(tmp_path):
    header = {"type": "session", "version": 3, "id": "corrupt", "cwd": "/corrupt/project"}
    active = [
        {"type": "message", "id": "active-root", "parentId": None, "message": {"role": "user", "content": "active request"}},
        {"type": "message", "id": "active-leaf", "parentId": "active-root", "message": {"role": "assistant", "content": "active answer"}},
    ]
    corrupt_branches = {
        "cycle": [
            {"type": "message", "id": "bad-a", "parentId": "bad-b", "message": {"role": "user", "content": "abandoned"}},
            {"type": "message", "id": "bad-b", "parentId": "bad-a", "message": {"role": "assistant", "content": "abandoned"}},
        ],
        "missing-parent": [
            {"type": "message", "id": "bad-child", "parentId": "absent", "message": {"role": "assistant", "content": "abandoned"}},
        ],
    }

    for name, branch in corrupt_branches.items():
        p = _write_session(
            tmp_path,
            f"corrupt-{name}",
            name,
            [header, *branch, *active],
        )
        assert digest_pi_session(p) is None, name


def test_legacy_session_without_version_remains_linear(tmp_path):
    entries = [
        {"type": "session", "id": "legacy", "cwd": "/legacy/project"},
        {"type": "message", "message": {"role": "user", "content": "request"}},
        {"type": "message", "message": {"role": "assistant", "content": "answer"}},
    ]
    p = _write_session(tmp_path, "legacy-project", "legacy", entries)

    d = digest_pi_session(p)

    assert d is not None
    assert d.user_prompts == ["request"]
    assert d.assistant_finals == ["answer"]


def test_explicit_session_version_requires_supported_integer(tmp_path):
    body = [
        {"type": "message", "id": "u", "parentId": None, "message": {"role": "user", "content": "request"}},
        {"type": "message", "id": "a", "parentId": "u", "message": {"role": "assistant", "content": "answer"}},
    ]
    for name, version in (("null", None), ("bool", True), ("float", 3.0), ("string", "3")):
        header = {"type": "session", "version": version, "id": name, "cwd": "/bad/project"}
        p = _write_session(tmp_path, f"bad-version-{name}", name, [header, *body])
        assert digest_pi_session(p) is None, name


def test_malformed_final_jsonl_line_fails_closed(tmp_path):
    p = tmp_path / "truncated.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for record in PI_SESSION:
            f.write(json.dumps(record) + "\n")
        f.write('{"type":"message","id":"new-active-leaf"')

    assert digest_pi_session(str(p)) is None


def test_malformed_middle_jsonl_line_fails_closed(tmp_path):
    p = tmp_path / "corrupt-middle.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps(PI_SESSION[0]) + "\n")
        f.write("not-json\n")
        for record in PI_SESSION[1:]:
            f.write(json.dumps(record) + "\n")

    assert digest_pi_session(str(p)) is None


def test_non_object_jsonl_record_fails_closed(tmp_path):
    p = tmp_path / "non-object.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps(PI_SESSION[0]) + "\n")
        f.write("[]\n")
        for record in PI_SESSION[1:]:
            f.write(json.dumps(record) + "\n")

    assert digest_pi_session(str(p)) is None


def test_blank_jsonl_lines_remain_valid(tmp_path):
    p = tmp_path / "blank-lines.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n")
        for record in PI_SESSION:
            f.write(json.dumps(record) + "\n\n")

    digest = digest_pi_session(str(p))

    assert digest is not None
    assert digest.user_prompts == ["fix the failing tests", "thanks, that works now"]


def test_tree_session_allows_multiple_roots(tmp_path):
    entries = [
        {"type": "session", "version": 3, "id": "roots", "cwd": "/roots/project"},
        {"type": "message", "id": "old-root", "parentId": None, "message": {"role": "user", "content": "abandoned"}},
        {"type": "message", "id": "new-root", "parentId": None, "message": {"role": "user", "content": "active request"}},
        {"type": "message", "id": "leaf", "parentId": "new-root", "message": {"role": "assistant", "content": "active answer"}},
    ]
    p = _write_session(tmp_path, "roots-project", "roots", entries)

    d = digest_pi_session(p)

    assert d is not None
    assert d.user_prompts == ["active request"]
    assert d.assistant_finals == ["active answer"]


def test_tool_call_name_is_sanitized(tmp_path):
    entries = [
        {"type": "session", "version": 1, "id": "tool-call", "timestamp": "2026-06-23T12:00:00Z", "cwd": "/tool/project"},
        {"type": "message", "timestamp": "2026-06-23T12:00:01Z", "message": {"role": "user", "content": "run a tool"}},
        {"type": "message", "timestamp": "2026-06-23T12:00:10Z", "message": {"role": "assistant", "content": [{"type": "toolCall", "name": "bad tool/<arg>"}]}},
    ]
    p = _write_session(tmp_path, "tool-project", "tool-call", entries)

    d = digest_pi_session(p)

    assert d is not None
    assert d.tools_used == ["bad_tool_arg_"]


def test_tool_result_name_is_sanitized(tmp_path):
    entries = [
        {"type": "session", "version": 1, "id": "tool-result", "timestamp": "2026-06-23T12:00:00Z", "cwd": "/tool/project"},
        {"type": "message", "timestamp": "2026-06-23T12:00:01Z", "message": {"role": "user", "content": "run a tool"}},
        {"type": "message", "timestamp": "2026-06-23T12:00:10Z", "message": {"role": "toolResult", "toolName": "bad result/<arg>", "content": "done"}},
    ]
    p = _write_session(tmp_path, "tool-project", "tool-result", entries)

    d = digest_pi_session(p)

    assert d is not None
    assert d.tools_used == ["bad_result_arg_"]
