"""Edits that change nothing must stay visible.

An edit whose anchor is absent is applied to nothing, but it is also not
gate-rejected — so before `apply_edits_detailed` it appeared in neither list and
the night reported fewer edits than the optimizer actually produced.
"""
import unittest

from skillopt_sleep.memory import apply_edits, apply_edits_detailed, set_learned
from skillopt_sleep.types import EditRecord


def _doc(*lines):
    return set_learned("# Skill\n\nhand-written body\n", list(lines))


class TestUnmatchedEdits(unittest.TestCase):
    def test_replace_with_absent_anchor_is_unmatched(self) -> None:
        doc = _doc("existing rule")
        e = EditRecord(target="skill", op="replace", content="new", anchor="NOT THERE")
        new_doc, applied, unmatched = apply_edits_detailed(doc, [e])
        self.assertEqual(applied, [])
        self.assertEqual(unmatched, [e])
        self.assertEqual(new_doc, doc)

    def test_delete_with_absent_anchor_is_unmatched(self) -> None:
        doc = _doc("existing rule")
        e = EditRecord(target="skill", op="delete", content="", anchor="NOT THERE")
        _new, applied, unmatched = apply_edits_detailed(doc, [e])
        self.assertEqual((applied, unmatched), ([], [e]))

    def test_delete_with_empty_anchor_is_unmatched_and_preserves_all_lines(self) -> None:
        doc = _doc("keep one", "keep two")
        e = EditRecord(target="skill", op="delete", content="", anchor="")
        new_doc, applied, unmatched = apply_edits_detailed(doc, [e])
        self.assertEqual((applied, unmatched), ([], [e]))
        self.assertEqual(new_doc, doc)

    def test_replace_with_identical_content_is_unmatched(self) -> None:
        doc = _doc("keep this exact rule")
        e = EditRecord(
            target="skill",
            op="replace",
            content="keep this exact rule",
            anchor="exact rule",
        )
        new_doc, applied, unmatched = apply_edits_detailed(doc, [e])
        self.assertEqual((applied, unmatched), ([], [e]))
        self.assertEqual(new_doc, doc)

    def test_duplicate_add_is_unmatched_not_applied(self) -> None:
        doc = _doc("existing rule")
        e = EditRecord(target="skill", op="add", content="Existing   Rule")
        _new, applied, unmatched = apply_edits_detailed(doc, [e])
        self.assertEqual(applied, [])
        self.assertEqual(unmatched, [e])

    def test_empty_add_is_unmatched(self) -> None:
        doc = _doc("existing rule")
        e = EditRecord(target="skill", op="add", content="   ")
        _new, applied, unmatched = apply_edits_detailed(doc, [e])
        self.assertEqual((applied, unmatched), ([], [e]))

    def test_mixed_batch_splits_correctly(self) -> None:
        doc = _doc("keep me")
        good = EditRecord(target="skill", op="add", content="brand new rule")
        bad = EditRecord(target="skill", op="replace", content="x", anchor="ghost")
        new_doc, applied, unmatched = apply_edits_detailed(doc, [good, bad])
        self.assertEqual(applied, [good])
        self.assertEqual(unmatched, [bad])
        self.assertIn("brand new rule", new_doc)

    def test_apply_edits_keeps_its_two_tuple_contract(self) -> None:
        doc = _doc("keep me")
        e = EditRecord(target="skill", op="add", content="another rule")
        result = apply_edits(doc, [e])
        self.assertEqual(len(result), 2)
        new_doc, applied = result
        self.assertEqual(applied, [e])
        self.assertIn("another rule", new_doc)

    def test_unknown_op_is_unmatched(self) -> None:
        doc = _doc("existing rule")
        e = EditRecord(target="skill", op="rewrite-everything", content="x")
        new_doc, applied, unmatched = apply_edits_detailed(doc, [e])
        self.assertEqual((applied, unmatched), ([], [e]))
        self.assertEqual(new_doc, doc)

    def test_hand_written_body_is_never_touched(self) -> None:
        doc = _doc("existing rule")
        e = EditRecord(target="skill", op="replace", content="x", anchor="hand-written body")
        new_doc, applied, unmatched = apply_edits_detailed(doc, [e])
        self.assertIn("hand-written body", new_doc)
        self.assertEqual(applied, [])
        self.assertEqual(unmatched, [e])


if __name__ == "__main__":
    unittest.main()
