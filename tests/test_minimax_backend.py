"""Tests for the MiniMax backend, focusing on optimizer/target deployment
routing (regression for the #116 follow-up: optimizer calls must honour
OPTIMIZER_DEPLOYMENT, not silently reuse TARGET_DEPLOYMENT)."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from skillopt.model import minimax_backend as mm


def _fake_response(_payload, _timeout):
    return {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


class TestMiniMaxDeploymentRouting(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ)
        self._env.start()
        self._saved = (mm.TARGET_DEPLOYMENT, mm.OPTIMIZER_DEPLOYMENT)
        mm.set_target_deployment("target-model")
        mm.set_optimizer_deployment("optimizer-model")

    def tearDown(self):
        mm.TARGET_DEPLOYMENT, mm.OPTIMIZER_DEPLOYMENT = self._saved
        self._env.stop()

    def _captured_model(self, fn, *args, **kwargs):
        seen = {}

        def spy(payload, timeout):
            seen["model"] = payload["model"]
            return _fake_response(payload, timeout)

        with mock.patch.object(mm, "_post_chat_completion", side_effect=spy):
            fn(*args, **kwargs)
        return seen["model"]

    def test_target_text_uses_target_deployment(self):
        self.assertEqual(self._captured_model(mm.chat_target, "sys", "usr"), "target-model")

    def test_optimizer_text_uses_optimizer_deployment(self):
        # The core bug: before the fix this sent "target-model".
        self.assertEqual(self._captured_model(mm.chat_optimizer, "sys", "usr"), "optimizer-model")

    def test_optimizer_messages_uses_optimizer_deployment(self):
        msgs = [{"role": "user", "content": "hi"}]
        self.assertEqual(self._captured_model(mm.chat_optimizer_messages, msgs), "optimizer-model")

    def test_target_messages_uses_target_deployment(self):
        msgs = [{"role": "user", "content": "hi"}]
        self.assertEqual(self._captured_model(mm.chat_target_messages, msgs), "target-model")

    def test_optimizer_falls_back_to_target_when_unset(self):
        mm.set_optimizer_deployment("")
        self.assertEqual(self._captured_model(mm.chat_optimizer, "sys", "usr"), "target-model")
        mm.set_target_deployment("new-target")
        self.assertEqual(self._captured_model(mm.chat_optimizer_messages, []), "new-target")

    def test_facade_routes_both_optimizer_apis_and_setter(self):
        from skillopt import model
        with mock.patch.object(model, "get_optimizer_backend", return_value="minimax_chat"):
            self.assertEqual(self._captured_model(model.chat_optimizer, "sys", "usr"), "optimizer-model")
            self.assertEqual(self._captured_model(model.chat_optimizer_messages, []), "optimizer-model")
        with mock.patch.object(model._openai, "set_optimizer_deployment"), \
                mock.patch.object(model._claude, "set_optimizer_deployment"), \
                mock.patch.object(model._qwen, "set_optimizer_deployment"), \
                mock.patch.object(model._openai_compat, "set_optimizer_deployment"), \
                mock.patch.object(model._codex, "set_optimizer_deployment"):
            model.set_optimizer_deployment("other-optimizer")
        self.assertEqual(mm.OPTIMIZER_DEPLOYMENT, "other-optimizer")
        self.assertEqual(mm.TARGET_DEPLOYMENT, "target-model")


if __name__ == "__main__":
    unittest.main()
