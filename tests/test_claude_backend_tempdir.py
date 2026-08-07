"""Regression coverage for Claude CLI temporary-directory cleanup."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from types import SimpleNamespace

from skillopt.model import claude_backend


def test_claude_print_uses_cleanup_tolerant_temporary_directory(monkeypatch) -> None:
    cleanup_modes = []
    subprocess_cwd = ""

    def simulated_windows_rmtree(cls, name, ignore_errors=False, repeated=False):
        del cls, repeated
        cleanup_modes.append(ignore_errors)
        if not ignore_errors:
            raise PermissionError(32, "directory is still in use", name)
        shutil.rmtree(name, ignore_errors=True)

    def fake_run(*args, **kwargs):
        nonlocal subprocess_cwd
        subprocess_cwd = kwargs["cwd"]
        payload = {
            "type": "result",
            "result": "ok",
            "usage": {"input_tokens": 2, "output_tokens": 3},
        }
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload) + "\n",
            stderr="",
        )

    monkeypatch.setattr(
        tempfile.TemporaryDirectory,
        "_rmtree",
        classmethod(simulated_windows_rmtree),
    )
    monkeypatch.setattr(claude_backend.subprocess, "run", fake_run)

    text, _, usage = claude_backend._run_claude_print(
        system="system",
        prompt="prompt",
        model="",
        tools=None,
        tool_choice=None,
        return_message=False,
        timeout=10,
    )

    assert text == "ok"
    assert usage == {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}
    assert cleanup_modes == [True]
    assert subprocess_cwd
    assert not os.path.exists(subprocess_cwd)
