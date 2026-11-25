"""
detector.py
-----------
Modular forensic media analysis subsystem for VERITY.

Implements:
  - BaseMediaDetector interface so a pretrained deep learning model (e.g.,
    XceptionNet or EfficientNet fine-tuned on FaceForensics++) can be plugged in
    later without altering the hashing, ledger, blockchain, or API layers.
  - HeuristicForensicDetector baseline computing:
      1. High-frequency noise residual R = I - G_sigma(I) statistics
      2. Regional edge-density consistency across a 4x4 grid
      3. Image consistency indicators (8x8 JPEG-grid artifact ratio & channel noise)
      4. EXIF / PNG-chunk metadata inspection (missing metadata recorded as
         unavailable evidence, never as proof of manipulation)
      5. Explicit AI-generator provenance indicators
      6. Equidistant sampled-frame video analysis (up to 12 frames) with
         per-frame scores, timestamps, pHashes, and mean/max aggregation.

IMPORTANT:
  Until calibrated against labeled datasets, outputs are documented heuristic
  suspicion scores, NOT scientifically validated manipulation probabilities.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
import os
from pathlib import Path
import time
from typing import Any

import cv2
import numpy as np
from PIL import ExifTags, Image

from app.config import get_settings
from app.hasher import compute_video_perceptual_fingerprint, perceptual_hash_from_gray_array
from app.security import MediaValidationError, safe_open_image

DETECTOR_VERSION = "heuristic-v2.0.0"
CONFIG_ID = "cfg-2026.11-baseline"

# Fixed, versioned baseline parameters (frozen before evaluation)
GAUSSIAN_SIGMA = 2.0
EDGE_GRID_ROWS = 4
EDGE_GRID_COLS = 4
LOW_EVIDENCE_MAX = 0.35
REVIEW_REQUIRED_MAX = 0.65

VERDICT_LOW = "Low Evidence"
VERDICT_REVIEW = "Review Required"
VERDICT_ELEVATED = "Elevated Evidence of Manipulation"
VERDICT_INCONCLUSIVE = "Inconclusive"

AI_GENERATOR_MARKERS = (
    "chatgpt",
    "openai",
    "dall-e",
    "dalle",
    "midjourney",
    "stable diffusion",
    "stablediffusion",
    "automatic1111",
    "comfyui",
    "firefly",
    "adobe firefly",
    "imagen",
    "flux",
    "ai generated",
    "ai-generated",
    "generated image",
    "generated-image",
)


def classify_heuristic_score(score: float | None, failed: bool = False) -> str:
    """Map bounded heuristic score [0, 1] into research-aligned evidence labels."""
    if failed or score is None:
        return VERDICT_INCONCLUSIVE
    if score < LOW_EVIDENCE_MAX:
        return VERDICT_LOW
    if score < REVIEW_REQUIRED_MAX:
        return VERDICT_REVIEW
    return VERDICT_ELEVATED


class BaseMediaDetector(ABC):
    """Abstract interface allowing drop-in replacement by learned detectors."""

    detector_version: str
    config_id: str

    @property
    def analysis_config_id(self) -> str:
        return f"{self.detector_version}:{self.config_id}"

    @abstractmethod
    def analyze_image(
        self, image_path: str | Path, original_filename: str | None = None
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def analyze_video(
        self, video_path: str | Path, original_filename: str | None = None
    ) -> dict[str, Any]:
        raise NotImplementedError


class HeuristicForensicDetector(BaseMediaDetector):
    """Lightweight statistical and forensic baseline detector."""

    def __init__(
        self,
        detector_version: str = DETECTOR_VERSION,
        config_id: str = CONFIG_ID,
    ) -> None:
        self.detector_version = detector_version
        self.config_id = config_id

    @staticmethod
    def _compute_noise_indicators(gray: np.ndarray) -> dict[str, float]:
        """Compute R = I - G_sigma(I) and regional residual dispersion."""
        gray_f = gray.astype(np.float32)
        blurred = cv2.GaussianBlur(gray_f, (0, 0), sigmaX=GAUSSIAN_SIGMA, sigmaY=GAUSSIAN_SIGMA)
        residual = gray_f - blurred

        residual_std = float(np.std(residual))
        residual_mean_abs = float(np.mean(np.abs(residual)))
        p95_abs = float(np.percentile(np.abs(residual), 95))

        h, w = residual.shape
        region_stds = []
        row_step = max(1, h // EDGE_GRID_ROWS)
        col_step = max(1, w // EDGE_GRID_COLS)
        for r in range(EDGE_GRID_ROWS):
            for c in range(EDGE_GRID_COLS):
                r0 = r * row_step
                r1 = (r + 1) * row_step if r < EDGE_GRID_ROWS - 1 else h
                c0 = c * col_step
                c1 = (c + 1) * col_step if c < EDGE_GRID_COLS - 1 else w
                patch = residual[r0:r1, c0:c1]
                if patch.size > 0:
                    region_stds.append(float(np.std(patch)))

        regional_std_mean = float(np.mean(region_stds)) if region_stds else 0.0
        regional_std_var = float(np.std(region_stds)) if region_stds else 0.0
        regional_noise_cv = (
            float(regional_std_var / (regional_std_mean + 1e-5)) if region_stds else 0.0
        )

        return {
            "gaussian_sigma": GAUSSIAN_SIGMA,
            "residual_std": round(residual_std, 4),
            "residual_mean_abs": round(residual_mean_abs, 4),
            "residual_p95_abs": round(p95_abs, 4),
            "regional_noise_cv": round(regional_noise_cv, 4),
        }

    @staticmethod
    def _compute_edge_indicators(gray: np.ndarray) -> dict[str, float]:
        """Partition image into 4x4 regions and compare regional Canny edge densities."""
        edges = cv2.Canny(gray, threshold1=80, threshold2=180)
        h, w = edges.shape
        row_step = max(1, h // EDGE_GRID_ROWS)
        col_step = max(1, w // EDGE_GRID_COLS)
        densities = []
        for r in range(EDGE_GRID_ROWS):
            for c in range(EDGE_GRID_COLS):
                r0 = r * row_step
                r1 = (r + 1) * row_step if r < EDGE_GRID_ROWS - 1 else h
                c0 = c * col_step
                c1 = (c + 1) * col_step if c < EDGE_GRID_COLS - 1 else w
                patch = edges[r0:r1, c0:c1]
                if patch.size > 0:
                    densities.append(float(np.mean(patch > 0)))

        mean_density = float(np.mean(densities)) if densities else 0.0
        std_density = float(np.std(densities)) if densities else 0.0
        cv_density = float(std_density / (mean_density + 1e-4)) if mean_density > 0 else 0.0
        return {
            "grid_regions": len(densities),
            "edge_density_mean": round(mean_density, 4),
            "edge_density_std": round(std_density, 4),
            "edge_density_cv": round(cv_density, 4),
        }

    @staticmethod
    def _compute_consistency_indicators(rgb: np.ndarray, gray: np.ndarray) -> dict[str, float]:
        """Estimate JPEG 8x8 boundary artifact ratio and cross-channel noise balance."""
        gray_f = gray.astype(np.float32)
        h, w = gray_f.shape
        if h >= 16 and w >= 16:
            diff_v = np.abs(np.diff(gray_f, axis=0))
            diff_h = np.abs(np.diff(gray_f, axis=1))
            v_block = diff_v[7::8, :].mean() if diff_v.shape[0] > 8 else diff_v.mean()
            v_inner = diff_v.mean()
            h_block = diff_h[:, 7::8].mean() if diff_h.shape[1] > 8 else diff_h.mean()
            h_inner = diff_h.mean()
            blockiness_ratio = float(
                ((v_block / (v_inner + 1e-5)) + (h_block / (h_inner + 1e-5))) / 2.0
            )
        else:
            blockiness_ratio = 1.0

        # Channel residual spread (R, G, B high-frequency consistency)
        channel_stds = []
        for ch in range(3):
            plane = rgb[:, :, ch].astype(np.float32)
            blur = cv2.GaussianBlur(plane, (0, 0), sigmaX=1.5, sigmaY=1.5)
            channel_stds.append(float(np.std(plane - blur)))
        channel_spread = float(np.std(channel_stds))

        return {
            "block_boundary_ratio": round(blockiness_ratio, 4),
            "channel_noise_spread": round(channel_spread, 4),
        }

    @staticmethod
    def _inspect_metadata(
        pil_image: Image.Image, image_path: str | Path, original_filename: str | None = None
    ) -> dict[str, Any]:
        """
        Inspect EXIF and image text chunks safely.
        Missing metadata is recorded as unavailable evidence, NOT proof of manipulation.
        Filename markers are separated as weak contextual signals.
        """
        exif_summary: dict[str, str] = {}
        try:
            raw_exif = pil_image.getexif()
            for tag_id, value in raw_exif.items():
                tag_name = str(ExifTags.TAGS.get(tag_id, tag_id))
                if tag_name in {"Make", "Model", "Software", "DateTime", "ImageDescription"}:
                    exif_summary[tag_name] = str(value)[:120]
        except Exception:
            exif_summary = {}

        text_chunks: dict[str, str] = {}
        for key, val in getattr(pil_image, "info", {}).items():
            if isinstance(val, (str, bytes)) and key.lower() in {
                "software",
                "parameters",
                "prompt",
                "comment",
                "description",
                "source",
            }:
                text_val = val.decode("utf-8", errors="ignore") if isinstance(val, bytes) else val
                text_chunks[str(key)] = text_val[:200]

        metadata_blob = " ".join(
            list(exif_summary.values()) + list(text_chunks.values())
        ).lower()

        matched_meta_markers = [m for m in AI_GENERATOR_MARKERS if m in metadata_blob]
        filename = (original_filename or os.path.basename(str(image_path))).lower()
        matched_filename_markers = [m for m in AI_GENERATOR_MARKERS if m in filename]

        metadata_present = bool(exif_summary or text_chunks)
        return {
            "metadata_status": "present" if metadata_present else "unavailable_evidence",
            "metadata_interpretation": (
                "Metadata inspected; missing fields are unavailable evidence, not proof of manipulation."
            ),
            "safe_exif_fields": exif_summary,
            "generator_metadata_found": bool(matched_meta_markers),
            "generator_metadata_markers": matched_meta_markers,
            "weak_filename_marker_found": bool(matched_filename_markers),
            "weak_filename_markers": matched_filename_markers,
        }

    def _score_from_indicators(
        self,
        noise: dict[str, float],
        edge: dict[str, float],
        consistency: dict[str, float],
        provenance: dict[str, Any],
    ) -> tuple[float, dict[str, float], str]:
        """Combine normalized forensic indicators using fixed baseline weights."""
        noise_norm = min(noise["residual_std"] / 28.0, 1.0)
        noise_cv_norm = min(noise["regional_noise_cv"] / 1.2, 1.0)
        edge_std_norm = min(edge["edge_density_std"] / 0.14, 1.0)
        block_norm = min(max(consistency["block_boundary_ratio"] - 1.0, 0.0) / 0.8, 1.0)
        channel_norm = min(consistency["channel_noise_spread"] / 4.0, 1.0)

        visual_score = (
            0.30 * noise_norm
            + 0.25 * noise_cv_norm
            + 0.25 * edge_std_norm
            + 0.10 * block_norm
            + 0.10 * channel_norm
        )
        visual_score = float(np.clip(visual_score, 0.0, 1.0))

        # Embedded generator metadata is an explicit provenance signal;
        # filename-only matches remain weak contextual evidence without overriding visual analysis.
        if provenance["generator_metadata_found"]:
            combined = max(visual_score, 0.78)
            rationale = (
                "Explicit AI-generator metadata markers detected alongside statistical image indicators."
            )
        else:
            combined = visual_score
            if provenance["weak_filename_marker_found"]:
                rationale = (
                    "Visual heuristic score computed; weak generator keyword observed in filename "
                    "(contextual note only, not proof)."
                )
            elif combined < LOW_EVIDENCE_MAX:
                rationale = "Statistical noise and regional edge indicators show low manipulation evidence."
            elif combined < REVIEW_REQUIRED_MAX:
                rationale = "Moderate statistical or regional edge variance observed; manual review recommended."
            else:
                rationale = "Elevated high-frequency residual or regional consistency anomalies detected."

        subscores = {
            "noise_residual_component": round(noise_norm, 4),
            "regional_noise_variance_component": round(noise_cv_norm, 4),
            "regional_edge_variance_component": round(edge_std_norm, 4),
            "block_consistency_component": round(block_norm, 4),
            "channel_spread_component": round(channel_norm, 4),
            "visual_heuristic_score": round(visual_score, 4),
        }
        return round(combined, 4), subscores, rationale

    def analyze_image_array(
        self,
        rgb_array: np.ndarray,
        *,
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run forensic analysis directly on an RGB numpy array."""
        t0 = time.perf_counter()
        gray = cv2.cvtColor(rgb_array, cv2.COLOR_RGB2GRAY)
        noise = self._compute_noise_indicators(gray)
        edge = self._compute_edge_indicators(gray)
        consistency = self._compute_consistency_indicators(rgb_array, gray)
        prov = provenance or {
            "metadata_status": "unavailable_evidence",
            "metadata_interpretation": "Frame decoded from video stream; EXIF unavailable.",
            "safe_exif_fields": {},
            "generator_metadata_found": False,
            "generator_metadata_markers": [],
            "weak_filename_marker_found": False,
            "weak_filename_markers": [],
        }

        score, subscores, rationale = self._score_from_indicators(
            noise, edge, consistency, prov
        )
        verdict = classify_heuristic_score(score)
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)

        return {
            "detector_type": "heuristic_baseline",
            "detector_version": self.detector_version,
            "config_id": self.config_id,
            "analysis_config_id": self.analysis_config_id,
            "is_calibrated_probability": False,
            "heuristic_score": score,
            "deepfake_probability": score,  # Backward-compatible field alias; heuristic suspicion score
            "verdict": verdict,
            "authenticity_flag": verdict,  # Backward-compatible field alias
            "interpretation_note": (
                "Heuristic suspicion score (uncalibrated statistical baseline, not a trained deep-learning probability)."
            ),
            "manipulation_type": rationale,
            "ai_generated_provenance": bool(prov["generator_metadata_found"]),
            "ai_generated": bool(prov["generator_metadata_found"]),
            "media_type": "image",
            "dimensions": {"width": int(rgb_array.shape[1]), "height": int(rgb_array.shape[0])},
            "indicators": {
                "noise_residual": noise,
                "regional_edge_density": edge,
                "consistency": consistency,
                "subscores": subscores,
                "provenance": prov,
            },
            "analysis_time_ms": elapsed_ms,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def analyze_image(
        self, image_path: str | Path, original_filename: str | None = None
    ) -> dict[str, Any]:
        """Decode image safely and compute forensic indicators and heuristic verdict."""
        t_decode_start = time.perf_counter()
        pil_img = safe_open_image(image_path)
        try:
            provenance = self._inspect_metadata(pil_img, image_path, original_filename)
            rgb = np.asarray(pil_img.convert("RGB"), dtype=np.uint8)
        finally:
            pil_img.close()
        decode_ms = round((time.perf_counter() - t_decode_start) * 1000.0, 2)

        result = self.analyze_image_array(rgb, provenance=provenance)
        result["decode_time_ms"] = decode_ms
        return result

    def analyze_video(
        self, video_path: str | Path, original_filename: str | None = None
    ) -> dict[str, Any]:
        """Sample up to 12 equidistant video frames, score each, and aggregate transparently."""
        t_start = time.perf_counter()
        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            raise MediaValidationError("The uploaded video could not be opened.", 422)

        try:
            frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
            width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            duration_seconds = round(frame_count / fps, 2) if fps > 0 and frame_count > 0 else 0.0

            max_samples = 12
            if frame_count > 0:
                sample_count = min(max_samples, frame_count)
                sample_indexes = np.linspace(0, frame_count - 1, sample_count, dtype=int).tolist()
            else:
                sample_indexes = list(range(max_samples))

            filename_lower = (original_filename or Path(video_path).name).lower()
            weak_filename_markers = [m for m in AI_GENERATOR_MARKERS if m in filename_lower]

            frame_reports: list[dict[str, Any]] = []
            frame_phashes: list[str] = []

            for idx in sample_indexes:
                if frame_count > 0:
                    capture.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
                success, frame_bgr = capture.read()
                if not success or frame_bgr is None:
                    if frame_count <= 0:
                        break
                    continue

                rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
                frame_phash = perceptual_hash_from_gray_array(gray)
                frame_phashes.append(frame_phash)

                frame_res = self.analyze_image_array(rgb)
                ts_sec = round(idx / fps, 3) if fps > 0 else 0.0
                frame_reports.append(
                    {
                        "frame_index": int(idx),
                        "timestamp_seconds": ts_sec,
                        "heuristic_score": frame_res["heuristic_score"],
                        "verdict": frame_res["verdict"],
                        "frame_phash": frame_phash,
                        "noise_residual_std": frame_res["indicators"]["noise_residual"][
                            "residual_std"
                        ],
                        "edge_density_std": frame_res["indicators"]["regional_edge_density"][
                            "edge_density_std"
                        ],
                    }
                )
        finally:
            capture.release()

        if not frame_reports:
            raise MediaValidationError("No readable frames were found in the uploaded video.", 422)

        scores = [item["heuristic_score"] for item in frame_reports]
        mean_score = round(float(np.mean(scores)), 4)
        max_score = round(float(np.max(scores)), 4)
        # Documented fixed aggregation rule: balanced 50% mean + 50% max across sampled frames
        aggregated_score = round(0.5 * mean_score + 0.5 * max_score, 4)
        verdict = classify_heuristic_score(aggregated_score)
        video_phash = compute_video_perceptual_fingerprint(frame_phashes)
        elapsed_ms = round((time.perf_counter() - t_start) * 1000.0, 2)

        return {
            "detector_type": "heuristic_baseline",
            "detector_version": self.detector_version,
            "config_id": self.config_id,
            "analysis_config_id": self.analysis_config_id,
            "is_calibrated_probability": False,
            "heuristic_score": aggregated_score,
            "deepfake_probability": aggregated_score,
            "verdict": verdict,
            "authenticity_flag": verdict,
            "aggregation_rule": "0.5 * mean_frame_score + 0.5 * max_frame_score",
            "mean_frame_score": mean_score,
            "max_frame_score": max_score,
            "interpretation_note": (
                "Sampled-frame heuristic suspicion score. Sparse frame sampling may miss short "
                "manipulated segments and does not analyze audio or audiovisual synchronization."
            ),
            "manipulation_type": (
                f"Aggregated across {len(frame_reports)} sampled frames "
                f"(mean={mean_score:.4f}, max={max_score:.4f})."
            ),
            "ai_generated_provenance": False,
            "ai_generated": False,
            "media_type": "video",
            "dimensions": {"width": width, "height": height},
            "frames_analyzed": len(frame_reports),
            "total_frame_count": frame_count,
            "fps": round(fps, 3),
            "duration_seconds": duration_seconds,
            "audio_analysis_performed": False,
            "video_perceptual_fingerprint": video_phash,
            "frame_results": frame_reports,
            "indicators": {
                "aggregation_rule": "0.5 * mean_frame_score + 0.5 * max_frame_score",
                "mean_frame_score": mean_score,
                "max_frame_score": max_score,
                "weak_filename_marker_found": bool(weak_filename_markers),
                "weak_filename_markers": weak_filename_markers,
            },
            "analysis_time_ms": elapsed_ms,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


def get_active_detector() -> BaseMediaDetector:
    settings = get_settings()
    return HeuristicForensicDetector(
        detector_version=settings.detector_version,
        config_id=settings.config_id,
    )


def analyze_image(image_path: str | Path, original_filename: str | None = None) -> dict[str, Any]:
    return get_active_detector().analyze_image(image_path, original_filename)


def analyze_video(video_path: str | Path, original_filename: str | None = None) -> dict[str, Any]:
    return get_active_detector().analyze_video(video_path, original_filename)
