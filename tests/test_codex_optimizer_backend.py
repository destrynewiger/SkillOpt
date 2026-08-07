from __future__ import annotations

import importlib.util
import os
import sys
import types
from collections.abc import Iterator
from typing import Any

import pytest


class _OpenAIClientStub:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs


def _install_openai_stub() -> None:
    if "openai" in sys.modules or importlib.util.find_spec("openai") is not None:
        return
    openai_stub = types.ModuleType("openai")
    openai_stub.AzureOpenAI = _OpenAIClientStub
    openai_stub.OpenAI = _OpenAIClientStub
    sys.modules["openai"] = openai_stub


def _import_model_modules() -> tuple[Any, Any, Any, Any]:
    _install_openai_stub()
    import skillopt.model as model_module
    from skillopt.model import azure_openai, backend_config, codex_backend

    return model_module, backend_config, codex_backend, azure_openai


@pytest.fixture(autouse=True)
def isolate_backend_state() -> Iterator[tuple[Any, Any, Any, Any]]:
    model_module, backend_config, codex_backend, azure_openai = _import_model_modules()
    optimizer_backend = backend_config.get_optimizer_backend()
    target_backend = backend_config.get_target_backend()
    env = {
        key: os.environ.get(key)
        for key in (
            "OPTIMIZER_BACKEND",
            "TARGET_BACKEND",
            "OPTIMIZER_DEPLOYMENT",
            "TARGET_DEPLOYMENT",
        )
    }
    yield model_module, backend_config, codex_backend, azure_openai
    backend_config.set_optimizer_backend(optimizer_backend)
    backend_config.set_target_backend(target_backend)
    for key, value in env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_codex_exec_can_be_optimizer_backend(
    isolate_backend_state: tuple[Any, Any, Any, Any],
) -> None:
    _model_module, backend_config, _codex_backend, _azure_openai = isolate_backend_state

    backend_config.set_optimizer_backend("codex_exec")

    assert backend_config.get_optimizer_backend() == "codex_exec"


def test_set_backend_codex_uses_codex_for_optimizer_and_target(
    isolate_backend_state: tuple[Any, Any, Any, Any],
) -> None:
    model_module, backend_config, _codex_backend, _azure_openai = isolate_backend_state

    assert model_module.set_backend("codex") == "codex"

    assert backend_config.get_optimizer_backend() == "codex_exec"
    assert backend_config.get_target_backend() == "codex_exec"
    assert model_module.get_backend_name() == "codex"


def test_chat_optimizer_routes_to_codex_backend(
    monkeypatch: pytest.MonkeyPatch,
    isolate_backend_state: tuple[Any, Any, Any, Any],
) -> None:
    model_module, backend_config, codex_backend, azure_openai = isolate_backend_state
    codex_calls: list[dict[str, Any]] = []

    def fake_codex_optimizer(**kwargs: Any) -> tuple[str, dict[str, int]]:
        codex_calls.append(kwargs)
        return "codex result", {
            "prompt_tokens": 1,
            "completion_tokens": 2,
            "total_tokens": 3,
        }

    def fail_openai_optimizer(**_kwargs: Any) -> tuple[str, dict[str, int]]:
        raise AssertionError("openai optimizer should not be called for codex_exec")

    monkeypatch.setattr(codex_backend, "chat_optimizer", fake_codex_optimizer)
    monkeypatch.setattr(azure_openai, "chat_optimizer", fail_openai_optimizer)
    backend_config.set_optimizer_backend("codex_exec")

    text, usage = model_module.chat_optimizer("system", "user", retries=1, timeout=5)

    assert text == "codex result"
    assert usage["total_tokens"] == 3
    assert codex_calls[0]["system"] == "system"
    assert codex_calls[0]["user"] == "user"
    assert codex_calls[0]["timeout"] == 5


def test_openai_compatible_still_allowed_as_optimizer_backend(
    isolate_backend_state: tuple[Any, Any, Any, Any],
) -> None:
    _model_module, backend_config, _codex_backend, _azure_openai = isolate_backend_state

    backend_config.set_optimizer_backend("openai_compatible")

    assert backend_config.get_optimizer_backend() == "openai_compatible"


def test_chat_optimizer_routes_to_openai_compatible_when_selected(
    monkeypatch: pytest.MonkeyPatch,
    isolate_backend_state: tuple[Any, Any, Any, Any],
) -> None:
    model_module, backend_config, codex_backend, azure_openai = isolate_backend_state
    from skillopt.model import openai_compatible_backend

    compat_calls: list[dict[str, Any]] = []

    def fake_compat_optimizer(**kwargs: Any) -> tuple[str, dict[str, int]]:
        compat_calls.append(kwargs)
        return "compat result", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

    def fail_codex_optimizer(**_kwargs: Any) -> tuple[str, dict[str, int]]:
        raise AssertionError("codex optimizer should not be called for openai_compatible")

    def fail_openai_optimizer(**_kwargs: Any) -> tuple[str, dict[str, int]]:
        raise AssertionError("openai optimizer should not be called for openai_compatible")

    monkeypatch.setattr(openai_compatible_backend, "chat_optimizer", fake_compat_optimizer)
    monkeypatch.setattr(codex_backend, "chat_optimizer", fail_codex_optimizer)
    monkeypatch.setattr(azure_openai, "chat_optimizer", fail_openai_optimizer)
    backend_config.set_optimizer_backend("openai_compatible")

    text, usage = model_module.chat_optimizer("system", "user", retries=1, timeout=7)

    assert text == "compat result"
    assert usage["total_tokens"] == 2
    assert compat_calls[0]["timeout"] == 7


def test_chat_optimizer_messages_routes_to_codex_backend(
    monkeypatch: pytest.MonkeyPatch,
    isolate_backend_state: tuple[Any, Any, Any, Any],
) -> None:
    model_module, backend_config, codex_backend, azure_openai = isolate_backend_state
    codex_calls: list[dict[str, Any]] = []

    def fake_codex_messages(**kwargs: Any) -> tuple[str, dict[str, int]]:
        codex_calls.append(kwargs)
        return "codex messages", {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}

    def fail_openai_messages(**_kwargs: Any) -> tuple[str, dict[str, int]]:
        raise AssertionError("openai messages should not be called for codex_exec")

    monkeypatch.setattr(codex_backend, "chat_optimizer_messages", fake_codex_messages)
    monkeypatch.setattr(azure_openai, "chat_optimizer_messages", fail_openai_messages)
    backend_config.set_optimizer_backend("codex_exec")

    text, usage = model_module.chat_optimizer_messages(
        [{"role": "user", "content": "hi"}],
        retries=1,
        tools=[{"name": "lookup"}],
        tool_choice="required",
        return_message=True,
        timeout=9,
    )

    assert text == "codex messages"
    assert usage["total_tokens"] == 5
    assert codex_calls[0]["tools"] == [{"name": "lookup"}]
    assert codex_calls[0]["tool_choice"] == "required"
    assert codex_calls[0]["return_message"] is True
    assert codex_calls[0]["timeout"] == 9


def test_codex_optimizer_does_not_change_openai_target_routing(
    monkeypatch: pytest.MonkeyPatch,
    isolate_backend_state: tuple[Any, Any, Any, Any],
) -> None:
    model_module, backend_config, codex_backend, azure_openai = isolate_backend_state

    def fake_openai_target(**_kwargs: Any) -> tuple[str, dict[str, int]]:
        return "openai target", {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1}

    def fail_codex_target(**_kwargs: Any) -> tuple[str, dict[str, int]]:
        raise AssertionError("codex target should not be called when target_backend=openai_chat")

    monkeypatch.setattr(azure_openai, "chat_target", fake_openai_target)
    monkeypatch.setattr(codex_backend, "chat_target", fail_codex_target)
    backend_config.set_optimizer_backend("codex_exec")
    backend_config.set_target_backend("openai_chat")

    text, usage = model_module.chat_target("system", "user", retries=1)

    assert text == "openai target"
    assert usage["total_tokens"] == 1


def test_get_backend_name_keeps_openai_compatible(
    isolate_backend_state: tuple[Any, Any, Any, Any],
) -> None:
    model_module, backend_config, _codex_backend, _azure_openai = isolate_backend_state

    backend_config.set_optimizer_backend("openai_compatible")
    backend_config.set_target_backend("openai_compatible")

    assert model_module.get_backend_name() == "openai_compatible"


def test_token_summary_merges_codex_once_with_existing_backends(
    monkeypatch: pytest.MonkeyPatch,
    isolate_backend_state: tuple[Any, Any, Any, Any],
) -> None:
    model_module, _backend_config, codex_backend, azure_openai = isolate_backend_state
    from skillopt.model import claude_backend, minimax_backend, openai_compatible_backend, qwen_backend

    empty = {"_total": {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}
    monkeypatch.setattr(azure_openai, "get_token_summary", lambda: empty.copy())
    monkeypatch.setattr(claude_backend, "get_token_summary", lambda: empty.copy())
    monkeypatch.setattr(qwen_backend, "get_token_summary", lambda: empty.copy())
    monkeypatch.setattr(minimax_backend, "get_token_summary", lambda: empty.copy())
    monkeypatch.setattr(
        openai_compatible_backend,
        "get_token_summary",
        lambda: {"optimizer": {"calls": 1, "prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}},
    )
    monkeypatch.setattr(
        codex_backend,
        "get_token_summary",
        lambda: {"optimizer": {"calls": 1, "prompt_tokens": 11, "completion_tokens": 13, "total_tokens": 24}},
    )

    summary = model_module.get_token_summary()

    assert summary["optimizer"]["calls"] == 2
    assert summary["optimizer"]["prompt_tokens"] == 16
    assert summary["optimizer"]["completion_tokens"] == 20
    assert summary["_total"]["total_tokens"] == 36


def test_codex_usage_is_not_double_counted_by_shared_tracker(
    isolate_backend_state: tuple[Any, Any, Any, Any],
) -> None:
    model_module, _backend_config, codex_backend, _azure_openai = isolate_backend_state
    from skillopt.model import claude_backend

    model_module.reset_token_tracker()
    try:
        assert codex_backend.tracker is not claude_backend.tracker

        codex_backend.tracker.record("optimizer", 11, 13)
        summary = model_module.get_token_summary()

        assert summary["optimizer"] == {
            "calls": 1,
            "prompt_tokens": 11,
            "completion_tokens": 13,
            "total_tokens": 24,
        }
        assert summary["_total"] == {
            "calls": 1,
            "prompt_tokens": 11,
            "completion_tokens": 13,
            "total_tokens": 24,
        }
    finally:
        model_module.reset_token_tracker()


def test_reset_token_tracker_resets_codex_and_existing_backends(
    monkeypatch: pytest.MonkeyPatch,
    isolate_backend_state: tuple[Any, Any, Any, Any],
) -> None:
    model_module, _backend_config, codex_backend, azure_openai = isolate_backend_state
    from skillopt.model import claude_backend, minimax_backend, openai_compatible_backend, qwen_backend

    called: list[str] = []
    for name, module in [
        ("openai", azure_openai),
        ("claude", claude_backend),
        ("qwen", qwen_backend),
        ("minimax", minimax_backend),
        ("compat", openai_compatible_backend),
        ("codex", codex_backend),
    ]:
        monkeypatch.setattr(module, "reset_token_tracker", lambda name=name: called.append(name))

    model_module.reset_token_tracker()

    assert called == ["openai", "claude", "qwen", "minimax", "compat", "codex"]


def test_set_reasoning_effort_updates_codex_and_existing_backends(
    monkeypatch: pytest.MonkeyPatch,
    isolate_backend_state: tuple[Any, Any, Any, Any],
) -> None:
    model_module, _backend_config, codex_backend, azure_openai = isolate_backend_state
    from skillopt.model import claude_backend, minimax_backend, openai_compatible_backend, qwen_backend

    called: list[tuple[str, str]] = []
    for name, module in [
        ("openai", azure_openai),
        ("claude", claude_backend),
        ("qwen", qwen_backend),
        ("minimax", minimax_backend),
        ("compat", openai_compatible_backend),
        ("codex", codex_backend),
    ]:
        monkeypatch.setattr(module, "set_reasoning_effort", lambda effort, name=name: called.append((name, effort)))

    model_module.set_reasoning_effort("high")

    assert called == [
        ("openai", "high"),
        ("claude", "high"),
        ("qwen", "high"),
        ("minimax", "high"),
        ("compat", "high"),
        ("codex", "high"),
    ]


def test_deployment_setters_update_codex_without_dropping_openai_compatible(
    monkeypatch: pytest.MonkeyPatch,
    isolate_backend_state: tuple[Any, Any, Any, Any],
) -> None:
    model_module, _backend_config, codex_backend, _azure_openai = isolate_backend_state
    from skillopt.model import openai_compatible_backend

    called: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        openai_compatible_backend,
        "set_target_deployment",
        lambda deployment: called.append(("compat", "target", deployment)),
    )
    monkeypatch.setattr(
        openai_compatible_backend,
        "set_optimizer_deployment",
        lambda deployment: called.append(("compat", "optimizer", deployment)),
    )
    monkeypatch.setattr(
        codex_backend,
        "set_target_deployment",
        lambda deployment: called.append(("codex", "target", deployment)),
    )
    monkeypatch.setattr(
        codex_backend,
        "set_optimizer_deployment",
        lambda deployment: called.append(("codex", "optimizer", deployment)),
    )

    model_module.set_target_deployment("target-model")
    model_module.set_optimizer_deployment("optimizer-model")

    assert ("compat", "target", "target-model") in called
    assert ("codex", "target", "target-model") in called
    assert ("compat", "optimizer", "optimizer-model") in called
    assert ("codex", "optimizer", "optimizer-model") in called
