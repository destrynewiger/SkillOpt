"""Tests for the F16 model-change warning and F08 CLI credential warning."""
from __future__ import annotations

import argparse
import importlib
import warnings

from skillopt_sleep.cycle import _check_model_change, _make_model_key
from skillopt_sleep.config import load_config
from skillopt_sleep.state import SleepState


def _cfg():
    cfg = load_config()
    return cfg


def test_last_model_key_roundtrips(tmp_path) -> None:
    path = str(tmp_path / "state.json")
    state = SleepState.load(path)
    assert state.last_model_key == ""
    state.set_last_model_key("anthropic::claude")
    assert state.last_model_key_format == 2
    state.save()
    assert SleepState.load(path).last_model_key == "anthropic::claude"


def test_warns_when_model_changed(tmp_path, capsys) -> None:
    cfg = _cfg()
    state = SleepState.load(str(tmp_path / "state.json"))
    state.set_last_model_key("some-other::model")
    _check_model_change(cfg, state)
    err = capsys.readouterr().err
    assert "model changed since last night" in err


def test_no_warning_on_first_night(tmp_path, capsys) -> None:
    cfg = _cfg()
    state = SleepState.load(str(tmp_path / "state.json"))  # last_model_key == ""
    _check_model_change(cfg, state)
    assert "model changed" not in capsys.readouterr().err


def test_no_warning_when_model_same(tmp_path, capsys) -> None:
    cfg = _cfg()
    state = SleepState.load(str(tmp_path / "state.json"))
    state.set_last_model_key(_make_model_key(cfg))
    _check_model_change(cfg, state)
    assert "model changed" not in capsys.readouterr().err


def test_model_key_tracks_effective_optimizer_and_target_roles() -> None:
    cfg = load_config(
        backend="mock",
        model="shared",
        optimizer_backend="claude",
        optimizer_model="opus",
        target_backend="codex",
        target_model="gpt",
    )
    assert _make_model_key(cfg) == (
        "optimizer=claude::opus;target=codex::gpt"
    )

    inherited = load_config(
        backend="mock",
        model="shared",
        optimizer_backend="claude",
    )
    assert _make_model_key(inherited) == (
        "optimizer=claude::shared;target=mock::"
    )


def test_model_key_normalizes_aliases_and_tracks_environment_defaults(
    monkeypatch,
) -> None:
    monkeypatch.delenv("SKILLOPT_SLEEP_CLAUDE_MODEL", raising=False)
    claude = _make_model_key(load_config(backend="claude", model=""))
    anthropic = _make_model_key(load_config(backend="anthropic", model=""))
    assert claude == anthropic == "claude::sonnet"

    monkeypatch.setenv("SKILLOPT_SLEEP_CLAUDE_MODEL", "opus")
    assert _make_model_key(load_config(backend="claude", model="")) == "claude::opus"


def test_model_key_resolution_failure_uses_safe_config_fallback(monkeypatch) -> None:
    cycle_module = importlib.import_module("skillopt_sleep.cycle")

    def fail_to_build(**_kwargs):
        raise RuntimeError("diagnostic construction failed")

    monkeypatch.setattr(cycle_module, "build_backend", fail_to_build)
    cfg = load_config(
        backend="claude",
        model="sonnet",
        optimizer_backend="codex",
        optimizer_model="gpt",
    )
    assert cycle_module._make_model_key(cfg) == (
        "configured:optimizer=codex::gpt;target=claude::sonnet"
    )


def test_model_key_forwards_pi_executable_path(monkeypatch) -> None:
    cycle_module = importlib.import_module("skillopt_sleep.cycle")
    captured = {}

    real_build = cycle_module.build_backend

    def capture_without_recursion(**kwargs):
        captured.update(kwargs)
        return real_build(backend="mock")

    monkeypatch.setattr(cycle_module, "build_backend", capture_without_recursion)
    cfg = load_config(backend="pi", pi_path="/opt/custom/pi")

    assert cycle_module._make_model_key(cfg) == "mock::"
    assert captured["pi_path"] == "/opt/custom/pi"


def test_legacy_unresolved_model_key_migrates_without_false_warning(
    tmp_path, capsys
) -> None:
    state = SleepState.load(str(tmp_path / "state.json"))
    state.data["last_model_key"] = "claude::"
    state.data["last_model_key_format"] = 1

    _check_model_change(load_config(backend="claude", model=""), state)

    assert "model changed" not in capsys.readouterr().err


def test_warns_when_one_split_backend_role_changes(tmp_path, capsys) -> None:
    previous = load_config(
        optimizer_backend="claude",
        optimizer_model="opus",
        target_backend="codex",
        target_model="gpt",
    )
    current = load_config(
        optimizer_backend="claude",
        optimizer_model="opus",
        target_backend="cursor",
        target_model="composer",
    )
    state = SleepState.load(str(tmp_path / "state.json"))
    state.set_last_model_key(_make_model_key(previous))

    _check_model_change(current, state)

    assert "model changed since last night" in capsys.readouterr().err


def test_cli_api_key_emits_deprecation_warning() -> None:
    from scripts.train import load_config as train_load_config

    args = argparse.Namespace(
        config="configs/_base_/default.yaml",
        cfg_options=None,
        azure_openai_api_key="sk-secret-value",
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            train_load_config(args)
        except Exception:
            pass  # config loading may fail; we only assert the warning fired
    assert any(
        issubclass(w.category, DeprecationWarning)
        and "azure_openai_api_key" in str(w.message)
        for w in caught
    )


def test_cli_key_warnings_name_the_correct_environment_variable() -> None:
    from scripts.train import load_config as train_load_config

    cases = {
        "optimizer_azure_openai_api_key": "OPTIMIZER_AZURE_OPENAI_API_KEY",
        "target_azure_openai_api_key": "TARGET_AZURE_OPENAI_API_KEY",
        "qwen_chat_api_key": "QWEN_CHAT_API_KEY",
        "optimizer_qwen_chat_api_key": "OPTIMIZER_QWEN_CHAT_API_KEY",
        "target_qwen_chat_api_key": "TARGET_QWEN_CHAT_API_KEY",
        "minimax_api_key": "MINIMAX_API_KEY",
    }
    for flag, environment_variable in cases.items():
        args = argparse.Namespace(
            config="configs/_base_/default.yaml",
            cfg_options=None,
            **{flag: "secret-value"},
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                train_load_config(args)
            except Exception:
                pass
        messages = [
            str(w.message)
            for w in caught
            if issubclass(w.category, DeprecationWarning)
        ]
        assert any(environment_variable in message for message in messages), flag


def test_cfg_options_api_key_emits_safe_deprecation_warning() -> None:
    from scripts.train import load_config as train_load_config

    args = argparse.Namespace(
        config="configs/_base_/default.yaml",
        cfg_options=["model.azure_openai_api_key=do-not-print-this-secret"],
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            train_load_config(args)
        except Exception:
            pass
    messages = [
        str(w.message)
        for w in caught
        if issubclass(w.category, DeprecationWarning)
    ]
    assert any("AZURE_OPENAI_API_KEY" in message for message in messages)
    assert all("do-not-print-this-secret" not in message for message in messages)


def test_cfg_options_warning_uses_leaf_name_and_catches_future_secrets() -> None:
    from scripts.train import load_config as train_load_config

    args = argparse.Namespace(
        config="configs/_base_/default.yaml",
        cfg_options=[
            "custom.optimizer_qwen_chat_api_key=role-secret",
            "future.backend.access_token=future-secret",
        ],
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            train_load_config(args)
        except Exception:
            pass
    messages = [
        str(w.message)
        for w in caught
        if issubclass(w.category, DeprecationWarning)
    ]

    assert any("OPTIMIZER_QWEN_CHAT_API_KEY" in message for message in messages)
    assert any("future.backend.access_token" in message for message in messages)
    assert all("role-secret" not in message for message in messages)
    assert all("future-secret" not in message for message in messages)
