"""Synthetic-only acceptance for owner-bound Hermes exports."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from skillopt_sleep import harvest_hermes as h
from skillopt_sleep.config import DEFAULTS, SleepConfig
from skillopt_sleep.harvest_sources import harvest_for_config

NOW = datetime.now(timezone.utc).replace(microsecond=0)


@pytest.fixture
def packet(tmp_path):
    base = tmp_path.resolve()
    root = base / "exports"
    root.mkdir()
    project = str(base / "project")
    source = root / "session_one.json"
    manifest = base / "manifest.json"
    data = {"format": "skillopt.hermes.session.v1", "owner": "personal", "project": project,
            "session_id": "one", "session_start": (NOW - timedelta(minutes=3)).isoformat(),
            "last_updated": (NOW - timedelta(minutes=1)).isoformat(), "platform": "cli",
            "messages": [{"role": "user", "content": "Please fix this calculation"},
                         {"role": "assistant", "content": "The answer is 4."}]}
    grant = {"format": "skillopt.hermes.manifest.v1", "owner": "personal", "project": project,
             "sessions_dir": str(root), "generated_at": (NOW - timedelta(seconds=30)).isoformat(),
             "expires_at": (NOW + timedelta(hours=1)).isoformat(), "sources": []}

    def write():
        source.write_text(json.dumps(data))
        grant["sources"] = [{"path": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}]
        manifest.write_text(json.dumps(grant))

    def read(**overrides):
        args = dict(manifest_path=str(manifest), owner="personal", invoked_project=project, now=NOW)
        args.update(overrides)
        return h.harvest_hermes(str(root), **args)

    write()
    return dict(base=base, root=root, source=source, manifest=manifest, data=data, grant=grant, write=write, read=read)


def test_positive_control_and_config_dispatch(packet):
    digest, = packet["read"]()
    assert digest.user_prompts == ["Please fix this calculation"]
    assert digest.n_user_turns == digest.n_assistant_turns == 1
    assert digest.session_id.startswith("hermes-") and len(digest.session_id) == 71
    cfg = SleepConfig({**DEFAULTS, "transcript_source": "hermes", "invoked_project": packet["data"]["project"],
                       "hermes_sessions_dir": str(packet["root"]), "hermes_manifest": str(packet["manifest"]),
                       "hermes_owner": "personal"})
    assert harvest_for_config(cfg) == [digest]


@pytest.mark.parametrize("field,value", [
    ("owner", "byteport"), ("project", "/other-company"), ("format", "native.unqualified"),
    ("session_start", "yesterday"), ("last_updated", "2026-01-01T00:00:00"),
    ("last_updated", "2026-01-01T01:00:00+01:00"), ("last_updated", "2026-02-30T00:00:00Z"),
    ("last_updated", (NOW + timedelta(days=1)).isoformat()),
    ("session_start", (NOW + timedelta(seconds=10)).isoformat()),
    ("messages", {}), ("messages", ["not an object"]), ("session_id", None),
])
def test_invalid_source_denied(packet, field, value):
    packet["data"][field] = value
    packet["write"]()
    with pytest.raises(h.HermesSourceError):
        packet["read"]()


@pytest.mark.parametrize("change", [
    {"owner": "rootly"}, {"project": "/other-project"}, {"sessions_dir": "/other-root"},
    {"expires_at": NOW.isoformat()}, {"generated_at": (NOW + timedelta(seconds=1)).isoformat()},
    {"expires_at": (NOW + timedelta(days=2)).isoformat()}, {"format": "unknown"},
    {"sources": []}, {"sources": [{"path": "../session_other.json", "sha256": "0" * 64}]},
])
def test_invalid_manifest_denied(packet, change):
    packet["grant"].update(change)
    packet["manifest"].write_text(json.dumps(packet["grant"]))
    with pytest.raises(h.HermesSourceError):
        packet["read"]()


def test_missing_authority_and_broad_scope_denied(packet):
    for overrides in ({"owner": ""}, {"owner": "rootly"}, {"scope": "all"}, {"scope": ["/other"]},
                      {"manifest_path": ""}, {"limit": -1}, {"limit": True}, {"since_iso": "yesterday"}):
        with pytest.raises(h.HermesSourceError):
            packet["read"](**overrides)


def test_changed_bytes_malformed_and_duplicate_json_keys_denied(packet):
    packet["source"].write_text("changed")
    with pytest.raises(h.HermesSourceError, match="hash changed"):
        packet["read"]()
    for raw in (b"{bad", b'{"owner":"personal","owner":"rootly"}', b'{"number":NaN}'):
        packet["source"].write_bytes(raw)
        packet["grant"]["sources"][0]["sha256"] = hashlib.sha256(raw).hexdigest()
        packet["manifest"].write_text(json.dumps(packet["grant"]))
        with pytest.raises(h.HermesSourceError, match="JSON"):
            packet["read"]()


@pytest.mark.parametrize("kind", ["file", "directory", "manifest", "parent", "hardlink", "fifo"])
def test_unsafe_files_denied(packet, kind):
    src, root, manifest = packet["source"], packet["root"], packet["manifest"]
    if kind == "file":
        copy = packet["base"] / "elsewhere.json"
        src.rename(copy)
        src.symlink_to(copy)
    elif kind == "directory":
        moved = root.with_name("moved")
        root.rename(moved)
        root.symlink_to(moved, target_is_directory=True)
    elif kind == "manifest":
        moved = manifest.with_name("actual.json")
        manifest.rename(moved)
        manifest.symlink_to(moved)
    elif kind == "parent":
        alias = packet["base"] / "alias"
        alias.symlink_to(packet["base"], target_is_directory=True)
        with pytest.raises(h.HermesSourceError):
            packet["read"](manifest_path=str(alias / manifest.name))
        return
    elif kind == "hardlink":
        os.link(src, packet["base"] / "linked.json")
    else:
        src.unlink()
        os.mkfifo(src)
    with pytest.raises(h.HermesSourceError):
        packet["read"]()


def test_limits_and_unlisted_files_not_read(packet, monkeypatch):
    (packet["root"] / "session_unlisted.json").symlink_to("/definitely/not/accessible")
    assert len(packet["read"]()) == 1
    monkeypatch.setattr(h, "MAX_TOTAL_BYTES", 10)
    with pytest.raises(h.HermesSourceError, match="batch"):
        packet["read"]()


@pytest.mark.parametrize("kind", ["file", "manifest", "sources", "messages", "message_text", "digest_text"])
def test_bounds(packet, kind):
    if kind == "file":
        packet["source"].write_bytes(b" " * (h.MAX_SOURCE_BYTES + 1))
    elif kind == "manifest":
        packet["manifest"].write_bytes(b" " * (h.MAX_MANIFEST_BYTES + 1))
    elif kind == "sources":
        packet["grant"]["sources"] *= h.MAX_SOURCES + 1
        packet["manifest"].write_text(json.dumps(packet["grant"]))
    else:
        msg = {"role": "user", "content": "x"}
        if kind == "messages":
            packet["data"]["messages"] = [msg] * (h.MAX_MESSAGES + 1)
        elif kind == "message_text":
            packet["data"]["messages"] = [{**msg, "content": "x" * (h.MAX_TEXT_CHARS + 1)}]
        else:
            packet["data"]["messages"] = [{**msg, "content": "x" * h.MAX_TEXT_CHARS}] * 5
        packet["write"]()
    with pytest.raises(h.HermesSourceError):
        packet["read"]()


def test_limit_does_not_hide_invalid_later_source(packet):
    packet["grant"]["sources"].append({"path": "session_missing.json", "sha256": "0" * 64})
    packet["manifest"].write_text(json.dumps(packet["grant"]))
    with pytest.raises(h.HermesSourceError):
        packet["read"](limit=1)


def test_deduplication_and_since_are_deterministic(packet):
    assert packet["read"](since_iso=NOW.isoformat()) == []
    packet["grant"]["sources"] *= 2
    packet["manifest"].write_text(json.dumps(packet["grant"]))
    with pytest.raises(h.HermesSourceError, match="duplicated"):
        packet["read"]()


def test_metadata_and_text_secrets_never_persist(packet):
    secret = "sk-" + "A" * 40
    data = packet["data"]
    data.update(session_id="../../" + secret, model=secret, system_prompt=secret, title=secret)
    data["messages"] = [
        {"role": "system", "content": secret}, {"role": "tool", "content": secret},
        {"role": "user", "content": "api_key=" + secret},
        {"role": "assistant", "content": "fixed", "tool_calls": [
            {"function": {"name": "search", "arguments": secret}}, {"function": {"name": secret}}]},
    ]
    packet["write"]()
    digest, = packet["read"]()
    serialized = json.dumps(digest.to_dict())
    assert secret not in serialized and "../../" not in serialized and str(packet["source"]) not in serialized
    assert "REDACTED" in serialized and digest.tools_used == ["search"]
    assert digest.raw_path.startswith("hermes:sha256:")


def test_cron_excluded_and_environment_cannot_enable_it(packet, monkeypatch):
    monkeypatch.setenv("HERMES_INCLUDE_CRON", "1")
    packet["data"]["platform"] = "cron"
    packet["write"]()
    assert packet["read"]() == []


def test_malformed_text_blocks_denied(packet):
    packet["data"]["messages"] = [{"role": "user", "content": [{"type": "text", "text": {"secret": "value"}}]}]
    packet["write"]()
    with pytest.raises(h.HermesSourceError, match="text blocks"):
        packet["read"]()


def test_unsupported_safe_access_denied(packet, monkeypatch):
    monkeypatch.setattr(os, "supports_dir_fd", set())
    with pytest.raises(h.HermesSourceError, match="unsupported"):
        packet["read"]()


@pytest.mark.parametrize("kind", ["unicode", "tool_names"])
def test_text_and_metadata_bounds(packet, kind):
    if kind == "unicode":
        packet["data"]["messages"][0]["content"] = "\ud800"
    else:
        packet["data"]["messages"][1]["tool_calls"] = [
            {"function": {"name": "tool_" + str(i)}} for i in range(h.MAX_TOOL_NAMES + 1)
        ]
    packet["write"]()
    with pytest.raises(h.HermesSourceError):
        packet["read"]()


def test_cli_requires_explicit_contract(packet, capsys):
    from skillopt_sleep.__main__ import main
    assert main(["harvest", "--source", "hermes", "--project", packet["data"]["project"],
                 "--hermes-sessions-dir", str(packet["root"]), "--hermes-manifest", str(packet["manifest"]),
                 "--hermes-owner", "personal", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["transcript_source"] == "hermes" and payload["n_sessions"] == 1
    assert payload["tasks"][0]["source_sessions"][0].startswith("hermes-")
