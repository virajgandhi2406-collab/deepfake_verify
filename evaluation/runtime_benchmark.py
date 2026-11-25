"""
runtime_benchmark.py
--------------------
Measures VERITY pipeline runtime across hashing, decoding, forensic analysis,
local canonical ledger write, and real EVM blockchain transaction submission.
Reports median and p95 latencies along with hardware & software versions.
"""

from __future__ import annotations

import os
from pathlib import Path
import platform
import tempfile
import time
from typing import Any

import cv2
import numpy as np
from PIL import Image
import web3

from app.blockchain_client import VerityBlockchainClient
from app.detector import get_active_detector
from app.hasher import perceptual_hash, sha256_of_file
from app.ledger import add_record, record_hash_of


def _stats_ms(values: list[float]) -> dict[str, float]:
    arr = np.asarray(values, dtype=np.float64)
    return {
        "count": len(values),
        "median_ms": round(float(np.median(arr)), 2),
        "p95_ms": round(float(np.percentile(arr, 95)), 2),
        "min_ms": round(float(np.min(arr)), 2),
        "max_ms": round(float(np.max(arr)), 2),
    }


def evaluate_runtime(
    *,
    iterations: int = 5,
    blockchain_client: VerityBlockchainClient | None = None,
) -> dict[str, Any]:
    """Benchmark image & video verification stages and record environment versions."""
    detector = get_active_detector()
    hashing_times: list[float] = []
    decode_times: list[float] = []
    analysis_times: list[float] = []
    ledger_times: list[float] = []
    blockchain_times: list[float] = []
    total_times: list[float] = []

    with tempfile.TemporaryDirectory(prefix="verity_rt_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        ledger_path = tmp_dir / "bench_ledger.json"

        bc_connected = False
        if blockchain_client is not None:
            st = blockchain_client.get_status()
            bc_connected = bool(st.get("connected") and st.get("contract_deployed"))

        for i in range(iterations):
            img_path = tmp_dir / f"bench_{i}.jpg"
            rng = np.random.default_rng(500 + i)
            arr = rng.integers(0, 255, size=(512, 512, 3), dtype=np.uint8)
            Image.fromarray(arr, mode="RGB").save(img_path, format="JPEG", quality=90)

            t0 = time.perf_counter()
            t_h0 = time.perf_counter()
            sha = sha256_of_file(img_path)
            ph = perceptual_hash(img_path)
            h_ms = (time.perf_counter() - t_h0) * 1000.0

            res = detector.analyze_image(img_path, img_path.name)
            d_ms = float(res.get("decode_time_ms", 0.0))
            a_ms = float(res.get("analysis_time_ms", 0.0))

            t_l0 = time.perf_counter()
            rec = add_record(
                sha,
                ph,
                res,
                img_path.name,
                file_size_bytes=img_path.stat().st_size,
                media_type="image",
                ledger_path=ledger_path,
            )
            l_ms = (time.perf_counter() - t_l0) * 1000.0

            if bc_connected and blockchain_client is not None:
                t_b0 = time.perf_counter()
                blockchain_client.register_verification(
                    content_sha256_hex=sha,
                    perceptual_fingerprint=ph,
                    verification_id=rec["verification_id"],
                    media_type="image",
                    result_category=rec["verdict"],
                    analysis_config_id=rec["analysis_config_id"],
                    record_hash_hex=record_hash_of(rec),
                    local_sequence=rec["sequence_number"],
                )
                b_ms = (time.perf_counter() - t_b0) * 1000.0
                blockchain_times.append(b_ms)

            tot_ms = (time.perf_counter() - t0) * 1000.0
            hashing_times.append(h_ms)
            decode_times.append(d_ms)
            analysis_times.append(a_ms)
            ledger_times.append(l_ms)
            total_times.append(tot_ms)

        # Also benchmark 1 synthetic 15-frame video (480x360)
        vid_path = tmp_dir / "bench_video.mp4"
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(vid_path), fourcc, 15.0, (480, 360))
        for f_idx in range(15):
            frame = np.full((360, 480, 3), fill_value=(f_idx * 12) % 255, dtype=np.uint8)
            cv2.circle(frame, (120 + f_idx * 10, 180), 40, (200, 140, 80), -1)
            writer.write(frame)
        writer.release()

        tv0 = time.perf_counter()
        v_sha = sha256_of_file(vid_path)
        v_res = detector.analyze_video(vid_path, vid_path.name)
        v_total_ms = round((time.perf_counter() - tv0) * 1000.0, 2)

    return {
        "benchmark": "VERITY Runtime Evaluation",
        "image_iterations": iterations,
        "image_dimensions": "512x512 JPEG",
        "blockchain_included": bc_connected,
        "image_timings": {
            "hashing": _stats_ms(hashing_times),
            "decode": _stats_ms(decode_times),
            "analysis": _stats_ms(analysis_times),
            "ledger_write": _stats_ms(ledger_times),
            "blockchain_tx": _stats_ms(blockchain_times) if blockchain_times else None,
            "total": _stats_ms(total_times),
        },
        "video_sample_benchmark": {
            "sha256": v_sha,
            "file_size_bytes": v_res.get("dimensions"),
            "duration_seconds": v_res.get("duration_seconds"),
            "frames_analyzed": v_res.get("frames_analyzed"),
            "total_ms": v_total_ms,
        },
        "environment": {
            "os": platform.platform(),
            "processor": platform.processor(),
            "cpu_count": os.cpu_count(),
            "python_version": platform.python_version(),
            "opencv_version": cv2.__version__,
            "numpy_version": np.__version__,
            "web3_version": web3.__version__,
        },
    }
