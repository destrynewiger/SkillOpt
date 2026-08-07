"""Tests for the Devin MCP plugin: tool schema, ATIF-v1.7 harvest, path expansion."""
import importlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

# Allow importing from the plugin directory (mirrors tests/test_mcp_schema.py)
PLUGIN = os.path.join(os.path.dirname(__file__), "..", "plugins", "devin")
sys.path.insert(0, PLUGIN)

import mcp_server            # noqa: E402
import harvest_devin as hw   # noqa: E402

FIXTURES = os.path.join(PLUGIN, "fixtures")
INSTALLER = os.path.join(PLUGIN, "install.sh")


def _read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _find_session_jsonl(out_dir):
    for root, _dirs, files in os.walk(os.path.join(out_dir, "projects")):
        for name in files:
            if name.endswith(".jsonl"):
                return _read_jsonl(os.path.join(root, name))
    raise AssertionError("no session jsonl written")


class TestDevinMcpSchema(unittest.TestCase):
    def test_tools_are_the_sleep_interface(self):
        names = {t["name"] for t in mcp_server.TOOLS}
        self.assertEqual(names, {"sleep_status", "sleep_dry_run", "sleep_run",
                                 "sleep_adopt", "sleep_harvest",
                                 "sleep_schedule", "sleep_unschedule"})

    def test_actions_map_to_engine_subcommands(self):
        expected = {"sleep_status": "status", "sleep_dry_run": "dry-run",
                    "sleep_run": "run", "sleep_adopt": "adopt",
                    "sleep_harvest": "harvest", "sleep_schedule": "schedule",
                    "sleep_unschedule": "unschedule"}
        for t in mcp_server.TOOLS:
            self.assertEqual(t["action"], expected[t["name"]])

    def test_backends_in_enum(self):
        backends = mcp_server._TOOL_SCHEMA["properties"]["backend"]["enum"]
        for b in ["mock", "claude", "codex", "copilot", "handoff"]:
            self.assertIn(b, backends)

    def test_schema_has_key_engine_params(self):
        # parity with plugins/copilot's schema (tests/test_plugin_sync.py)
        props = set(mcp_server._TOOL_SCHEMA["properties"].keys())
        for param in {"project", "backend", "scope", "source", "model",
                      "tasks_file", "target_skill_path", "max_sessions",
                      "max_tasks", "lookback_hours", "auto_adopt", "json",
                      "edit_budget", "hour", "minute"}:
            self.assertIn(param, props)


class TestClaudeHomeExpansion(unittest.TestCase):
    """Regression: ~ must be expanded even when CLAUDE_HOME comes from the env
    (the documented mcp-config sets SKILLOPT_DEVIN_CLAUDE_HOME="~/...")."""

    def test_env_tilde_is_expanded(self):
        # Re-insert the devin plugin path at position 0 so importlib.reload
        # picks up this module, not plugins/copilot/mcp_server.py when both
        # test modules are loaded in the same process.
        sys.path.insert(0, PLUGIN)
        os.environ["SKILLOPT_DEVIN_CLAUDE_HOME"] = "~/.skillopt-sleep-devin"
        try:
            importlib.reload(mcp_server)
            self.assertFalse(mcp_server.CLAUDE_HOME.startswith("~"))
            self.assertEqual(mcp_server.CLAUDE_HOME,
                             os.path.expanduser("~/.skillopt-sleep-devin"))
        finally:
            del os.environ["SKILLOPT_DEVIN_CLAUDE_HOME"]
            importlib.reload(mcp_server)


class TestDevinInstaller(unittest.TestCase):
    def _run_installer(self, project, home, installer=INSTALLER):
        env = os.environ.copy()
        env["HOME"] = home
        return subprocess.run(
            ["bash", installer, project],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )

    @staticmethod
    def _skillopt_hook():
        config_path = os.path.join(PLUGIN, "hooks", "hooks.v1.json")
        with open(config_path, encoding="utf-8") as f:
            return json.load(f)["SessionEnd"][0]

    def test_new_install_and_hook_marker(self):
        with tempfile.TemporaryDirectory() as d:
            project = os.path.join(d, "project with spaces")
            home = os.path.join(d, "home")
            os.makedirs(project)
            os.makedirs(home)

            self._run_installer(project, home)

            config_path = os.path.join(project, ".devin", "hooks.v1.json")
            with open(config_path, encoding="utf-8") as f:
                config = json.load(f)
            self.assertEqual(config["SessionEnd"], [self._skillopt_hook()])

            hook_path = os.path.join(
                project, ".devin", "hooks", "skillopt-sleep-on-session-end.sh"
            )
            self.assertTrue(os.stat(hook_path).st_mode & stat.S_IXUSR)
            env = os.environ.copy()
            env.update(HOME=home, DEVIN_PROJECT_DIR=project)
            subprocess.run([hook_path], check=True, env=env)
            marker = os.path.join(home, ".skillopt-sleep", "session-end.log")
            with open(marker, encoding="utf-8") as f:
                lines = f.readlines()
            self.assertEqual(len(lines), 1)
            self.assertTrue(lines[0].endswith(f"\t{project}\n"))

    def test_hook_is_non_blocking_without_home(self):
        env = os.environ.copy()
        env.pop("HOME", None)
        result = subprocess.run(
            [os.path.join(PLUGIN, "hooks", "on-session-end.sh")],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")

    def test_existing_config_without_session_end_is_extended(self):
        unrelated = [
            {"matcher": "", "hooks": [{"type": "command", "command": "./pre.sh"}]}
        ]
        with tempfile.TemporaryDirectory() as d:
            project = os.path.join(d, "project")
            home = os.path.join(d, "home")
            devin_dir = os.path.join(project, ".devin")
            os.makedirs(devin_dir)
            os.makedirs(home)
            config_path = os.path.join(devin_dir, "hooks.v1.json")
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump({"PreToolUse": unrelated}, f)

            self._run_installer(project, home)

            with open(config_path, encoding="utf-8") as f:
                config = json.load(f)
            self.assertEqual(config["PreToolUse"], unrelated)
            self.assertEqual(config["SessionEnd"], [self._skillopt_hook()])

    def test_existing_hooks_are_preserved_and_reinstall_is_idempotent(self):
        existing_session_end = {
            "matcher": "existing",
            "hooks": [{"type": "command", "command": "./existing.sh"}],
        }
        unrelated = [
            {"matcher": "", "hooks": [{"type": "command", "command": "./pre.sh"}]}
        ]
        with tempfile.TemporaryDirectory() as d:
            project = os.path.join(d, "project")
            home = os.path.join(d, "home")
            devin_dir = os.path.join(project, ".devin")
            os.makedirs(devin_dir)
            os.makedirs(home)
            hooks_dir = os.path.join(devin_dir, "hooks")
            os.makedirs(hooks_dir)
            legacy_hook = os.path.join(hooks_dir, "on-session-end.sh")
            with open(legacy_hook, "w", encoding="utf-8") as f:
                f.write("#!/bin/sh\n# existing project hook\n")
            config_path = os.path.join(devin_dir, "hooks.v1.json")
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"PreToolUse": unrelated, "SessionEnd": [existing_session_end]}, f
                )

            self._run_installer(project, home)
            self._run_installer(project, home)

            with open(config_path, encoding="utf-8") as f:
                config = json.load(f)
            self.assertEqual(config["PreToolUse"], unrelated)
            self.assertIn(existing_session_end, config["SessionEnd"])
            self.assertEqual(config["SessionEnd"].count(self._skillopt_hook()), 1)
            with open(legacy_hook, encoding="utf-8") as f:
                self.assertEqual(f.read(), "#!/bin/sh\n# existing project hook\n")

    def test_malformed_existing_config_fails_without_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            project = os.path.join(d, "project")
            home = os.path.join(d, "home")
            devin_dir = os.path.join(project, ".devin")
            os.makedirs(devin_dir)
            os.makedirs(home)
            config_path = os.path.join(devin_dir, "hooks.v1.json")
            original = "{not-json\n"
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(original)

            with self.assertRaises(subprocess.CalledProcessError):
                self._run_installer(project, home)
            with open(config_path, encoding="utf-8") as f:
                self.assertEqual(f.read(), original)

    def test_registration_path_is_shell_quoted(self):
        with tempfile.TemporaryDirectory() as d:
            plugin_copy = os.path.join(
                d, "repo with spaces $dollar `tick` 'quote'", "plugins", "devin"
            )
            shutil.copytree(PLUGIN, plugin_copy)
            project = os.path.join(d, "project")
            home = os.path.join(d, "home")
            os.makedirs(project)
            os.makedirs(home)

            result = self._run_installer(
                project,
                home,
                installer=os.path.join(plugin_copy, "install.sh"),
            )

            command_line = next(
                line.strip()
                for line in result.stdout.splitlines()
                if line.strip().startswith("-- python3 ")
            )
            self.assertEqual(
                shlex.split(command_line),
                ["--", "python3", os.path.join(plugin_copy, "mcp_server.py")],
            )


class TestDevinHarvest(unittest.TestCase):
    def test_atif_fixture_yields_gradeable_task(self):
        with tempfile.TemporaryDirectory() as out:
            n = hw.harvest_devin_transcripts(FIXTURES, out, ["/tmp/proj"])
            self.assertEqual(n, 1)

            outcomes = _read_jsonl(os.path.join(out, "outcomes.jsonl"))
            self.assertEqual(len(outcomes), 1)
            o = outcomes[0]
            self.assertEqual(o["verifier"], "tests")
            self.assertTrue(o["success"])
            self.assertIn("repro", o["reference"])

            # the converted transcript carries the grouping key on the user turn
            session = _find_session_jsonl(out)
            user_turn = next(r for r in session if r["type"] == "user")
            self.assertIn("taskKey", user_turn)


if __name__ == "__main__":
    unittest.main()
