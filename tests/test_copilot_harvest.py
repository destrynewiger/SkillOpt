"""Tests for read-only VS Code GitHub Copilot Chat harvesting."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime
from unittest import mock

from skillopt_sleep.config import load_config
from skillopt_sleep.harvest_copilot import (
    default_vscode_workspace_storage_roots,
    digest_copilot_session,
    discover_vscode_sessions,
    harvest_copilot,
    reconstruct_chat_session,
    _iso_epoch,
)
from skillopt_sleep.harvest_sources import harvest_for_config

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "copilot")


class TestCopilotHarvest(unittest.TestCase):
    def _storage_fixture(self, root: str, workspace: str = "workspace-a") -> tuple[str, str]:
        workspace_dir = os.path.join(root, workspace)
        sessions_dir = os.path.join(workspace_dir, "chatSessions")
        os.makedirs(sessions_dir)
        shutil.copy(os.path.join(FIXTURES, "workspace.json"), workspace_dir)
        session_path = os.path.join(sessions_dir, "fixture-session.jsonl")
        shutil.copy(os.path.join(FIXTURES, "session-v3.jsonl"), session_path)
        return workspace_dir, session_path

    def test_reconstructs_v3_operations_and_tolerates_truncated_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            _workspace, path = self._storage_fixture(tmp)
            with open(path, "ab") as f:
                f.write(b'{"kind":2,"k":["requests"],"v":[')

            session = reconstruct_chat_session(path)

        self.assertIsNotNone(session)
        self.assertEqual(session["sessionId"], "fixture-session")
        self.assertEqual(len(session["requests"]), 3)
        response = session["requests"][0]["response"]
        self.assertEqual(response[0]["value"], "Implemented safely with password: assistant-secret.")
        self.assertNotIn("obsolete partial answer", json.dumps(session))
        self.assertEqual(session["requests"][0]["modelState"]["completedAt"], 1700000010000)

    def test_reconstructs_delete_and_array_truncation_operations(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "operations.jsonl")
            records = [
                {"kind": 0, "v": {"metadata": {"temporary": True}, "items": [1, 2, 3]}},
                {"kind": 3, "k": ["metadata", "temporary"]},
                {"kind": 2, "k": ["items"], "i": 1},
            ]
            with open(path, "w", encoding="utf-8") as f:
                for record in records:
                    f.write(json.dumps(record) + "\n")

            session = reconstruct_chat_session(path)

        self.assertEqual(session, {"metadata": {}, "items": [1]})

    def test_digest_extracts_visible_content_and_enforces_privacy_boundary(self):
        path = os.path.join(FIXTURES, "session-v3.jsonl")

        digest = digest_copilot_session(path, project="/tmp/copilot-project")

        self.assertIsNotNone(digest)
        self.assertEqual(digest.session_id, "fixture-session")
        self.assertEqual(digest.project, "/tmp/copilot-project")
        self.assertEqual(digest.n_user_turns, 1)
        self.assertEqual(digest.n_assistant_turns, 1)
        self.assertEqual(digest.tools_used, ["copilot_readFile"])
        self.assertTrue(any(signal == "neg:still broken" for signal in digest.feedback_signals))
        joined = "\n".join(digest.user_prompts + digest.assistant_finals)
        self.assertIn("[REDACTED_OPENAI_KEY]", joined)
        self.assertIn("token [REDACTED]", joined)
        self.assertIn("password: [REDACTED]", joined)
        for excluded in (
            "obsolete partial answer",
            "private chain of thought",
            "raw tool arguments",
            "raw tool output",
            "private result details",
            "private system context",
            "private rendered message",
            "private tool result",
            "private machine context",
            "orphan response",
            "foreign provider prompt",
            "foreign provider response",
        ):
            self.assertNotIn(excluded, joined)

    def test_digest_rejects_sessions_without_confirmed_copilot_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "other.jsonl")
            snapshot = {
                "kind": 0,
                "v": {
                    "version": 3,
                    "sessionId": "other",
                    "requests": [{
                        "agent": {"extensionId": {"_lower": "example.other-chat"}},
                        "message": {"text": "do not harvest"},
                        "response": [{"value": "not Copilot"}],
                    }],
                },
            }
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps(snapshot) + "\n")

            digest = digest_copilot_session(path)

        self.assertIsNone(digest)

    def test_digest_skips_system_initiated_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "system-request.jsonl")
            snapshot = {
                "kind": 0,
                "v": {
                    "version": 3,
                    "sessionId": "system-request",
                    "requests": [
                        {
                            "timestamp": 1700000001000,
                            "agent": {"extensionId": {"_lower": "github.copilot-chat"}},
                            "message": {"text": "real user prompt"},
                            "response": [{"value": "visible answer"}],
                        },
                        {
                            "timestamp": 1700000009000,
                            "isSystemInitiated": True,
                            "agent": {"extensionId": {"_lower": "github.copilot-chat"}},
                            "message": {"text": "terminal command completed"},
                            "response": [{"value": "system notification response"}],
                        },
                    ],
                },
            }
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps(snapshot) + "\n")

            digest = digest_copilot_session(path, project="/tmp/copilot-project")

        self.assertIsNotNone(digest)
        self.assertEqual(digest.user_prompts, ["real user prompt"])
        self.assertEqual(digest.assistant_finals, ["visible answer"])
        self.assertEqual(digest.ended_at, "2023-11-14T22:13:21Z")
        self.assertEqual(digest.n_user_turns, 1)
        self.assertEqual(digest.n_assistant_turns, 1)

    def test_discovers_sessions_and_resolves_workspace_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            _workspace, path = self._storage_fixture(tmp)

            found = discover_vscode_sessions(tmp)

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].path, path)
        self.assertEqual(found[0].project, os.path.abspath("/tmp/copilot-project"))

    def test_workspace_folder_uri_is_decoded(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace_dir, _path = self._storage_fixture(tmp)
            with open(os.path.join(workspace_dir, "workspace.json"), "w", encoding="utf-8") as f:
                json.dump({"folder": "file:///tmp/copilot%20project"}, f)

            found = discover_vscode_sessions(tmp)

        self.assertEqual(found[0].project, os.path.abspath("/tmp/copilot project"))

    def test_harvest_filters_scope_time_and_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._storage_fixture(tmp)
            shutil.copytree(os.path.join(tmp, "workspace-a"), os.path.join(tmp, "workspace-b"))

            digests = harvest_copilot(
                tmp,
                scope="invoked",
                invoked_project="/tmp/copilot-project/subdir",
                limit=1,
            )
            old_digests = harvest_copilot(tmp, scope="all", since_iso="2024-01-01T00:00:00Z")

        self.assertEqual(len(digests), 1)
        self.assertEqual(digests[0].project, os.path.abspath("/tmp/copilot-project"))
        self.assertEqual(old_digests, [])

    def test_harvest_compares_fractional_timestamps_chronologically(self):
        with tempfile.TemporaryDirectory() as tmp:
            _workspace, path = self._storage_fixture(tmp)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "kind": 1,
                    "k": ["requests", 0, "modelState", "completedAt"],
                    "v": 1700000010500,
                }) + "\n")

            included = harvest_copilot(
                tmp, scope="all", since_iso="2023-11-14T22:13:30Z"
            )
            excluded = harvest_copilot(
                tmp, scope="all", since_iso="2023-11-14T22:13:30.750000Z"
            )
            offset_included = harvest_copilot(
                tmp, scope="all", since_iso="2023-11-14T17:13:30-05:00"
            )

        self.assertEqual(len(included), 1)
        self.assertEqual(included[0].ended_at, "2023-11-14T22:13:30.500000Z")
        self.assertEqual(excluded, [])
        self.assertEqual(len(offset_included), 1)

    @unittest.skipUnless(hasattr(time, "tzset"), "requires POSIX timezone control")
    def test_naive_checkpoint_uses_local_timezone_like_sleep_state(self):
        previous_tz = os.environ.get("TZ")
        try:
            # Force a non-UTC local zone so treating the naive value as UTC
            # would make this assertion fail.
            os.environ["TZ"] = "UTC-5"
            time.tzset()
            cutoff = 1_700_000_000
            local_checkpoint = datetime.fromtimestamp(cutoff).isoformat()

            self.assertEqual(_iso_epoch(local_checkpoint), cutoff)
        finally:
            if previous_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = previous_tz
            time.tzset()

    def test_missing_storage_returns_empty_result(self):
        self.assertEqual(discover_vscode_sessions("/definitely/missing/workspaceStorage"), [])
        self.assertEqual(harvest_copilot("/definitely/missing/workspaceStorage"), [])

    def test_harvest_skips_sessions_without_project_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace_dir, _path = self._storage_fixture(tmp)
            os.remove(os.path.join(workspace_dir, "workspace.json"))

            discovered = discover_vscode_sessions(tmp)
            digests = harvest_copilot(tmp, scope="all")

        self.assertEqual(len(discovered), 1)
        self.assertEqual(discovered[0].project, "")
        self.assertEqual(digests, [])

    def test_platform_storage_candidates_include_stable_and_insiders(self):
        mac = default_vscode_workspace_storage_roots(
            platform="darwin", env={}, home="/Users/test"
        )
        linux = default_vscode_workspace_storage_roots(
            platform="linux", env={"XDG_CONFIG_HOME": "/config"}, home="/home/test"
        )
        windows = default_vscode_workspace_storage_roots(
            platform="win32", env={"APPDATA": "C:/Users/test/AppData/Roaming"}, home="C:/Users/test"
        )

        for candidates in (mac, linux, windows):
            self.assertEqual(len(candidates), 2)
            self.assertTrue(candidates[0].endswith(os.path.join("User", "workspaceStorage")))
            self.assertIn("Insiders", candidates[1])
        self.assertIn(os.path.join("Application Support", "Code"), mac[0])
        self.assertTrue(linux[0].startswith("/config"))
        self.assertIn("AppData", windows[0])

    def test_explicit_empty_environment_remains_isolated(self):
        with mock.patch.dict(
            os.environ,
            {
                "XDG_CONFIG_HOME": "/host-config",
                "VSCODE_PORTABLE": "/host-portable",
            },
            clear=True,
        ):
            candidates_by_platform = {
                platform: default_vscode_workspace_storage_roots(
                    platform=platform, env={}, home=home
                )
                for platform, home in (
                    ("linux", "/home/test"),
                    ("darwin", "/Users/test"),
                    ("win32", "C:/Users/test"),
                )
            }

        for candidates in candidates_by_platform.values():
            self.assertEqual(len(candidates), 2)
            self.assertNotIn("/host-config", "\n".join(candidates))
            self.assertNotIn("/host-portable", "\n".join(candidates))
        self.assertEqual(
            candidates_by_platform["linux"][0],
            os.path.join("/home/test", ".config", "Code", "User", "workspaceStorage"),
        )

    def test_portable_storage_uses_vscode_portable_data_root(self):
        candidates = default_vscode_workspace_storage_roots(
            platform="linux",
            env={"VSCODE_PORTABLE": "/opt/vscode/data"},
            home="/home/test",
        )

        self.assertEqual(
            candidates[0],
            os.path.join("/opt/vscode/data", "user-data", "User", "workspaceStorage"),
        )

    def test_cli_config_and_source_router(self):
        from skillopt_sleep.__main__ import _add_common, _cfg_from_args

        with tempfile.TemporaryDirectory() as tmp:
            self._storage_fixture(tmp)
            parser = argparse.ArgumentParser()
            _add_common(parser)
            args = parser.parse_args([
                "--project", "/tmp/copilot-project",
                "--source", "copilot",
                "--vscode-workspace-storage", tmp,
            ])
            cfg = _cfg_from_args(args)
            digests = harvest_for_config(cfg, limit=10)

        self.assertEqual(cfg.get("transcript_source"), "copilot")
        self.assertEqual(cfg.vscode_workspace_storage, os.path.abspath(tmp))
        self.assertEqual(len(digests), 1)
        self.assertEqual(digests[0].session_id, "fixture-session")

    def test_documented_user_config_keys_route_copilot_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._storage_fixture(tmp)
            config_path = os.path.join(tmp, "config.json")
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump({
                    "transcript_source": "copilot",
                    "vscode_workspace_storage": tmp,
                    "projects": "invoked",
                }, f)
            with mock.patch(
                "skillopt_sleep.config._user_config_path", return_value=config_path
            ):
                cfg = load_config(invoked_project="/tmp/copilot-project")
                digests = harvest_for_config(cfg, limit=10)

        self.assertEqual(cfg.get("transcript_source"), "copilot")
        self.assertEqual(cfg.vscode_workspace_storage, os.path.abspath(tmp))
        self.assertEqual(len(digests), 1)

    def test_auto_source_behavior_remains_codex_then_claude(self):
        cfg = load_config(
            transcript_source="auto",
            codex_home="/missing/codex",
            claude_home="/missing/claude",
            vscode_workspace_storage="/missing/vscode",
        )
        with mock.patch("skillopt_sleep.harvest_sources.harvest_copilot") as copilot:
            self.assertEqual(harvest_for_config(cfg), [])
        copilot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
