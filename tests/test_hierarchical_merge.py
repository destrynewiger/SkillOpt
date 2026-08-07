"""Tests for skillopt.gradient.aggregate._hierarchical_merge.

Issue #194: merge_batch_size=1 used to infinite-loop because every batch
had length 1 and was passed through unchanged, so `current` never shrank.
"""
from __future__ import annotations

from unittest.mock import patch

from skillopt.gradient.aggregate import _hierarchical_merge


def _stub_merge_batch(skill_content, patches, system_prompt, update_mode,
                      meta_skill_context="", level=1):
    """Deterministic merge: concatenate edits and tag with merge level."""
    all_edits = []
    for p in patches:
        for e in p.get("edits", []):
            all_edits.append({**e, "merge_level": level})
    return {
        "reasoning": f"merged {len(patches)} patches at level {level}",
        "edits": all_edits,
    }


class TestHierarchicalMergeBatchSizeGuard:
    """_hierarchical_merge must terminate for batch_size < 2 (issue #194)."""

    def test_batch_size_one_terminates_and_merges(self) -> None:
        patches = [
            {"reasoning": "a", "edits": [{"id": "1", "content": "one"}]},
            {"reasoning": "b", "edits": [{"id": "2", "content": "two"}]},
            {"reasoning": "c", "edits": [{"id": "3", "content": "three"}]},
        ]
        with patch(
            "skillopt.gradient.aggregate._merge_batch",
            side_effect=_stub_merge_batch,
        ):
            result = _hierarchical_merge(
                skill_content="skill body",
                patches=patches,
                system_prompt="merge prompt",
                update_mode="patch",
                batch_size=1,
                verbose=False,
                label="test",
                workers=1,
            )

        assert isinstance(result, dict)
        assert "edits" in result
        ids = {e["id"] for e in result["edits"]}
        assert ids == {"1", "2", "3"}

    def test_batch_size_zero_terminates(self) -> None:
        patches = [
            {"reasoning": "a", "edits": [{"id": "1"}]},
            {"reasoning": "b", "edits": [{"id": "2"}]},
        ]
        with patch(
            "skillopt.gradient.aggregate._merge_batch",
            side_effect=_stub_merge_batch,
        ):
            result = _hierarchical_merge(
                skill_content="skill",
                patches=patches,
                system_prompt="sys",
                update_mode="patch",
                batch_size=0,
                verbose=False,
                workers=1,
            )
        assert {e["id"] for e in result["edits"]} == {"1", "2"}

    def test_normal_batch_size_still_merges(self) -> None:
        patches = [
            {"reasoning": "a", "edits": [{"id": "1"}]},
            {"reasoning": "b", "edits": [{"id": "2"}]},
            {"reasoning": "c", "edits": [{"id": "3"}]},
            {"reasoning": "d", "edits": [{"id": "4"}]},
        ]
        with patch(
            "skillopt.gradient.aggregate._merge_batch",
            side_effect=_stub_merge_batch,
        ):
            result = _hierarchical_merge(
                skill_content="skill",
                patches=patches,
                system_prompt="sys",
                update_mode="patch",
                batch_size=2,
                verbose=False,
                workers=2,
            )
        assert {e["id"] for e in result["edits"]} == {"1", "2", "3", "4"}
