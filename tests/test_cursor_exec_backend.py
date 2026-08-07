from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import skillopt.model as model
from skillopt.config import flatten_config
from skillopt.model import backend_config
from skillopt.model import codex_harness as harness
from skillopt.model.common import default_model_for_backend, normalize_backend_name


@pytest.fixture(autouse=True)
def restore_backend_state() -> Iterator[None]:
    optimizer_backend = backend_config.get_optimizer_backend()
    target_backend = backend_config.get_target_backend()
    cursor_path = backend_config.CURSOR_EXEC_PATH
    cursor_sandbox = backend_config.CURSOR_EXEC_SANDBOX
    retries = backend_config.EXEC_EMPTY_RESPONSE_RETRIES
    env = {
        key: os.environ.get(key)
        for key in (
            "OPTIMIZER_BACKEND",
            "TARGET_BACKEND",
            "CURSOR_EXEC_PATH",
            "CURSOR_EXEC_SANDBOX",
        )
    }
    yield
    backend_config.OPTIMIZER_BACKEND = optimizer_backend
    backend_config.TARGET_BACKEND = target_backend
    backend_config.CURSOR_EXEC_PATH = cursor_path
    backend_config.CURSOR_EXEC_SANDBOX = cursor_sandbox
    backend_config.EXEC_EMPTY_RESPONSE_RETRIES = retries
    for key, value in env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _result(text: str = "<answer>A</answer>") -> str:
    return (
        '{"type":"system","subtype":"init","model":"composer-2.5",'
        '"permissionMode":"default","session_id":"session-1"}\n'
        '{"type":"tool_call","subtype":"started","call_id":"call-1",'
        '"tool_call":{"readToolCall":{"args":{"path":"task.md"}}}}\n'
        f'{{"type":"result","subtype":"success","is_error":false,'
        f'"duration_ms":12,"result":"{text}","session_id":"session-1"}}\n'
    )


def _workspace(tmp_path: Path) -> Path:
    work_dir = tmp_path / "predictions" / "task-1" / "cursor_exec"
    work_dir.mkdir(parents=True)
    return work_dir


def test_cursor_exec_is_target_only() -> None:
    backend_config.set_target_backend("cursor")

    assert backend_config.get_target_backend() == "cursor_exec"
    assert backend_config.is_target_exec_backend()
    with pytest.raises(ValueError, match="Unsupported optimizer backend"):
        backend_config.set_optimizer_backend("cursor_exec")
    with pytest.raises(NotImplementedError, match="Exec backends"):
        model.chat_target("system", "user")


def test_cursor_alias_and_default_model() -> None:
    assert normalize_backend_name("cursor") == "cursor_exec"
    assert normalize_backend_name("cursor_agent") == "cursor_exec"
    assert default_model_for_backend("cursor_exec") == "composer-2.5"

    assert model.set_backend("cursor") == "cursor_exec"
    assert backend_config.get_optimizer_backend() == "openai_chat"
    assert backend_config.get_target_backend() == "cursor_exec"
    assert model.get_backend_name() == "cursor_exec"


def test_cursor_config_flattens_and_validates() -> None:
    flat = flatten_config(
        {
            "model": {
                "cursor_exec_path": "/opt/cursor-agent",
                "cursor_exec_sandbox": "disabled",
            }
        }
    )
    assert flat["cursor_exec_path"] == "/opt/cursor-agent"
    assert flat["cursor_exec_sandbox"] == "disabled"

    backend_config.configure_cursor_exec(path="cursor-test", sandbox="disabled")
    assert backend_config.get_cursor_exec_config()["path"] == "cursor-test"
    assert backend_config.get_cursor_exec_config()["sandbox"] == "disabled"
    with pytest.raises(ValueError, match="sandbox must be"):
        backend_config.configure_cursor_exec(sandbox="invalid")


def test_train_cursor_shorthand_configures_target_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import train

    config_path = Path(__file__).parents[1] / "configs" / "_base_" / "default.yaml"
    monkeypatch.setattr(
        sys,
        "argv",
        ["train.py", "--config", str(config_path), "--backend", "cursor"],
    )

    cfg = train.load_config(train.parse_args())

    assert cfg["optimizer_backend"] == "openai_chat"
    assert cfg["target_backend"] == "cursor_exec"
    assert cfg["target_model"] == "composer-2.5"


def test_read_only_cursor_exec_uses_stdin_and_preserves_trace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    work_dir = _workspace(tmp_path)
    data_dir = tmp_path / "corpus"
    data_dir.mkdir()
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=0, stdout="not json\n" + _result(), stderr="")

    backend_config.configure_cursor_exec(path="cursor-test", sandbox="enabled")
    monkeypatch.setattr(harness.subprocess, "run", fake_run)

    response, raw = harness.run_cursor_exec(
        work_dir=str(work_dir),
        prompt="Answer the benchmark task.",
        model="composer-2.5",
        timeout=17,
        data_dirs=[str(data_dir)],
    )

    assert response == "<answer>A</answer>"
    assert "tool_call" in raw
    assert "not json" not in raw
    assert "task.md" not in raw
    assert "<answer>A</answer>" not in raw
    cmd, kwargs = calls[0]
    assert cmd[:4] == ["cursor-test", "-p", "--output-format", "stream-json"]
    assert ["--mode", "ask"] == cmd[cmd.index("--mode"):cmd.index("--mode") + 2]
    assert "--force" not in cmd
    assert cmd[cmd.index("--workspace") + 1] == str(work_dir)
    assert cmd[cmd.index("--sandbox") + 1] == "enabled"
    assert cmd[cmd.index("--model") + 1] == "composer-2.5"
    assert cmd[cmd.index("--add-dir") + 1] == str(data_dir)
    assert kwargs["cwd"] == str(work_dir)
    assert kwargs["timeout"] == 17
    assert ".agents/skills/skillopt-target/SKILL.md" in kwargs["input"]
    assert "Do not modify files" in kwargs["input"]
    persisted_raw = (work_dir.parent / "cursor_raw.txt").read_text()
    assert "not json" not in persisted_raw
    assert "task.md" not in persisted_raw
    assert "<answer>A</answer>" not in persisted_raw
    summary = (work_dir.parent / "cursor_trace_summary.txt").read_text()
    assert "tool calls: 1" in summary
    assert "session-1" in summary


def test_cursor_exec_force_is_limited_to_file_edit_rollouts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    work_dir = _workspace(tmp_path)
    calls: list[tuple[list[str], str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append((cmd, kwargs["input"]))
        return SimpleNamespace(returncode=0, stdout=_result(), stderr="")

    monkeypatch.setattr(harness.subprocess, "run", fake_run)

    harness.run_cursor_exec(
        work_dir=str(work_dir),
        prompt="Write solution.py.",
        model="",
        timeout=10,
        allow_file_edits=True,
    )

    cmd, prompt = calls[0]
    assert "--force" in cmd
    assert "--mode" not in cmd
    assert "You may modify files" in prompt


def test_cursor_exec_rejects_file_edits_with_disabled_sandbox(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    work_dir = _workspace(tmp_path)
    calls = 0

    def fake_run(_cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        return SimpleNamespace(returncode=0, stdout=_result(), stderr="")

    backend_config.configure_cursor_exec(sandbox="disabled")
    monkeypatch.setattr(harness.subprocess, "run", fake_run)

    with pytest.raises(ValueError, match="refusing to combine --force"):
        harness.run_cursor_exec(
            work_dir=str(work_dir),
            prompt="Write solution.py.",
            model="composer-2.5",
            timeout=10,
            allow_file_edits=True,
        )

    assert calls == 0


def test_cursor_exec_retries_zero_exit_malformed_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    work_dir = _workspace(tmp_path)
    prompts: list[str] = []

    def fake_run(_cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        prompts.append(kwargs["input"])
        stdout = "malformed output" if len(prompts) == 1 else _result()
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    backend_config.EXEC_EMPTY_RESPONSE_RETRIES = 1
    monkeypatch.setattr(harness.subprocess, "run", fake_run)

    response, _raw = harness.run_cursor_exec(
        work_dir=str(work_dir),
        prompt="Answer.",
        model="",
        timeout=10,
    )

    assert response == "<answer>A</answer>"
    assert len(prompts) == 2
    assert "Previous execution returned an empty final response" in prompts[1]


def test_cursor_trace_summary_tolerates_malformed_metadata() -> None:
    raw = (
        '{"type":"result","subtype":"success","is_error":false,'
        '"duration_ms":"unknown","result":"done"}\n'
    )

    summary = harness._build_cursor_trace_summary(raw, "done")

    assert "duration ms: 0" in summary


def test_cursor_trace_omits_message_and_tool_payloads() -> None:
    raw = "\n".join(
        [
            '{"type":"user","message":{"role":"user","content":'
            '[{"type":"text","text":"private prompt"}]}}',
            '{"type":"assistant","message":{"role":"assistant","content":'
            '[{"type":"text","text":"private response"}]}}',
            '{"type":"tool_call","subtype":"completed","tool_call":'
            '{"readToolCall":{"args":{"path":"secret.txt"},"result":'
            '{"success":{"content":"private file contents"}}}}}',
            '{"type":"result","subtype":"success","is_error":false,'
            '"duration_ms":1,"result":"private final answer"}',
        ]
    )

    sanitized = harness._sanitize_cursor_trace(raw)
    events = [json.loads(line) for line in sanitized.splitlines()]

    assert events[0]["message"]["content"] == "[OMITTED]"
    assert events[1]["message"]["content"] == "[OMITTED]"
    assert events[2]["tool_call"]["readToolCall"]["args"] == "[OMITTED]"
    assert events[2]["tool_call"]["readToolCall"]["result"] == "[OMITTED]"
    assert events[3]["result"] == "[OMITTED]"
    assert "private" not in sanitized
    assert "secret.txt" not in sanitized


def test_cursor_exec_does_not_retry_error_result_and_redacts_detail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    work_dir = _workspace(tmp_path)
    calls = 0

    def fake_run(_cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        stdout = (
            '{"type":"result","subtype":"error","is_error":true,'
            '"result":"API key: cursor-secret-value"}\n'
        )
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    backend_config.EXEC_EMPTY_RESPONSE_RETRIES = 1
    monkeypatch.setattr(harness.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as exc_info:
        harness.run_cursor_exec(
            work_dir=str(work_dir),
            prompt="Answer.",
            model="",
            timeout=10,
        )

    assert calls == 1
    assert "[REDACTED]" in str(exc_info.value)
    assert "cursor-secret-value" not in str(exc_info.value)
    persisted_raw = (work_dir.parent / "cursor_raw.txt").read_text()
    assert "cursor-secret-value" not in persisted_raw
    assert '"result":"[OMITTED]"' in persisted_raw


def test_cursor_exec_nonzero_exit_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    work_dir = _workspace(tmp_path)
    calls = 0

    def fake_run(_cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="CURSOR_API_KEY=cursor-secret-token authentication failed",
        )

    backend_config.EXEC_EMPTY_RESPONSE_RETRIES = 1
    monkeypatch.setattr(harness.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as exc_info:
        harness.run_cursor_exec(
            work_dir=str(work_dir),
            prompt="Answer.",
            model="",
            timeout=10,
        )

    assert calls == 1
    assert "[REDACTED]" in str(exc_info.value)
    assert "cursor-secret-token" not in str(exc_info.value)
    persisted_raw = (work_dir.parent / "cursor_raw.txt").read_text()
    assert "cursor-secret-token" not in persisted_raw
    assert "CURSOR_API_KEY=[REDACTED]" in persisted_raw


def test_cursor_exec_timeout_is_persisted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    work_dir = _workspace(tmp_path)

    def fake_run(_cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
        raise subprocess.TimeoutExpired(
            "cursor-test",
            3,
            output=b"partial CURSOR_API_KEY=timeout-secret",
        )

    monkeypatch.setattr(harness.subprocess, "run", fake_run)

    with pytest.raises(subprocess.TimeoutExpired):
        harness.run_cursor_exec(
            work_dir=str(work_dir),
            prompt="Answer.",
            model="",
            timeout=3,
        )

    persisted_raw = (work_dir.parent / "cursor_raw.txt").read_text()
    assert "partial" not in persisted_raw
    assert "[OMITTED NON-JSON OUTPUT]" in persisted_raw
    assert "timeout-secret" not in persisted_raw


def test_cursor_exec_spawn_failure_is_actionable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    work_dir = _workspace(tmp_path)

    def fake_run(_cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
        raise FileNotFoundError("cursor-agent-test was not found")

    monkeypatch.setattr(harness.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="could not be executed"):
        harness.run_cursor_exec(
            work_dir=str(work_dir),
            prompt="Answer.",
            model="",
            timeout=3,
        )


def test_run_target_exec_dispatches_cursor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    def fake_cursor(**kwargs: Any) -> tuple[str, str]:
        captured.update(kwargs)
        return "cursor response", "cursor trace"

    backend_config.set_target_backend("cursor_exec")
    monkeypatch.setattr(harness, "run_cursor_exec", fake_cursor)

    response, raw = harness.run_target_exec(
        work_dir=str(tmp_path),
        prompt="task",
        model="composer-2.5",
        timeout=20,
        allow_file_edits=True,
    )

    assert (response, raw) == ("cursor response", "cursor trace")
    assert captured["allow_file_edits"] is True
    assert captured["model"] == "composer-2.5"
