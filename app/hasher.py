"""
hasher.py
---------
Dual-layer digital fingerprinting for VERITY:
1. Cryptographic SHA-256 computed on exact raw file bytes before decoding.
2. Documented 64-bit DCT-based perceptual hash (pHash) for visual similarity
   detection and Hamming-distance comparison.
3. Sampled-frame perceptual fingerprint sets for video inputs (never treating a
   single frame's hash as the identity of an entire video).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from app.security import safe_open_image

PHASH_ALGORITHM_ID = "dct-64-median-ac-v1"


def sha256_of_bytes(data: bytes) -> str:
    """Compute SHA-256 hex digest over exact raw bytes."""
    return hashlib.sha256(data).hexdigest()


def sha256_of_file(file_path: str | Path) -> str:
    """Exact-match cryptographic SHA-256 over raw file bytes before decoding."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def perceptual_hash_from_gray_array(gray_array: np.ndarray, hash_size: int = 8) -> str:
    """
    Compute a 64-bit DCT-based perceptual hash from a 2D grayscale array.

    Procedure:
      1. Resize grayscale image to (32, 32) using Lanczos/area interpolation.
      2. Compute 2D Discrete Cosine Transform (DCT-II).
      3. Retain the top-left 8x8 low-frequency coefficients (64 values).
      4. Compute the median over the 63 AC coefficients (excluding DC [0,0])
         so global brightness shifts do not dominate the threshold.
      5. Threshold all 64 coefficients against that median to form a 64-bit hex string.
    """
    dct_size = hash_size * 4  # 32x32 for 8x8 hash
    resized = cv2.resize(
        gray_array.astype(np.float32),
        (dct_size, dct_size),
        interpolation=cv2.INTER_AREA,
    )
    dct_matrix = cv2.dct(resized)
    low_freq = dct_matrix[:hash_size, :hash_size].flatten()
    ac_coeffs = low_freq[1:]
    median_val = float(np.median(ac_coeffs))
    bits = low_freq > median_val
    hash_int = 0
    for bit in bits:
        hash_int = (hash_int << 1) | int(bool(bit))
    hex_len = (hash_size * hash_size) // 4
    return format(hash_int, f"0{hex_len}x")


def perceptual_hash(file_path: str | Path, hash_size: int = 8) -> str:
    """Compute documented 64-bit DCT perceptual hash for an image file."""
    image = safe_open_image(file_path)
    try:
        gray = image.convert("L").resize((hash_size * 4, hash_size * 4), Image.LANCZOS)
        arr = np.asarray(gray, dtype=np.float32)
        dct_matrix = cv2.dct(arr)
        low_freq = dct_matrix[:hash_size, :hash_size].flatten()
        median_val = float(np.median(low_freq[1:]))
        bits = low_freq > median_val
        hash_int = 0
        for bit in bits:
            hash_int = (hash_int << 1) | int(bool(bit))
        hex_len = (hash_size * hash_size) // 4
        return format(hash_int, f"0{hex_len}x")
    finally:
        image.close()


def hamming_distance(hash_a: str, hash_b: str) -> int:
    """Count differing bits between two equal-length hexadecimal perceptual hashes."""
    a = hash_a.strip().lower()
    b = hash_b.strip().lower()
    if len(a) != len(b):
        raise ValueError(f"Perceptual hash length mismatch: {len(a)} vs {len(b)}")
    xor_val = int(a, 16) ^ int(b, 16)
    return xor_val.bit_count()


def evaluate_similarity(
    hash_a: str, hash_b: str, threshold: int = 10
) -> dict[str, Any]:
    """Compare two 64-bit perceptual hashes using Hamming distance."""
    dist = hamming_distance(hash_a, hash_b)
    similarity_ratio = round(1.0 - (dist / 64.0), 4)
    return {
        "hamming_distance": dist,
        "threshold": threshold,
        "visually_similar": dist <= threshold,
        "similarity_ratio": similarity_ratio,
    }


def compute_video_perceptual_fingerprint(frame_phashes: list[str]) -> dict[str, Any]:
    """
    Build a composite perceptual fingerprint summary from sampled video frames.
    Never treats a single frame's pHash as the identity of the entire video.
    """
    canonical_joined = "|".join(frame_phashes)
    frame_set_digest = hashlib.sha256(canonical_joined.encode("utf-8")).hexdigest()
    return {
        "algorithm": PHASH_ALGORITHM_ID,
        "mode": "sampled-frame-set",
        "frame_count": len(frame_phashes),
        "frame_phashes": frame_phashes,
        "primary_reference": f"vframes:{frame_set_digest[:24]}",
        "frame_set_digest_sha256": frame_set_digest,
    }


def find_similar_image_records(
    target_phash: str,
    records: list[dict[str, Any]],
    *,
    exact_sha256: str,
    threshold: int = 10,
) -> list[dict[str, Any]]:
    """
    Search existing image verification records for perceptual-hash matches,
    keeping exact SHA-256 identity separate from visual similarity.
    """
    matches: list[dict[str, Any]] = []
    seen_sha: set[str] = set()
    for rec in records:
        rec_sha = rec.get("content_sha256") or rec.get("content_hash")
        if not rec_sha or rec_sha == exact_sha256 or rec_sha in seen_sha:
            continue
        if rec.get("media_type", "image") != "image":
            continue
        rec_phash = (
            rec.get("perceptual_fingerprints", {}).get("primary_phash")
            or rec.get("perceptual_hash")
        )
        if not rec_phash or len(rec_phash) != len(target_phash):
            continue
        try:
            int(rec_phash, 16)
        except ValueError:
            continue

        sim = evaluate_similarity(target_phash, rec_phash, threshold=threshold)
        if sim["visually_similar"]:
            seen_sha.add(rec_sha)
            matches.append(
                {
                    "verification_id": rec.get("verification_id")
                    or (rec.get("block_hash", "")[:16]),
                    "content_sha256": rec_sha,
                    "perceptual_hash": rec_phash,
                    "hamming_distance": sim["hamming_distance"],
                    "similarity_ratio": sim["similarity_ratio"],
                }
            )

    matches.sort(key=lambda item: item["hamming_distance"])
    return matches
