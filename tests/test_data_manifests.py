import json
from pathlib import Path

from skillopt.datasets.base import SPLIT_NAMES

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def test_split_manifest_counts_match_item_files() -> None:
    manifest_paths = sorted(DATA_DIR.glob("*/split_manifest.json"))
    assert manifest_paths, f"No split manifests found under {DATA_DIR}"

    for manifest_path in manifest_paths:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        for split_name in SPLIT_NAMES:
            declared_count = manifest["counts"][split_name]
            items_path = manifest_path.parent / split_name / "items.json"
            actual_count = len(json.loads(items_path.read_text(encoding="utf-8")))

            assert declared_count == actual_count, (
                f"{manifest_path} split {split_name!r}: declared count {declared_count}, actual count {actual_count}"
            )
