"""Offline regressions for recovery after failed model calls and interrupted pins."""
from __future__ import annotations

import stat
from types import SimpleNamespace
from unittest import mock

import pytest

from skillopt_sleep import __main__ as cli
from skillopt_sleep.backend import ClaudeCliBackend
from skillopt_sleep.config import DEFAULTS, SleepConfig
from skillopt_sleep.handoff_backend import HandoffBackend
from skillopt_sleep.types import SessionDigest, TaskRecord


@pytest.mark.parametrize("with_tools", [False, True])
def test_claude_failure_then_success_and_auth_failure(with_tools):
    backend = ClaudeCliBackend(claude_path="nonexistent-synthetic-claude")
    task = TaskRecord(id="synthetic", project="/synthetic", intent="Compute 2+2")
    def call():
        return backend.attempt_with_tools(task, "", "", ["search"])[0] if with_tools else backend._call("synthetic")
    with mock.patch("skillopt_sleep.backend.subprocess.run", side_effect=FileNotFoundError("synthetic")):
        assert call() == "" and backend.last_call_error
    with mock.patch("skillopt_sleep.backend.subprocess.run", return_value=SimpleNamespace(stdout="4", stderr="")):
        assert call() == "4" and backend.last_call_error == ""
    with mock.patch("skillopt_sleep.backend.subprocess.run", return_value=SimpleNamespace(stdout="", stderr="Not logged in")):
        call()
        assert backend.last_call_error
    with mock.patch("skillopt_sleep.backend.subprocess.run", return_value=SimpleNamespace(stdout="5", stderr="")):
        assert call() == "5" and backend.last_call_error == ""


def test_claude_cache_retries_failure_and_clears_unrelated_error():
    backend = ClaudeCliBackend(claude_path="nonexistent-synthetic-claude")
    with mock.patch("skillopt_sleep.backend.subprocess.run", side_effect=OSError("synthetic")):
        assert backend._cached_call("same", "prompt") == ""
    assert "same" not in backend._cache
    with mock.patch("skillopt_sleep.backend.subprocess.run", return_value=SimpleNamespace(stdout="4", stderr="")) as run:
        assert backend._cached_call("same", "prompt") == "4"
        assert run.call_count == 1
    backend.last_call_error = "another call failed"
    with mock.patch("skillopt_sleep.backend.subprocess.run", side_effect=AssertionError("cache should avoid execution")):
        assert backend._cached_call("same", "prompt") == "4"
    assert backend.last_call_error == ""


@pytest.fixture
def setup(tmp_path):
    project = str(tmp_path / "project")
    cfg = SleepConfig({**DEFAULTS, "invoked_project": project, "claude_home": str(tmp_path / "home"),
                       "state_dir": str(tmp_path / "state"), "llm_mine": False, "backend": "handoff"})
    backend = HandoffBackend(handoff_dir=str(tmp_path / "handoff"))
    return cfg, backend, SimpleNamespace(json=False)


def test_empty_pin_releases_and_next_run_reharvests(setup, monkeypatch):
    from pathlib import Path
    cfg, backend, args = setup
    pin = Path(backend.handoff_dir) / "digests.json"
    pin.write_text("[]")
    fresh = SessionDigest("fresh", cfg.invoked_project, user_prompts=["synthetic"])
    harvest = mock.Mock(return_value=[fresh])
    monkeypatch.setattr(cli, "harvest_for_config", harvest)
    monkeypatch.setattr(cli, "mine", lambda *a, **k: [])
    snapshot = str(pin.with_name("tasks.json"))
    assert cli._handoff_mine_and_pin(cfg, args, backend, snapshot, False) == (0, None)
    assert not pin.exists() and harvest.call_count == 0
    assert cli._handoff_mine_and_pin(cfg, args, backend, snapshot, False) == (0, None)
    assert harvest.call_count == 1 and harvest.call_args.kwargs["since_iso"]


@pytest.mark.parametrize("name,raw", [("tasks.json", b'{"tasks": [bad'), ("tasks.json", b'{"tasks": [{}]}'),
                                     ("digests.json", b'{bad'), ("digests.json", b'42')])
@pytest.mark.parametrize("dry", [False, True])
def test_corrupt_pin_private_recovery_and_dry_preservation(setup, monkeypatch, name, raw, dry):
    from pathlib import Path
    cfg, backend, args = setup
    hdir = Path(backend.handoff_dir)
    pin = hdir / name
    pin.write_bytes(raw)
    monkeypatch.setattr(cli, "_handoff_dir_for", lambda cfg: str(hdir))
    monkeypatch.setattr(cli, "harvest_for_config", lambda *a, **k: [])
    monkeypatch.setattr(cli, "mine", lambda *a, **k: [])
    assert cli._run_handoff(cfg, args, seed_tasks=None, task_meta={}, dry=dry) == 0
    recovery = Path(cfg.state_dir) / "handoff-recovery"
    if dry:
        assert pin.read_bytes() == raw and not recovery.exists()
        assert not Path(cfg.state_path).exists()
    else:
        assert not pin.exists()
        copies = list(recovery.iterdir())
        assert len(copies) == 1 and copies[0].read_bytes() == raw
        assert stat.S_IMODE(copies[0].stat().st_mode) == 0o600
        assert stat.S_IMODE(recovery.stat().st_mode) == 0o700


def test_dry_empty_pin_does_not_advance_or_delete(setup, monkeypatch):
    from pathlib import Path
    cfg, backend, args = setup
    pin = Path(backend.handoff_dir) / "digests.json"
    pin.write_text("[]")
    monkeypatch.setattr(cli, "mine", lambda *a, **k: [])
    assert cli._handoff_mine_and_pin(cfg, args, backend, str(pin.with_name("tasks.json")), True) == (0, None)
    assert pin.read_text() == "[]" and not Path(cfg.state_path).exists()


def test_dry_fresh_digests_and_tasks_are_not_pinned(setup, monkeypatch):
    from pathlib import Path
    cfg, backend, args = setup
    monkeypatch.setattr(cli, "harvest_for_config", lambda *a, **k: [SessionDigest("s", cfg.invoked_project)])
    monkeypatch.setattr(cli, "mine", lambda *a, **k: [TaskRecord("t", cfg.invoked_project, "synthetic")])
    snapshot = Path(backend.handoff_dir) / "tasks.json"
    rc, tasks = cli._handoff_mine_and_pin(cfg, args, backend, str(snapshot), True)
    assert rc == 0 and len(tasks) == 1
    assert not snapshot.exists() and not snapshot.with_name("digests.json").exists()


def test_recovery_failure_never_discards_original(setup, monkeypatch):
    from pathlib import Path
    cfg, backend, args = setup
    pin = Path(backend.handoff_dir) / "tasks.json"
    pin.write_text("{bad")
    monkeypatch.setattr(cli, "_handoff_dir_for", lambda cfg: backend.handoff_dir)
    with mock.patch("tempfile.mkstemp", side_effect=OSError("synthetic disk full")):
        with pytest.raises(OSError):
            cli._run_handoff(cfg, args, seed_tasks=None, task_meta={}, dry=False)
    assert pin.read_text() == "{bad"


@pytest.mark.parametrize("when", ["before", "during"])
def test_recovery_does_not_remove_a_newer_pin(setup, monkeypatch, when):
    from pathlib import Path
    cfg, backend, _args = setup
    pin = Path(backend.handoff_dir) / "tasks.json"
    pin.write_text("{bad")
    expected = cli._pin_identity(str(pin))
    newer = '{"tasks": []}'
    if when == "before":
        pin.write_text(newer)
    else:
        real_fsync = cli.os.fsync
        def replace_on_sync(fd):
            real_fsync(fd)
            pin.write_text(newer)
        monkeypatch.setattr(cli.os, "fsync", replace_on_sync)
    with pytest.raises(ValueError, match="changed"):
        cli._preserve_corrupt_pin(cfg, str(pin), expected)
    assert pin.read_text() == newer
