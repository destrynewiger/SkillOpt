"""Regression tests for consolidate._split hold-out contract.

test-split tasks must never enter train or val. An all-test batch must not
silently fall back to using the held-out set for consolidation.
"""
from __future__ import annotations

import os
import tempfile
import unittest

from skillopt_sleep.consolidate import _split
from skillopt_sleep.tasks_file import load_tasks_file, write_tasks_file
from skillopt_sleep.types import TaskRecord


def _ids(tasks):
    return sorted(t.id for t in tasks)


class TestConsolidateSplit(unittest.TestCase):
    def test_all_test_batch_does_not_leak_into_train_or_val(self):
        tasks = [
            TaskRecord(id="t0", project="p", intent="do X", split="test"),
            TaskRecord(id="t1", project="p", intent="do Y", split="test"),
            TaskRecord(id="t2", project="p", intent="do Z", split="test"),
        ]
        train, val, _leaked = _split(tasks)
        self.assertEqual(train, [])
        self.assertEqual(val, [])

    def test_all_test_via_tasks_file_path_does_not_leak(self):
        tasks = [
            TaskRecord(id="t0", project="p", intent="do X", split="test"),
            TaskRecord(id="t1", project="p", intent="do Y", split="test"),
            TaskRecord(id="t2", project="p", intent="do Z", split="test"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = write_tasks_file(
                os.path.join(tmp, "tasks.json"),
                {"tasks": [t.to_dict() for t in tasks]},
            )
            loaded, _ = load_tasks_file(path)
            train, val, _leaked = _split(loaded)
        self.assertEqual(_ids(train), [])
        self.assertEqual(_ids(val), [])
        self.assertEqual({t.split for t in loaded}, {"test"})

    def test_train_only_falls_back_val_to_train(self):
        tasks = [
            TaskRecord(id="a", project="p", intent="A", split="train"),
            TaskRecord(id="b", project="p", intent="B", split="train"),
        ]
        train, val, _leaked = _split(tasks)
        self.assertEqual(_ids(train), ["a", "b"])
        self.assertEqual(_ids(val), ["a", "b"])

    def test_train_plus_test_without_val_gates_on_train_not_test(self):
        tasks = [
            TaskRecord(id="tr", project="p", intent="train", split="train"),
            TaskRecord(id="te", project="p", intent="test", split="test"),
        ]
        train, val, _leaked = _split(tasks)
        self.assertEqual(_ids(train), ["tr"])
        self.assertEqual(_ids(val), ["tr"])
        self.assertNotIn("te", _ids(train) + _ids(val))

    def test_explicit_train_val_test_keeps_partitions(self):
        tasks = [
            TaskRecord(id="tr", project="p", intent="train", split="train"),
            TaskRecord(id="va", project="p", intent="val", split="val"),
            TaskRecord(id="te", project="p", intent="test", split="test"),
        ]
        train, val, _leaked = _split(tasks)
        self.assertEqual(_ids(train), ["tr"])
        self.assertEqual(_ids(val), ["va"])

    def test_legacy_holdout_name_maps_to_val(self):
        tasks = [
            TaskRecord(id="tr", project="p", intent="train", split="replay"),
            TaskRecord(id="va", project="p", intent="val", split="holdout"),
            TaskRecord(id="te", project="p", intent="test", split="test"),
        ]
        train, val, _leaked = _split(tasks)
        self.assertEqual(_ids(train), ["tr"])
        self.assertEqual(_ids(val), ["va"])


if __name__ == "__main__":
    unittest.main()
