"""Tests for the azure_openai backend's OpenAI-compatible mode.

Pure-stdlib (unittest), deterministic, NO network, no API key. Covers the
integration points requested in review of the openai-compatible feature:
  * CLI acceptance of --backend azure_openai
  * compat-vs-Azure client selection
  * endpoint resolution and the managed-identity credential guard
  * provider-neutral request kwargs (opt-in extra body / token cap)
  * retry-success error clearing + empty-response diagnostics
  * example runner exit-code propagation

Run:  python -m unittest tests.test_azure_openai_compat
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from skillopt_sleep.__main__ import _add_common
from skillopt_sleep.backend import AzureOpenAIBackend

try:
    import openai  # noqa: F401
    HAVE_OPENAI = True
except ImportError:
    HAVE_OPENAI = False

COMPAT_ENV = {
    "AZURE_OPENAI_AUTH_MODE": "openai_compatible",
    "AZURE_OPENAI_ENDPOINT": "https://compat.example.test",
    "AZURE_OPENAI_API_KEY": "sk-test-not-a-real-key",
}


def _resp(text):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=None,
    )


class _FakeCompletions:
    """Scripted chat.completions.create: replies are strings or Exceptions."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return _resp(item)


def _fake_client(replies):
    return SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions(replies)))


def _backend_with(replies, env):
    """Build a backend under `env` with a scripted fake client injected."""
    with mock.patch.dict(os.environ, env, clear=True):
        be = AzureOpenAIBackend(deployment="any-model")
    be._client = _fake_client(replies)
    return be


class TestCliAcceptance(unittest.TestCase):
    def test_backend_azure_openai_is_accepted(self):
        p = argparse.ArgumentParser()
        _add_common(p)
        ns = p.parse_args(["--backend", "azure_openai"])
        self.assertEqual(ns.backend, "azure_openai")


class TestClientSelection(unittest.TestCase):
    @unittest.skipUnless(HAVE_OPENAI, "openai package not installed")
    def test_compat_mode_builds_plain_openai_client(self):
        from openai import AzureOpenAI, OpenAI
        # Overlay (clear=False): constructing a real OpenAI client builds an
        # SSL context, which needs the ambient cert-related env vars intact.
        with mock.patch.dict(os.environ, COMPAT_ENV, clear=False):
            be = AzureOpenAIBackend(deployment="some-model", endpoint=COMPAT_ENV["AZURE_OPENAI_ENDPOINT"])
            client = be._get_client()
        self.assertIsInstance(client, OpenAI)
        self.assertNotIsInstance(client, AzureOpenAI)
        self.assertIn("compat.example.test", str(client.base_url))

    def test_managed_identity_refuses_non_azure_endpoint(self):
        # No compat auth mode + a custom non-Azure endpoint: the backend must
        # raise instead of sending an Azure AD bearer token to that host. The
        # guard fires before azure.identity is imported, so this test needs
        # neither azure-identity nor any network.
        env = {"AZURE_OPENAI_ENDPOINT": "https://api.deepseek.com"}
        with mock.patch.dict(os.environ, env, clear=True):
            be = AzureOpenAIBackend(deployment="some-model")
            with self.assertRaises(ValueError) as ctx:
                be._get_client()
        self.assertIn("openai_compatible", str(ctx.exception))

    def test_managed_identity_refuses_insecure_azure_endpoint(self):
        # A matching Azure hostname is insufficient: AAD bearer credentials
        # must never be sent over plaintext HTTP.
        env = {"AZURE_OPENAI_ENDPOINT": "http://foo.openai.azure.com"}
        with mock.patch.dict(os.environ, env, clear=True):
            be = AzureOpenAIBackend(deployment="some-model")
            self.assertFalse(be._is_azure_host())
            with self.assertRaises(ValueError) as ctx:
                be._get_client()
        self.assertIn("openai_compatible", str(ctx.exception))

    def test_azure_host_detection(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            be = AzureOpenAIBackend(deployment="gpt-5.5")  # table endpoint
            self.assertTrue(be._is_azure_host())
            be2 = AzureOpenAIBackend(
                deployment="gpt-5.5", endpoint="https://api.deepseek.com")
            self.assertFalse(be2._is_azure_host())


class TestEndpointResolution(unittest.TestCase):
    def test_env_endpoint_is_honored(self):
        env = {"AZURE_OPENAI_ENDPOINT": "https://compat.example.test"}
        with mock.patch.dict(os.environ, env, clear=True):
            be = AzureOpenAIBackend(deployment="some-model")
        self.assertEqual(be.endpoint, "https://compat.example.test")

    def test_explicit_arg_beats_env(self):
        env = {"AZURE_OPENAI_ENDPOINT": "https://env.example.test"}
        with mock.patch.dict(os.environ, env, clear=True):
            be = AzureOpenAIBackend(deployment="m", endpoint="https://arg.example.test")
        self.assertEqual(be.endpoint, "https://arg.example.test")

    def test_table_fallback_without_env(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            be = AzureOpenAIBackend(deployment="gpt-5.5")
        self.assertTrue(be.endpoint.endswith(".openai.azure.com/"))


class TestRequestKwargs(unittest.TestCase):
    def test_compat_mode_sends_standard_max_tokens(self):
        be = _backend_with(["hi"], COMPAT_ENV)
        with mock.patch.dict(os.environ, COMPAT_ENV, clear=True):
            out = be._call("p", retries=1)
        self.assertEqual(out, "hi")
        (call,) = be._client.chat.completions.calls
        self.assertEqual(call["max_tokens"], 8192)
        self.assertNotIn("max_completion_tokens", call)
        self.assertNotIn("extra_body", call)

    def test_compat_max_tokens_env_override(self):
        env = dict(COMPAT_ENV, SKILLOPT_SLEEP_COMPAT_MAX_TOKENS="4096")
        be = _backend_with(["hi"], env)
        with mock.patch.dict(os.environ, env, clear=True):
            be._call("p", retries=1)
        (call,) = be._client.chat.completions.calls
        self.assertEqual(call["max_tokens"], 4096)

    def test_extra_body_is_opt_in_via_env_not_model_name(self):
        # A deepseek-named model WITHOUT the env knob gets a pure standard
        # request (nothing inferred from the name)...
        with mock.patch.dict(os.environ, COMPAT_ENV, clear=True):
            be = AzureOpenAIBackend(deployment="deepseek-v4-pro")
            be._client = _fake_client(["hi"])
            be._call("p", retries=1)
        (call,) = be._client.chat.completions.calls
        self.assertNotIn("extra_body", call)
        # ...and any model WITH the env knob gets exactly the configured body.
        body = {"thinking": {"type": "enabled"}}
        env = dict(COMPAT_ENV, SKILLOPT_SLEEP_CHAT_EXTRA_BODY=json.dumps(body))
        be2 = _backend_with(["hi"], env)
        with mock.patch.dict(os.environ, env, clear=True):
            be2._call("p", retries=1)
        (call2,) = be2._client.chat.completions.calls
        self.assertEqual(call2["extra_body"], body)

    def test_malformed_extra_body_is_ignored(self):
        env = dict(COMPAT_ENV, SKILLOPT_SLEEP_CHAT_EXTRA_BODY="{not json")
        be = _backend_with(["hi"], env)
        with mock.patch.dict(os.environ, env, clear=True):
            be._call("p", retries=1)
        (call,) = be._client.chat.completions.calls
        self.assertNotIn("extra_body", call)

    def test_azure_mode_sends_max_completion_tokens(self):
        be = _backend_with(["hi"], {})  # no compat auth mode
        with mock.patch.dict(os.environ, {}, clear=True):
            be._call("p", retries=1)
        (call,) = be._client.chat.completions.calls
        self.assertEqual(call["max_completion_tokens"], 16384)
        self.assertNotIn("max_tokens", call)

    def test_azure_mode_ignores_compat_extra_body(self):
        body = {"thinking": {"type": "enabled"}}
        env = {"SKILLOPT_SLEEP_CHAT_EXTRA_BODY": json.dumps(body)}
        be = _backend_with(["hi"], env)
        with mock.patch.dict(os.environ, env, clear=True):
            be._call("p", retries=1)
        (call,) = be._client.chat.completions.calls
        self.assertNotIn("extra_body", call)


class TestErrorState(unittest.TestCase):
    def test_recovered_retry_clears_last_call_error(self):
        be = _backend_with([RuntimeError("transient boom"), "recovered"], COMPAT_ENV)
        with mock.patch.dict(os.environ, COMPAT_ENV, clear=True), \
                mock.patch("time.sleep"):
            out = be._call("p", retries=2)
        self.assertEqual(out, "recovered")
        self.assertEqual(be.last_call_error, "")

    def test_all_empty_responses_set_diagnostic(self):
        be = _backend_with(["", ""], COMPAT_ENV)
        with mock.patch.dict(os.environ, COMPAT_ENV, clear=True), \
                mock.patch("time.sleep"):
            out = be._call("p", retries=2)
        self.assertEqual(out, "")
        self.assertIn("empty response on all 2 attempts", be.last_call_error)

    def test_persistent_exception_is_surfaced(self):
        be = _backend_with([RuntimeError("boom-1"), RuntimeError("boom-2")], COMPAT_ENV)
        with mock.patch.dict(os.environ, COMPAT_ENV, clear=True), \
                mock.patch("time.sleep"):
            out = be._call("p", retries=2)
        self.assertEqual(out, "")
        self.assertIn("boom-2", be.last_call_error)


class TestRunnerExitCode(unittest.TestCase):
    RUNNER = Path(__file__).resolve().parent.parent / "docs" / "sleep" / "examples" / "runner.py"

    def _load_runner(self):
        spec = importlib.util.spec_from_file_location("example_runner", self.RUNNER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _run_with_child_rc(self, rc):
        env = {"DEEPSEEK_API_KEY": "sk-test-not-a-real-key"}
        with mock.patch.dict(os.environ, env, clear=True):
            mod = self._load_runner()
            fake = SimpleNamespace(run=lambda *a, **k: SimpleNamespace(returncode=rc))
            with mock.patch.object(mod, "subprocess", fake), \
                    mock.patch.object(sys, "argv", ["runner.py", "run"]), \
                    contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as ctx:
                    mod.main()
        return ctx.exception.code

    def test_child_failure_propagates(self):
        self.assertEqual(self._run_with_child_rc(7), 7)

    def test_child_success_exits_zero(self):
        self.assertEqual(self._run_with_child_rc(0), 0)


if __name__ == "__main__":
    unittest.main()
