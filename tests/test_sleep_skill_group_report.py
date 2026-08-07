"""Tests for per-skill-group gate reporting (issue #120).

Pure-stdlib (unittest), deterministic MockBackend, no API key, no network.
Run:  python -m pytest tests/test_sleep_skill_group_report.py
"""
from __future__ import annotations

import unittest

from skillopt_sleep.backend import MockBackend
from skillopt_sleep.consolidate import ConsolidationResult
from skillopt_sleep.experiments.personas import researcher_persona
from skillopt_sleep.memory import set_learned
from skillopt_sleep.mine import assign_splits
from skillopt_sleep.multi_skill import (
    CONSOLIDATED,
    FAILED,
    SKIPPED,
    GroupConsolidation,
    SkillGroup,
    consolidate_groups,
    skill_group_reports,
)
from skillopt_sleep.types import EditRecord, SkillGroupReport, SleepReport


def _tasks(seed=42):
    return assign_splits(researcher_persona(), holdout_fraction=0.34, seed=seed)


def _result(accepted, action, baseline, candidate, applied=0, rejected=0):
    return ConsolidationResult(
        accepted=accepted, gate_action=action, baseline_score=baseline,
        candidate_score=candidate, new_skill="", new_memory="",
        applied_edits=[EditRecord("skill", "add", f"rule {i}") for i in range(applied)],
        rejected_edits=[EditRecord("skill", "add", f"bad {i}") for i in range(rejected)],
        holdout_baseline=baseline, holdout_candidate=candidate,
    )


class TestSkillGroupReport(unittest.TestCase):
    def test_row_defaults_are_empty_evidence(self):
        row = SkillGroupReport(skill_name="example-skill")
        self.assertEqual(
            (row.status, row.accepted, row.gate_action, row.baseline_score,
             row.candidate_score, row.n_tasks, row.n_applied_edits,
             row.n_rejected_edits, row.reason),
            ("", False, "", 0.0, 0.0, 0, 0, 0, ""),
        )
        self.assertEqual(row.to_dict()["skill_name"], "example-skill")


class TestSkillGroupRows(unittest.TestCase):
    def test_each_group_keeps_its_own_scores_and_decision(self):
        outcomes = {
            "strong-skill": GroupConsolidation(
                "strong-skill", CONSOLIDATED, n_tasks=6,
                result=_result(True, "accept_new_best", 0.25, 0.75, applied=2),
            ),
            "weak-skill": GroupConsolidation(
                "weak-skill", CONSOLIDATED, n_tasks=3,
                result=_result(False, "reject", 0.5, 0.1, rejected=1),
            ),
        }
        rows = {r.skill_name: r for r in skill_group_reports(outcomes)}
        strong, weak = rows["strong-skill"], rows["weak-skill"]
        self.assertTrue(strong.accepted)
        self.assertEqual((strong.baseline_score, strong.candidate_score), (0.25, 0.75))
        self.assertEqual((strong.n_tasks, strong.n_applied_edits), (6, 2))
        self.assertFalse(weak.accepted)
        self.assertEqual((weak.baseline_score, weak.candidate_score), (0.5, 0.1))
        self.assertEqual((weak.n_applied_edits, weak.n_rejected_edits), (0, 1))
        self.assertEqual(weak.gate_action, "reject")

    def test_rows_follow_first_seen_group_order(self):
        outcomes = {
            "b-skill": GroupConsolidation("b-skill", SKIPPED, reason="no mined tasks"),
            "a-skill": GroupConsolidation("a-skill", SKIPPED, reason="no mined tasks"),
        }
        self.assertEqual([r.skill_name for r in skill_group_reports(outcomes)],
                         ["b-skill", "a-skill"])

    def test_skipped_and_failed_rows_carry_reasons_and_no_borrowed_scores(self):
        outcomes = {
            "strong-skill": GroupConsolidation(
                "strong-skill", CONSOLIDATED, n_tasks=6,
                result=_result(True, "accept_new_best", 0.25, 0.75, applied=2),
            ),
            "empty-skill": GroupConsolidation(
                "empty-skill", SKIPPED, reason="no mined tasks for this skill"),
            "broken-skill": GroupConsolidation(
                "broken-skill", FAILED, reason="RuntimeError: backend exploded", n_tasks=4),
        }
        rows = {r.skill_name: r for r in skill_group_reports(outcomes)}
        for name in ("empty-skill", "broken-skill"):
            self.assertFalse(rows[name].accepted)
            self.assertEqual(rows[name].gate_action, "")
            self.assertEqual((rows[name].baseline_score, rows[name].candidate_score), (0.0, 0.0))
            self.assertTrue(rows[name].reason)
        self.assertEqual(rows["broken-skill"].status, FAILED)
        self.assertEqual(rows["broken-skill"].n_tasks, 4)
        self.assertTrue(rows["strong-skill"].accepted)

    def test_rows_from_a_real_mixed_night(self):
        outcomes = consolidate_groups(
            MockBackend(),
            [
                SkillGroup("research-skill", set_learned("", []), _tasks()),
                SkillGroup("empty-skill", set_learned("", []), []),
            ],
            edit_budget=4, gate_metric="mixed", night=1,
        )
        rows = skill_group_reports(outcomes)
        self.assertEqual([r.skill_name for r in rows], ["research-skill", "empty-skill"])
        research = rows[0]
        self.assertEqual(research.status, CONSOLIDATED)
        self.assertTrue(research.accepted)
        self.assertGreater(research.candidate_score, research.baseline_score)
        self.assertEqual(research.n_tasks, len(_tasks()))
        self.assertEqual(rows[1].status, SKIPPED)
        self.assertFalse(rows[1].accepted)


class TestSleepReportCompatibility(unittest.TestCase):
    def test_single_skill_report_has_no_group_rows(self):
        report = SleepReport(night=1, project="/repo/example", accepted=True,
                             gate_action="accept_new_best")
        self.assertEqual(report.skill_groups, [])
        self.assertEqual(report.to_dict()["skill_groups"], [])

    def test_legacy_keyword_construction_still_works(self):
        legacy = {
            "night": 2, "project": "/repo/example", "n_sessions": 1, "n_tasks": 3,
            "baseline_score": 0.1, "candidate_score": 0.2, "accepted": True,
            "gate_action": "accept", "edits": [], "notes": ["ok"],
        }
        report = SleepReport(**legacy)
        self.assertEqual(report.skill_groups, [])
        self.assertEqual(report.gate_action, "accept")

    def test_group_rows_serialize_beside_the_single_skill_summary(self):
        report = SleepReport(night=3, project="/repo/example", accepted=True,
                             gate_action="accept_new_best", candidate_score=0.75)
        report.skill_groups = skill_group_reports({
            "research-skill": GroupConsolidation(
                "research-skill", CONSOLIDATED, n_tasks=6,
                result=_result(True, "accept_new_best", 0.25, 0.75, applied=2),
            ),
        })
        payload = report.to_dict()
        # older consumers read the flat summary and ignore the new key
        self.assertEqual(payload["gate_action"], "accept_new_best")
        self.assertEqual(payload["candidate_score"], 0.75)
        self.assertEqual(payload["skill_groups"][0]["skill_name"], "research-skill")
        self.assertEqual(payload["skill_groups"][0]["n_applied_edits"], 2)

    def test_rows_track_outcomes_not_input_groups(self):
        # outcomes is keyed by skill name, so groups that collapsed onto a
        # shared key upstream are already a single entry and cannot each get a
        # row. Pinned because "one row per group" is the intuitive reading and
        # it is wrong.
        groups = [
            SkillGroup("research-skill", set_learned("", []), _tasks()),
            SkillGroup("research-skill", set_learned("", []), _tasks()),
            SkillGroup("", set_learned("", []), _tasks()),
            SkillGroup("   ", set_learned("", []), _tasks()),
        ]
        outcomes = consolidate_groups(MockBackend(), groups, edit_budget=4, night=1)
        rows = skill_group_reports(outcomes)
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(len(rows), len(outcomes))
        self.assertLess(len(rows), len(groups))
        self.assertEqual([r.skill_name for r in rows], ["research-skill", ""])

    def test_a_blank_named_group_still_reports_its_task_count(self):
        # The row is that group's own evidence. Reporting 0 tasks for a group
        # that had several misstates why it was dropped.
        tasks = _tasks()
        self.assertTrue(tasks, "fixture must supply tasks for this to mean anything")
        outcomes = consolidate_groups(
            MockBackend(),
            [SkillGroup("  ", set_learned("", []), tasks)],
            edit_budget=4, night=1,
        )
        row = skill_group_reports(outcomes)[0]
        self.assertEqual(row.status, SKIPPED)
        self.assertEqual(row.n_tasks, len(tasks))
        self.assertIn("no skill name", row.reason)


if __name__ == "__main__":
    unittest.main()
