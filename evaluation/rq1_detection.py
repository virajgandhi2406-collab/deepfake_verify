"""
rq1_detection.py
----------------
RQ1 Experimental Evaluation:
"How reliably can the heuristic baseline distinguish manipulated samples from
genuine media and benign transformations?"

Positive class (explicitly defined): manipulated OR ai_generated.
Negative class: genuine OR benign_transformed (resized, recompressed, textured genuine).

Reports:
  - Confusion matrix (TP, FP, TN, FN)
  - Precision, Recall, F1 score, False-Positive Rate (FPR), Balanced Accuracy
  - Coverage and Inconclusive count (handled separately)
  - Per-category outcome breakdown
  - Baseline comparison between individual indicators and combined heuristic score
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.detector import (
    VERDICT_ELEVATED,
    VERDICT_INCONCLUSIVE,
    VERDICT_LOW,
    VERDICT_REVIEW,
    get_active_detector,
)
from evaluation.dataset_manifest import DatasetSample, load_manifest, validate_manifest

POSITIVE_LABELS = {"manipulated", "ai_generated"}
NEGATIVE_LABELS = {"genuine", "benign_transformed"}


def _safe_div(num: float, den: float) -> float | None:
    return round(num / den, 4) if den > 0 else None


def evaluate_rq1(
    samples: list[DatasetSample],
    *,
    base_dir: Path | None = None,
    split: str = "test",
    positive_threshold: float = 0.5,
) -> dict[str, Any]:
    """Run RQ1 heuristic evaluation on a validated dataset split."""
    validation = validate_manifest(samples, base_dir=base_dir)
    if not validation["valid"]:
        raise ValueError(f"Dataset manifest failed validation: {validation['errors']}")

    target_samples = [s for s in samples if s.dataset_split == split]
    detector = get_active_detector()

    tp = fp = tn = fn = 0
    inconclusive_count = 0
    per_category: dict[str, dict[str, int]] = {}
    records: list[dict[str, Any]] = []

    for sample in target_samples:
        path = Path(sample.file_path)
        if not path.is_absolute() and base_dir is not None:
            path = base_dir / path

        cat_stats = per_category.setdefault(
            sample.known_label,
            {
                "total": 0,
                "low_evidence": 0,
                "review_required": 0,
                "elevated_evidence": 0,
                "inconclusive": 0,
            },
        )
        cat_stats["total"] += 1

        try:
            res = (
                detector.analyze_video(path, path.name)
                if sample.media_type == "video"
                else detector.analyze_image(path, path.name)
            )
            score = res.get("heuristic_score")
            verdict = res.get("verdict", VERDICT_INCONCLUSIVE)
        except Exception:
            score = None
            verdict = VERDICT_INCONCLUSIVE
            res = {}

        if verdict == VERDICT_INCONCLUSIVE or score is None:
            inconclusive_count += 1
            cat_stats["inconclusive"] += 1
            records.append(
                {
                    "sample_id": sample.sample_id,
                    "known_label": sample.known_label,
                    "verdict": VERDICT_INCONCLUSIVE,
                    "heuristic_score": None,
                }
            )
            continue

        if verdict == VERDICT_LOW:
            cat_stats["low_evidence"] += 1
        elif verdict == VERDICT_REVIEW:
            cat_stats["review_required"] += 1
        elif verdict == VERDICT_ELEVATED:
            cat_stats["elevated_evidence"] += 1

        gt_positive = sample.known_label in POSITIVE_LABELS
        pred_positive = float(score) >= positive_threshold

        if gt_positive and pred_positive:
            tp += 1
        elif not gt_positive and pred_positive:
            fp += 1
        elif not gt_positive and not pred_positive:
            tn += 1
        else:
            fn += 1

        subscores = res.get("indicators", {}).get("subscores", {})
        records.append(
            {
                "sample_id": sample.sample_id,
                "known_label": sample.known_label,
                "transformation": sample.transformation,
                "verdict": verdict,
                "heuristic_score": score,
                "subscores": subscores,
                "gt_positive": gt_positive,
                "pred_positive": pred_positive,
            }
        )

    evaluated_total = tp + fp + tn + fn
    total_samples = len(target_samples)
    coverage = _safe_div(evaluated_total, total_samples)
    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    specificity = _safe_div(tn, tn + fp)
    fpr = _safe_div(fp, fp + tn)
    f1 = (
        round((2 * precision * recall) / (precision + recall), 4)
        if (precision is not None and recall is not None and (precision + recall) > 0)
        else None
    )
    balanced_accuracy = (
        round((recall + specificity) / 2.0, 4)
        if (recall is not None and specificity is not None)
        else None
    )

    return {
        "research_question": "RQ1",
        "split": split,
        "positive_class_definition": sorted(POSITIVE_LABELS),
        "negative_class_definition": sorted(NEGATIVE_LABELS),
        "decision_threshold": positive_threshold,
        "detector_version": detector.detector_version,
        "config_id": detector.config_id,
        "total_samples": total_samples,
        "evaluated_samples": evaluated_total,
        "inconclusive_count": inconclusive_count,
        "coverage": coverage,
        "confusion_matrix": {"TP": tp, "FP": fp, "TN": tn, "FN": fn},
        "metrics": {
            "precision": precision,
            "recall": recall,
            "f1_score": f1,
            "false_positive_rate": fpr,
            "balanced_accuracy": balanced_accuracy,
        },
        "per_category_breakdown": per_category,
        "sample_results": records,
    }


def evaluate_rq1_from_manifest(
    manifest_path: str | Path, *, split: str = "test", positive_threshold: float = 0.5
) -> dict[str, Any]:
    path = Path(manifest_path)
    samples = load_manifest(path)
    return evaluate_rq1(
        samples, base_dir=path.parent, split=split, positive_threshold=positive_threshold
    )
