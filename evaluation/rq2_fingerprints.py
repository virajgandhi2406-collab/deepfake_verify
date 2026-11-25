"""
rq2_fingerprints.py
-------------------
RQ2 Experimental Evaluation:
"How do exact and perceptual fingerprints behave under resizing, recompression,
and byte-level modification?"

Tests:
  - Identical file uploads
  - One-byte modification
  - EXIF metadata edit
  - Resizing (50%, 75%)
  - JPEG recompression (quality 85, 60, 35)
  - Visually distinct / unrelated images

Reports exact-hash consistency, Hamming-distance distributions, and empirical
false-match rate across candidate similarity thresholds.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Any

import numpy as np
from PIL import Image

from app.hasher import hamming_distance, perceptual_hash, sha256_of_file


def _create_structured_image(path: Path, seed: int, size: tuple[int, int] = (256, 256)) -> None:
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0 : size[1], 0 : size[0]].astype(np.float32)
    base = (
        128.0
        + 55.0 * np.sin(x / (12.0 + (seed % 7))) * np.cos(y / (14.0 + (seed % 5)))
        + 25.0 * np.sin((x + y) / (9.0 + (seed % 11)))
    )
    noise = rng.normal(0, 6.0, size=(size[1], size[0]))
    r = np.clip(base + noise + (seed * 17) % 40, 0, 255).astype(np.uint8)
    g = np.clip(base * 0.9 + noise + (seed * 29) % 40, 0, 255).astype(np.uint8)
    b = np.clip(base * 0.8 + noise + (seed * 43) % 40, 0, 255).astype(np.uint8)
    rgb = np.stack([r, g, b], axis=-1)
    Image.fromarray(rgb, mode="RGB").save(path, format="JPEG", quality=95)


def _summarize_distances(dists: list[int]) -> dict[str, float | int]:
    arr = np.asarray(dists, dtype=np.float32)
    return {
        "count": len(dists),
        "min": int(np.min(arr)),
        "median": float(np.median(arr)),
        "mean": round(float(np.mean(arr)), 3),
        "p95": round(float(np.percentile(arr, 95)), 3),
        "max": int(np.max(arr)),
    }


def evaluate_rq2(
    image_paths: list[Path] | None = None,
    candidate_thresholds: tuple[int, ...] = (6, 8, 10, 12, 16),
    selected_threshold: int = 10,
) -> dict[str, Any]:
    """Execute RQ2 exact-hash and DCT perceptual-hash perturbation experiments."""
    with tempfile.TemporaryDirectory(prefix="verity_rq2_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        sources: list[Path] = []
        if image_paths:
            sources = [Path(p) for p in image_paths]
        else:
            for i in range(6):
                p = tmp_dir / f"base_{i}.jpg"
                _create_structured_image(p, seed=100 + i * 37)
                sources.append(p)

        identical_sha_matches = 0
        one_byte_sha_changes = 0
        metadata_edit_sha_changes = 0
        metadata_edit_dists: list[int] = []
        resize_50_dists: list[int] = []
        resize_75_dists: list[int] = []
        jpeg_85_dists: list[int] = []
        jpeg_60_dists: list[int] = []
        jpeg_35_dists: list[int] = []
        unrelated_dists: list[int] = []

        base_phashes: list[str] = []

        for idx, src in enumerate(sources):
            orig_bytes = src.read_bytes()
            sha_orig = sha256_of_file(src)
            ph_orig = perceptual_hash(src)
            base_phashes.append(ph_orig)

            # 1. Identical copy
            copy_path = tmp_dir / f"copy_{idx}.jpg"
            copy_path.write_bytes(orig_bytes)
            if sha256_of_file(copy_path) == sha_orig:
                identical_sha_matches += 1

            # 2. One-byte change at tail
            mutated_path = tmp_dir / f"onebyte_{idx}.jpg"
            mutated_bytes = bytearray(orig_bytes)
            mutated_bytes[-1] ^= 0x01
            mutated_path.write_bytes(bytes(mutated_bytes))
            if sha256_of_file(mutated_path) != sha_orig:
                one_byte_sha_changes += 1

            # 3. Metadata edit (adds EXIF Software tag without altering pixels)
            meta_path = tmp_dir / f"meta_{idx}.jpg"
            with Image.open(src) as img:
                exif = img.getexif()
                exif[0x0131] = "VERITY-RQ2-Metadata-Edit"
                img.save(meta_path, format="JPEG", quality=95, exif=exif)
                if sha256_of_file(meta_path) != sha_orig:
                    metadata_edit_sha_changes += 1
                metadata_edit_dists.append(hamming_distance(ph_orig, perceptual_hash(meta_path)))

                # 4. Resizing
                w, h = img.size
                r50 = tmp_dir / f"r50_{idx}.jpg"
                img.resize((max(32, w // 2), max(32, h // 2)), Image.LANCZOS).save(
                    r50, format="JPEG", quality=95
                )
                resize_50_dists.append(hamming_distance(ph_orig, perceptual_hash(r50)))

                r75 = tmp_dir / f"r75_{idx}.jpg"
                img.resize((max(32, int(w * 0.75)), max(32, int(h * 0.75))), Image.LANCZOS).save(
                    r75, format="JPEG", quality=95
                )
                resize_75_dists.append(hamming_distance(ph_orig, perceptual_hash(r75)))

                # 5. Recompression
                for q, bucket in ((85, jpeg_85_dists), (60, jpeg_60_dists), (35, jpeg_35_dists)):
                    qp = tmp_dir / f"q{q}_{idx}.jpg"
                    img.save(qp, format="JPEG", quality=q)
                    bucket.append(hamming_distance(ph_orig, perceptual_hash(qp)))

        # 6. Pairwise unrelated images
        for i in range(len(base_phashes)):
            for j in range(i + 1, len(base_phashes)):
                unrelated_dists.append(hamming_distance(base_phashes[i], base_phashes[j]))

        benign_dists = (
            metadata_edit_dists
            + resize_50_dists
            + resize_75_dists
            + jpeg_85_dists
            + jpeg_60_dists
            + jpeg_35_dists
        )
        threshold_sweep = {}
        for th in candidate_thresholds:
            true_match_rate = round(
                sum(1 for d in benign_dists if d <= th) / len(benign_dists), 4
            )
            false_match_rate = round(
                sum(1 for d in unrelated_dists if d <= th) / max(1, len(unrelated_dists)),
                4,
            )
            threshold_sweep[str(th)] = {
                "benign_true_match_rate": true_match_rate,
                "unrelated_false_match_rate": false_match_rate,
            }

        return {
            "research_question": "RQ2",
            "sample_count": len(sources),
            "source_mode": "provided_images" if image_paths else "controlled_synthetic_textures",
            "exact_sha256_consistency": {
                "identical_upload_match_rate": round(identical_sha_matches / len(sources), 4),
                "one_byte_change_detection_rate": round(one_byte_sha_changes / len(sources), 4),
                "metadata_edit_hash_change_rate": round(metadata_edit_sha_changes / len(sources), 4),
            },
            "perceptual_hamming_distributions": {
                "metadata_edit": _summarize_distances(metadata_edit_dists),
                "resize_50pct": _summarize_distances(resize_50_dists),
                "resize_75pct": _summarize_distances(resize_75_dists),
                "jpeg_q85": _summarize_distances(jpeg_85_dists),
                "jpeg_q60": _summarize_distances(jpeg_60_dists),
                "jpeg_q35": _summarize_distances(jpeg_35_dists),
                "unrelated_pairs": _summarize_distances(unrelated_dists) if unrelated_dists else {},
            },
            "selected_similarity_threshold": selected_threshold,
            "threshold_sweep": threshold_sweep,
            "false_match_rate_at_selected_threshold": threshold_sweep[str(selected_threshold)][
                "unrelated_false_match_rate"
            ],
        }
