"""Tests for shell-injection hardening in the ReAct agent's bash tool.

``_run_bash`` previously ran commands with ``shell=True``, allowing arbitrary
shell metacharacter injection. It now uses ``shlex.split`` + ``shell=False``
and restricts the executable to a small allow-list (python/python3). These
tests assert both the allow-list gate and that shell metacharacters are no
longer interpreted.
"""
from __future__ import annotations

from skillopt.envs.spreadsheetbench.react_agent import _run_bash


def test_disallowed_command_is_blocked(tmp_path) -> None:
    out = _run_bash("curl http://example.com/evil", str(tmp_path))
    assert "blocked" in out.lower()


def test_allowed_python_runs_without_path_lookup(tmp_path, monkeypatch) -> None:
    # Accepted aliases are mapped to the running interpreter, so an absent PATH
    # must not make the benchmark depend on a system-level Python command.
    monkeypatch.setenv("PATH", "")
    out = _run_bash('python -c "print(42)"', str(tmp_path))
    assert "42" in out


def test_similarly_named_executable_is_blocked(tmp_path) -> None:
    out = _run_bash('python.evil -c "print(42)"', str(tmp_path))
    assert "blocked" in out.lower()


def test_python_child_does_not_inherit_parent_secrets(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("SUPER_SECRET_TOKEN", "must-not-leak")
    out = _run_bash(
        'python -c "import os; print(os.environ.get(\'SUPER_SECRET_TOKEN\', \'ABSENT\'))"',
        str(tmp_path),
    )
    assert out == "ABSENT"


def test_shell_metacharacters_not_interpreted(tmp_path) -> None:
    # With shell=False the ';' and following tokens become arguments to python,
    # not a second shell command, so the marker file must NOT be created.
    marker = tmp_path / "pwned.txt"
    cmd = "python -c \"print(1)\" ; python -c \"open('pwned.txt','w')\""
    _run_bash(cmd, str(tmp_path))
    assert not marker.exists()
