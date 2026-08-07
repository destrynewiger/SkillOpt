from __future__ import annotations

from skillopt_sleep.backend import Backend
from skillopt_sleep.consolidate import _split, consolidate
from skillopt_sleep.types import TaskRecord


def _task(tid: str, split: str, rubric: str = "a good answer") -> TaskRecord:
    return TaskRecord(
        id=tid,
        project="/p",
        intent=f"intent {tid}",
        reference_kind="rubric",
        reference=rubric,
        split=split,
    )


# --- holdout leak detection --------------------------------------------------


def test_split_flags_leak_when_only_one_task() -> None:
    train, val, leaked = _split([_task("a", "train")])
    assert train and val
    assert leaked is True


def test_split_flags_leak_when_val_must_borrow_train() -> None:
    _train, _val, leaked = _split([_task("a", "train"), _task("b", "train")])
    assert leaked is True


def test_split_flags_leak_when_train_must_borrow_val() -> None:
    _train, _val, leaked = _split([_task("a", "val")])
    assert leaked is True


def test_split_flags_overlap_by_id() -> None:
    shared = _task("a", "train")
    dup = _task("a", "val")
    _train, _val, leaked = _split([shared, dup])
    assert leaked is True


def test_split_is_clean_when_train_and_val_are_disjoint() -> None:
    train, val, leaked = _split([_task("a", "train"), _task("b", "val")])
    assert [t.id for t in train] == ["a"]
    assert [t.id for t in val] == ["b"]
    assert leaked is False


def test_lone_test_task_is_never_used_as_train_or_val() -> None:
    train, val, _leaked = _split([_task("t", "test")])
    assert train == [] and val == []


# --- end-to-end gate behaviour ----------------------------------------------


class _ScriptedBackend(Backend):
    """Backend that always proposes the one edit which helps.

    Lets the gate be exercised without a live model: the candidate skill is the
    one carrying MARKER, and the patched replay scores only that skill well.
    """

    name = "scripted"
    MARKER = "ALWAYS REPORT WHAT WAS SEARCHED"

    def __init__(self) -> None:
        super().__init__()
        self.last_reflect_raw = ""
        self.last_call_error = ""

    def _call(self, prompt, *, max_tokens=1024):
        return "response"

    def reflect(self, failures, successes, skill, memory, *, edit_budget, evolve_skill, evolve_memory):
        from skillopt_sleep.types import EditRecord

        return [
            EditRecord(
                target="skill",
                op="add",
                content=self.MARKER,
                anchor="",
                rationale="report what was searched",
            )
        ]


def test_gate_abstains_when_holdout_leaked(monkeypatch) -> None:
    # One task means val == train, so an "improvement" cannot be validated.
    from skillopt_sleep import consolidate as cons

    def fake_replay_batch(backend, tasks, skill, memory, **kw):
        from skillopt_sleep.types import ReplayResult

        hard = 1.0 if _ScriptedBackend.MARKER in skill else 0.0
        return [(t, ReplayResult(id=t.id, response="r", hard=hard, soft=hard)) for t in tasks]

    monkeypatch.setattr(cons, "replay_batch", fake_replay_batch)
    result = consolidate(
        _ScriptedBackend(),
        [_task("only", "train")],
        skill="base skill",
        memory="",
        night=1,
    )
    assert result.holdout_leaked is True
    assert result.accepted is False
    assert result.gate_action == "reject_unverified"


def test_gate_still_accepts_a_genuine_improvement(monkeypatch) -> None:
    # The counterpart to the abstain test: a gate that rejects everything would
    # be trivially safe and useless. With a disjoint val slice, a candidate that
    # genuinely scores better on tasks the optimizer did NOT see must be
    # accepted.
    from skillopt_sleep import consolidate as cons

    def fake_replay_batch(backend, tasks, skill, memory, **kw):
        from skillopt_sleep.types import ReplayResult

        hard = 1.0 if _ScriptedBackend.MARKER in skill else 0.0
        return [(t, ReplayResult(id=t.id, response="r", hard=hard, soft=hard)) for t in tasks]

    monkeypatch.setattr(cons, "replay_batch", fake_replay_batch)
    result = consolidate(
        _ScriptedBackend(),
        [_task("tr", "train"), _task("va", "val")],
        skill="base skill",
        memory="",
        night=1,
    )
    assert result.holdout_leaked is False
    assert result.accepted is True
    assert result.gate_action in {"accept", "accept_new_best"}
    assert result.candidate_score > result.baseline_score
    assert _ScriptedBackend.MARKER in result.new_skill


# --- report banner -----------------------------------------------------------


def test_not_validated_banner_shown_only_when_gate_is_on() -> None:
    from skillopt_sleep.config import SleepConfig
    from skillopt_sleep.cycle import _render_report_md
    from skillopt_sleep.types import SleepReport

    report = SleepReport(night=1, project="/p", holdout_leaked=True)

    on = _render_report_md(report, SleepConfig())
    assert "Not validated" in on

    off_cfg = SleepConfig()
    off_cfg.data["gate_mode"] = "off"
    off = _render_report_md(report, off_cfg)
    # In greedy mode the gate does no scoring, so the "gate scored the same
    # tasks" banner would be misleading and must be suppressed.
    assert "Not validated" not in off


def test_leaked_edits_are_labeled_unverified_not_rejected() -> None:
    from skillopt_sleep.config import SleepConfig
    from skillopt_sleep.cycle import _render_report_md
    from skillopt_sleep.types import EditRecord, SleepReport

    edit = EditRecord(target="skill", op="add", content="a suggestion")

    leaked = SleepReport(
        night=1, project="/p", holdout_leaked=True, rejected_edits=[edit],
    )
    md = _render_report_md(leaked, SleepConfig())
    # On a leaked-holdout night the gate abstained; the surfaced edits are
    # unverified suggestions, not rejections/negative feedback.
    assert "Unverified suggestions" in md
    assert "negative feedback" not in md

    genuine = SleepReport(
        night=1, project="/p", holdout_leaked=False, rejected_edits=[edit],
    )
    md2 = _render_report_md(genuine, SleepConfig())
    assert "negative feedback" in md2

