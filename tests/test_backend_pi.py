"""Tests for the pi CLI backend (`--backend pi`)."""
from __future__ import annotations

import os
import subprocess
from unittest import mock

from skillopt_sleep.backend import (
    _NO_WINDOW,
    DualBackend,
    PiCliBackend,
    build_backend,
    get_backend,
)
from skillopt_sleep.types import TaskRecord


class _FakeProc:
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_get_backend_pi_aliases():
    for alias in ("pi", "pi_cli", "pi_coding_agent", "pi-coding-agent", "PI"):
        be = get_backend(alias, model="zai/glm-5.2")
        assert isinstance(be, PiCliBackend), alias
        assert be.name == "pi"


def test_default_model_from_env(monkeypatch):
    monkeypatch.setenv("SKILLOPT_SLEEP_PI_MODEL", "zai/glm-5.2")
    be = PiCliBackend()
    assert be.model == "zai/glm-5.2"
    assert be.pi_path == "pi"


def test_call_builds_isolated_command_and_returns_stdout():
    be = PiCliBackend(model="zai/glm-5.2", pi_path="/usr/local/bin/pi")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured.update(kwargs)
        return _FakeProc("answer text")

    with mock.patch("skillopt_sleep.backend.subprocess.run", side_effect=fake_run):
        out = be._call("do the thing")

    assert out == "answer text"
    cmd = captured["cmd"]
    assert cmd[0:2] == ["/usr/local/bin/pi", "-p"]
    # Prompts go over stdin rather than argv (important for long/Windows calls).
    assert captured["input"] == "do the thing"
    assert "do the thing" not in cmd
    # Isolation flags must be present (no ambient skills/context/tools).
    assert "--no-tools" in cmd
    assert "--no-skills" in cmd
    assert "--no-context-files" in cmd
    assert "--no-extensions" in cmd
    assert "--no-prompt-templates" in cmd
    assert "--no-themes" in cmd
    assert "--no-session" in cmd
    assert cmd[cmd.index("--system-prompt") + 1] == ""
    assert cmd[cmd.index("--append-system-prompt") + 1] == ""
    assert "--model" in cmd and "zai/glm-5.2" in cmd
    # ran from a clean temp cwd, not inherited
    assert captured["cwd"] is not None and captured["cwd"] != ""
    assert captured["creationflags"] == _NO_WINDOW
    assert captured["env"]["PI_OFFLINE"] == "1"
    assert captured["env"]["PI_SKIP_VERSION_CHECK"] == "1"
    assert captured["env"]["PI_TELEMETRY"] == "0"


def test_path_expands_and_resolves_windows_shim(monkeypatch):
    monkeypatch.setattr(os.path, "expanduser", lambda value: "/home/u/bin/pi" if value == "~/bin/pi" else value)
    with mock.patch("shutil.which", return_value="C:\\npm\\pi.CMD") as which:
        be = PiCliBackend(pi_path="~/bin/pi")
    which.assert_called_once_with("/home/u/bin/pi")
    assert be.pi_path == "C:\\npm\\pi.CMD"


def test_call_records_auth_error_from_nonzero_exit():
    be = PiCliBackend()
    with mock.patch(
        "skillopt_sleep.backend.subprocess.run",
        return_value=_FakeProc(
            "", stderr="No API key found for openai", returncode=1
        ),
    ):
        out = be._call("hi")
    assert out == ""  # empty stdout
    assert "No API key found for openai" in be.last_call_error


def test_successful_short_answers_that_mention_cli_errors_are_preserved():
    answers = (
        "Not logged in means the session needs authentication.",
        "Authentication required is an error message.",
        "Invalid API key should be reported to the user.",
        "Unauthorized requests receive HTTP 401.",
        "The provider not found error comes from configuration.",
        "No provider is required for this local operation.",
    )
    be = PiCliBackend()

    for answer in answers:
        with mock.patch(
            "skillopt_sleep.backend.subprocess.run",
            return_value=_FakeProc(answer),
        ):
            assert be._call("explain the error") == answer
        assert be.last_call_error == ""


def test_call_records_nonzero_exit_even_with_stdout():
    be = PiCliBackend()
    proc = _FakeProc("misleading answer", stderr="pi: unknown option", returncode=2)
    with mock.patch("skillopt_sleep.backend.subprocess.run", return_value=proc):
        assert be._call("hi") == ""
    assert "exited 2" in be.last_call_error
    assert "misleading answer" not in be.last_call_error


def test_call_records_empty_success_response():
    be = PiCliBackend()
    with mock.patch(
        "skillopt_sleep.backend.subprocess.run",
        return_value=_FakeProc("  \n"),
    ):
        assert be._call("hi") == ""
    assert be.last_call_error == "Pi CLI returned an empty response"


def test_call_redacts_stderr_from_empty_success(caplog):
    be = PiCliBackend()
    secret = "sk-1234567890abcdefghij"
    with mock.patch(
        "skillopt_sleep.backend.subprocess.run",
        return_value=_FakeProc("", stderr=f"warning: key={secret}"),
    ):
        assert be._call("hi") == ""
    assert "warning" in be.last_call_error
    assert secret not in be.last_call_error
    assert secret not in caplog.text


def test_call_preserves_nonempty_success_with_stderr_warning():
    be = PiCliBackend()
    with mock.patch(
        "skillopt_sleep.backend.subprocess.run",
        return_value=_FakeProc("answer", stderr="provider warning"),
    ):
        assert be._call("hi") == "answer"
    assert be.last_call_error == ""


def test_call_redacts_secrets_in_error(caplog):
    be = PiCliBackend()
    secret = "sk-1234567890abcdefghij"
    proc = _FakeProc("", stderr=f"Invalid API key: {secret}", returncode=1)
    with mock.patch("skillopt_sleep.backend.subprocess.run", return_value=proc):
        assert be._call("hi") == ""
    assert secret not in be.last_call_error
    assert secret not in caplog.text


def test_call_records_timeout():
    be = PiCliBackend(timeout=1)
    with mock.patch(
        "skillopt_sleep.backend.subprocess.run",
        side_effect=subprocess.TimeoutExpired("pi", 1),
    ):
        assert be._call("hi") == ""
    assert "timed out" in be.last_call_error


def test_call_normalizes_and_redacts_timeout_bytes(caplog):
    be = PiCliBackend(timeout=1)
    secret = "sk-1234567890abcdefghij"
    with mock.patch(
        "skillopt_sleep.backend.subprocess.run",
        side_effect=subprocess.TimeoutExpired(
            "pi", 1, stderr=f"API key: {secret}".encode()
        ),
    ):
        assert be._call("hi") == ""
    assert "timed out" in be.last_call_error
    assert "b'" not in be.last_call_error
    assert secret not in be.last_call_error
    assert secret not in caplog.text


def test_call_records_and_redacts_spawn_failure(caplog):
    be = PiCliBackend()
    secret = "sk-1234567890abcdefghij"
    with mock.patch(
        "skillopt_sleep.backend.subprocess.run",
        side_effect=OSError(f"cannot launch with token={secret}"),
    ):
        assert be._call("hi") == ""
    assert secret not in be.last_call_error
    assert secret not in caplog.text


def test_failed_empty_response_is_not_cached():
    be = PiCliBackend()
    with mock.patch.object(be, "_call", side_effect=["", "recovered"]) as call:
        assert be._cached_call("attempt:key", "prompt") == ""
        assert be._cached_call("attempt:key", "prompt") == "recovered"
    assert call.call_count == 2


def test_success_clears_previous_call_error():
    be = PiCliBackend()
    be.last_call_error = "an older failure"
    with mock.patch(
        "skillopt_sleep.backend.subprocess.run",
        return_value=_FakeProc("recovered"),
    ):
        assert be._call("hi") == "recovered"
    assert be.last_call_error == ""


def test_cache_and_retry_token_accounting():
    prompt = "p" * 20
    response = "r" * 12
    be = PiCliBackend()
    with mock.patch.object(be, "_call", return_value=response) as call:
        assert be._cached_call("attempt:success", prompt) == response
        spent = be.tokens_used()
        assert spent == len(prompt) // 4 + len(response) // 4
        assert be._cached_call("attempt:success", prompt) == response
    assert call.call_count == 1
    assert be.tokens_used() == spent

    retrying = PiCliBackend()
    with mock.patch.object(retrying, "_call", side_effect=["", response]) as call:
        assert retrying._cached_call("attempt:retry", prompt) == ""
        after_failure = retrying.tokens_used()
        assert after_failure == len(prompt) // 4
        assert retrying._cached_call("attempt:retry", prompt) == response
        after_recovery = retrying.tokens_used()
        assert retrying._cached_call("attempt:retry", prompt) == response
    assert call.call_count == 2
    assert after_recovery == after_failure + len(prompt) // 4 + len(response) // 4
    assert retrying.tokens_used() == after_recovery


def test_cached_success_clears_stale_call_error_without_spending_tokens():
    be = PiCliBackend()
    prompt = "cached prompt"
    with mock.patch.object(be, "_call", return_value="cached answer") as call:
        assert be._cached_call("attempt:cached", prompt) == "cached answer"
        spent = be.tokens_used()

        be.last_call_error = "unrelated later failure"
        assert be._cached_call("attempt:cached", prompt) == "cached answer"

    assert call.call_count == 1
    assert be.last_call_error == ""
    assert be.tokens_used() == spent


def test_dual_backend_tokens_are_summed_once():
    target = PiCliBackend()
    optimizer = PiCliBackend()
    dual = DualBackend(target=target, optimizer=optimizer)

    with (
        mock.patch.object(target, "_call", return_value="t" * 8),
        mock.patch.object(optimizer, "_call", return_value="o" * 12),
    ):
        target._cached_call("attempt:target", "a" * 16)
        optimizer._cached_call("judge:optimizer", "b" * 20)

    expected = target.tokens_used() + optimizer.tokens_used()
    assert expected > 0
    assert dual.tokens_used() == expected


def test_dual_attempt_failure_retries_then_caches_without_double_counting():
    target = PiCliBackend()
    optimizer = PiCliBackend()
    dual = DualBackend(target=target, optimizer=optimizer)
    task = TaskRecord(id="pi-retry", project="/repo", intent="fix the test")

    failed = _FakeProc("", stderr="Authentication required", returncode=1)
    recovered = _FakeProc("fixed response")
    with mock.patch(
        "skillopt_sleep.backend.subprocess.run",
        side_effect=[failed, recovered],
    ) as run:
        assert dual.attempt(task, "", "") == ""
        assert target.last_call_error
        after_failure = dual.tokens_used()

        assert dual.attempt(task, "", "") == "fixed response"
        assert target.last_call_error == ""
        after_recovery = dual.tokens_used()

        assert dual.attempt(task, "", "") == "fixed response"

    assert run.call_count == 2
    assert after_recovery > after_failure
    assert dual.tokens_used() == after_recovery
    assert optimizer.tokens_used() == 0


def test_pi_path_reaches_single_and_dual_backends():
    single = build_backend(backend="pi", pi_path="/opt/pi")
    assert isinstance(single, PiCliBackend)
    assert single.pi_path == "/opt/pi"

    dual = build_backend(
        backend="mock",
        optimizer_backend="pi",
        target_backend="pi",
        pi_path="/opt/pi",
    )
    assert isinstance(dual.optimizer, PiCliBackend)
    assert isinstance(dual.target, PiCliBackend)
    assert dual.optimizer.pi_path == dual.target.pi_path == "/opt/pi"
