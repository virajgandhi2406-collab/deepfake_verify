"""
run_all.py
----------
Executes VERITY evaluation modules (RQ2, RQ3, Runtime, and optional RQ1 if
a labeled dataset manifest is provided) and writes a structured JSON report.

Usage:
  python -m evaluation.run_all
  python -m evaluation.run_all --manifest path/to/manifest.json --split test
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from app.blockchain_client import VerityBlockchainClient
from app.config import BASE_DIR, get_settings
from evaluation.dataset_manifest import save_manifest_template
from evaluation.rq1_detection import evaluate_rq1_from_manifest
from evaluation.rq2_fingerprints import evaluate_rq2
from evaluation.rq3_ledger_attacks import evaluate_rq3
from evaluation.runtime_benchmark import evaluate_runtime


def main() -> None:
    parser = argparse.ArgumentParser(description="Run VERITY experimental evaluations")
    parser.add_argument("--manifest", default="", help="Optional labeled dataset manifest (.json/.csv) for RQ1")
    parser.add_argument("--split", default="test", help="Dataset split for RQ1 (dev or test)")
    parser.add_argument(
        "--output",
        default=str(BASE_DIR / "evaluation" / "reports" / "latest_evaluation.json"),
        help="Output JSON report path",
    )
    args = parser.parse_args()

    settings = get_settings()
    client = VerityBlockchainClient(settings)

    template_path = BASE_DIR / "evaluation" / "manifest_template.json"
    if not template_path.exists():
        save_manifest_template(template_path)

    rq1_report: dict
    if args.manifest:
        rq1_report = evaluate_rq1_from_manifest(args.manifest, split=args.split)
    else:
        rq1_report = {
            "research_question": "RQ1",
            "status": "pending_labeled_corpus",
            "note": (
                "No labeled genuine/manipulated/AI-generated dataset manifest was supplied. "
                "Use evaluation/manifest_template.json to register your evaluation corpus without data leakage."
            ),
        }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rq1": rq1_report,
        "rq2": evaluate_rq2(selected_threshold=settings.phash_threshold),
        "rq3": evaluate_rq3(blockchain_client=client),
        "runtime": evaluate_runtime(iterations=5, blockchain_client=client),
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
