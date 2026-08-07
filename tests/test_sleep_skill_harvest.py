"""Tests for Claude ``Skill`` tool-use harvesting (issue #120).

Pure-stdlib (unittest), deterministic, no API key, no third-party deps.
Run:  python -m pytest tests/test_sleep_skill_harvest.py
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from skillopt_sleep.harvest import digest_transcript
from skillopt_sleep.types import SessionDigest


def _skill_block(skill, name="Skill", block_type="tool_use", extra=None):
    args = {"skill": skill} if skill is not None else {}
    if extra:
        args.update(extra)
    return {"type": block_type, "name": name, "input": args}


def _assistant(content):
    return {
        "type": "assistant",
        "timestamp": "2026-07-28T10:00:00Z",
        "cwd": "/repo/example",
        "gitBranch": "main",
        "message": {"role": "assistant", "content": content},
    }


def _user(text):
    return {
        "type": "user",
        "timestamp": "2026-07-28T09:59:00Z",
        "cwd": "/repo/example",
        "message": {"role": "user", "content": text},
    }


class TestSessionDigestSkillsField(unittest.TestCase):
    def test_defaults_to_empty_list(self):
        digest = SessionDigest(session_id="s1", project="/repo/example")
        self.assertEqual(digest.skills_used, [])

    def test_to_dict_includes_empty_skills_used(self):
        digest = SessionDigest(session_id="s1", project="/repo/example")
        self.assertIn("skills_used", digest.to_dict())
        self.assertEqual(digest.to_dict()["skills_used"], [])

    def test_legacy_payload_without_skills_used_still_loads(self):
        legacy = {"session_id": "s1", "project": "/repo/example", "tools_used": ["Bash"]}
        known = set(SessionDigest.__dataclass_fields__)
        digest = SessionDigest(**{k: v for k, v in legacy.items() if k in known})
        self.assertEqual(digest.skills_used, [])
        self.assertEqual(digest.tools_used, ["Bash"])

    def test_unknown_key_filter_can_ignore_new_field(self):
        payload = SessionDigest(
            session_id="s1", project="/repo/example", skills_used=["example-skill"]
        ).to_dict()
        older_known = {
            f for f in SessionDigest.__dataclass_fields__ if f != "skills_used"
        }
        digest = SessionDigest(**{k: v for k, v in payload.items() if k in older_known})
        self.assertEqual(digest.skills_used, [])


class TestSkillHarvest(unittest.TestCase):
    def _digest(self, records):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "session-example.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                for record in records:
                    f.write(json.dumps(record) + "\n")
            return digest_transcript(path)

    def test_harvests_skill_invocation(self):
        digest = self._digest([
            _user("run the example skill"),
            _assistant([_skill_block("example-skill")]),
        ])
        self.assertIsNotNone(digest)
        self.assertEqual(digest.skills_used, ["example-skill"])
        # tools_used keeps its existing meaning, including the Skill tool itself
        self.assertEqual(digest.tools_used, ["Skill"])

    def test_duplicates_collapse_in_first_seen_order(self):
        digest = self._digest([
            _user("run both skills"),
            _assistant([
                _skill_block("second-skill"),
                _skill_block("example-skill"),
                _skill_block("second-skill"),
            ]),
        ])
        self.assertEqual(digest.skills_used, ["second-skill", "example-skill"])

    def test_whitespace_trimmed_without_rewriting_name(self):
        digest = self._digest([
            _user("run it"),
            _assistant([_skill_block("  Example-Skill:v2  ")]),
        ])
        self.assertEqual(digest.skills_used, ["Example-Skill:v2"])

    def test_ordinary_tools_never_populate_skills_used(self):
        digest = self._digest([
            _user("read the file"),
            _assistant([
                {"type": "tool_use", "name": "Read", "input": {"file_path": "/repo/a.py"}},
                {"type": "text", "text": "I used the Skill tool description here"},
            ]),
        ])
        self.assertEqual(digest.skills_used, [])
        self.assertEqual(digest.tools_used, ["Read"])

    def test_malformed_blocks_are_ignored(self):
        digest = self._digest([
            _user("run it"),
            _assistant([
                _skill_block("lowercase-tool", name="skill"),
                _skill_block("wrong-type", block_type="tool_result"),
                {"type": "tool_use", "name": "Skill"},
                {"type": "tool_use", "name": "Skill", "input": "example-skill"},
                {"type": "tool_use", "name": "Skill", "input": {"skill": 7}},
                _skill_block("   "),
                _skill_block(None),
                "not-a-block",
            ]),
        ])
        self.assertEqual(digest.skills_used, [])

    def test_malformed_jsonl_record_is_non_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "session-example.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps(_user("run it")) + "\n")
                f.write("{not json\n")
                f.write("\n")
                f.write(json.dumps(_assistant([_skill_block("example-skill")])) + "\n")
            digest = digest_transcript(path)
        self.assertIsNotNone(digest)
        self.assertEqual(digest.skills_used, ["example-skill"])

    def test_other_tool_arguments_and_outputs_stay_out_of_the_digest(self):
        digest = self._digest([
            _user("run it"),
            _assistant([
                _skill_block("example-skill", extra={"secret_arg": "do-not-copy"}),
                {"type": "tool_result", "content": "output should not copy"},
            ]),
        ])
        serialized = json.dumps(digest.to_dict())
        self.assertEqual(digest.skills_used, ["example-skill"])
        self.assertNotIn("do-not-copy", serialized)
        self.assertNotIn("output should not copy", serialized)


if __name__ == "__main__":
    unittest.main()
