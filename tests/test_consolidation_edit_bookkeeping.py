"""Final gate rollback must agree with edit bookkeeping and reports."""

from __future__ import annotations

import importlib

from skillopt_sleep.backend import Backend
from skillopt_sleep.memory import set_learned
from skillopt_sleep.types import EditRecord, ReplayResult, TaskRecord


def test_final_validation_rollback_reclassifies_tentative_edits(
    monkeypatch,
) -> None:
    consolidate_module = importlib.import_module("skillopt_sleep.consolidate")
    edit = EditRecord(
        target="skill",
        op="add",
        content="A tentatively useful rule.",
        rationale="trial improved",
    )

    class EditingBackend(Backend):
        name = "editing-stub"

        def reflect(self, *args, **kwargs):
            return [edit]

    task = TaskRecord(
        id="validation-task",
        project="test",
        intent="test final rollback",
        split="val",
        reference_kind="exact",
        reference="ok",
    )
    scores = iter((0.0, 0.0, 1.0, 0.0))

    def fake_replay_batch(backend, tasks, skill, memory):
        score = next(scores)
        return [
            (
                item,
                ReplayResult(
                    id=item.id,
                    hard=score,
                    soft=score,
                    response="ok" if score else "miss",
                ),
            )
            for item in tasks
        ]

    monkeypatch.setattr(
        consolidate_module, "replay_batch", fake_replay_batch
    )
    original = set_learned("# Skill\n", [])

    result = consolidate_module.consolidate(
        EditingBackend(),
        [task],
        original,
        "",
        gate_metric="hard",
        evolve_memory=False,
    )

    assert result.accepted is False
    assert result.new_skill == original
    assert result.applied_edits == []
    assert result.rejected_edits == [edit]


def test_final_rollback_does_not_duplicate_an_already_rejected_edit(
    monkeypatch,
) -> None:
    consolidate_module = importlib.import_module("skillopt_sleep.consolidate")
    edit = EditRecord(
        target="skill",
        op="add",
        content="A repeated proposal.",
        rationale="same proposal for both targets",
    )

    class RepeatingBackend(Backend):
        name = "repeating-stub"

        def reflect(self, *args, **kwargs):
            return [edit]

    task = TaskRecord(
        id="dedup-task",
        project="test",
        intent="test rollback deduplication",
        split="val",
        reference_kind="exact",
        reference="ok",
    )
    # baseline, train, rejected skill trial, post-skill train, accepted memory
    # trial, then regressing final replay.
    scores = iter((0.0, 0.0, 0.0, 0.0, 1.0, 0.0))

    def fake_replay_batch(backend, tasks, skill, memory):
        score = next(scores)
        return [
            (
                item,
                ReplayResult(
                    id=item.id,
                    hard=score,
                    soft=score,
                    response="ok" if score else "miss",
                ),
            )
            for item in tasks
        ]

    monkeypatch.setattr(consolidate_module, "replay_batch", fake_replay_batch)
    original = set_learned("# Skill\n", [])

    result = consolidate_module.consolidate(
        RepeatingBackend(),
        [task],
        original,
        original,
        gate_metric="hard",
        evolve_skill=True,
        evolve_memory=True,
    )

    assert result.accepted is False
    assert result.applied_edits == []
    assert result.rejected_edits == [edit]
