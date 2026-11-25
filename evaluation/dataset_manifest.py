"""
dataset_manifest.py
-------------------
Structured evaluation dataset manifest schema and partition-leakage validator
for VERITY (Part 13 of specification / Section 5.1 of Research Paper).

Ensures:
  - Every sample records sample_id, source, permission_terms, known_label,
    transformation, label_rationale, source_group_id, and dataset_split.
  - Transformed variants sharing a source_group_id remain strictly within a
    single split (dev OR test) to prevent data leakage.
  - Never fabricates real-world benchmark datasets or FaceForensics++ labels.
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

VALID_LABELS = {
    "genuine",
    "manipulated",
    "ai_generated",
    "benign_transformed",
}
VALID_SPLITS = {"dev", "test"}


@dataclass(frozen=True)
class DatasetSample:
    sample_id: str
    file_path: str
    media_type: str
    source: str
    permission_terms: str
    known_label: str
    transformation: str
    label_rationale: str
    source_group_id: str
    dataset_split: str
    notes: str = ""


def load_manifest(manifest_path: str | Path) -> list[DatasetSample]:
    path = Path(manifest_path)
    if path.suffix.lower() == ".json":
        raw_items = json.loads(path.read_text(encoding="utf-8"))
    elif path.suffix.lower() == ".csv":
        with open(path, "r", encoding="utf-8", newline="") as fh:
            raw_items = list(csv.DictReader(fh))
    else:
        raise ValueError("Dataset manifest must be .json or .csv")

    samples: list[DatasetSample] = []
    for item in raw_items:
        sample = DatasetSample(
            sample_id=str(item["sample_id"]).strip(),
            file_path=str(item["file_path"]).strip(),
            media_type=str(item.get("media_type", "image")).strip().lower(),
            source=str(item["source"]).strip(),
            permission_terms=str(item["permission_terms"]).strip(),
            known_label=str(item["known_label"]).strip().lower(),
            transformation=str(item.get("transformation", "none")).strip(),
            label_rationale=str(item["label_rationale"]).strip(),
            source_group_id=str(item["source_group_id"]).strip(),
            dataset_split=str(item["dataset_split"]).strip().lower(),
            notes=str(item.get("notes", "")).strip(),
        )
        samples.append(sample)
    return samples


def validate_manifest(
    samples: list[DatasetSample], *, base_dir: Path | None = None
) -> dict[str, Any]:
    """Validate label taxonomy, missing files, and source-group split leakage."""
    errors: list[str] = []
    seen_ids: set[str] = set()
    group_splits: dict[str, set[str]] = {}
    split_counts = {"dev": 0, "test": 0}
    label_counts: dict[str, int] = {}

    for s in samples:
        if s.sample_id in seen_ids:
            errors.append(f"Duplicate sample_id: {s.sample_id}")
        seen_ids.add(s.sample_id)

        if s.known_label not in VALID_LABELS:
            errors.append(f"Invalid known_label '{s.known_label}' on {s.sample_id}")
        if s.dataset_split not in VALID_SPLITS:
            errors.append(f"Invalid dataset_split '{s.dataset_split}' on {s.sample_id}")
        else:
            split_counts[s.dataset_split] += 1

        label_counts[s.known_label] = label_counts.get(s.known_label, 0) + 1
        group_splits.setdefault(s.source_group_id, set()).add(s.dataset_split)

        resolved = Path(s.file_path)
        if not resolved.is_absolute() and base_dir is not None:
            resolved = base_dir / resolved
        if not resolved.exists():
            errors.append(f"Missing media file for {s.sample_id}: {resolved}")

    leakage_groups = [
        gid for gid, splits in group_splits.items() if len(splits) > 1
    ]
    if leakage_groups:
        errors.append(
            f"Data leakage detected: source_group_id appears across multiple splits: {leakage_groups}"
        )

    return {
        "valid": len(errors) == 0,
        "total_samples": len(samples),
        "split_counts": split_counts,
        "label_counts": label_counts,
        "unique_source_groups": len(group_splits),
        "leakage_groups": leakage_groups,
        "errors": errors,
    }


def save_manifest_template(output_path: str | Path) -> Path:
    """Write a documented empty/template manifest schema for external corpora (e.g. FaceForensics++)."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    template = [
        asdict(
            DatasetSample(
                sample_id="EXAMPLE-001",
                file_path="path/to/genuine_original.jpg",
                media_type="image",
                source="Author-captured camera sample or licensed benchmark (e.g., FaceForensics++ subject to access terms)",
                permission_terms="Academic research evaluation only",
                known_label="genuine",
                transformation="none",
                label_rationale="Unedited original capture from verified source",
                source_group_id="SRC-001",
                dataset_split="dev",
                notes="Replace with actual local dataset entries before running RQ1 evaluation.",
            )
        )
    ]
    path.write_text(json.dumps(template, indent=2), encoding="utf-8")
    return path
