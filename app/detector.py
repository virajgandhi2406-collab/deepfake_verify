"""
detector.py
-----------
This module represents the "AI-powered Manipulation Analysis" block from
your architecture diagram (CNN/RNN -> AI Analysis Result).

IMPORTANT (read this):
Training or downloading a full deepfake-detection CNN (e.g. on FaceForensics++)
needs a GPU, a large dataset, and hours of training time -- not realistic to
generate on the fly. So this file ships with a HEURISTIC placeholder detector
that analyses real image statistics (noise patterns, edge consistency,
compression artifacts) and produces the exact same OUTPUT SHAPE a trained CNN
would produce.

This means:
  - The pipeline (hashing, ledger, certificate, API, dashboard) is fully real
    and functional right now.
  - Later, you can swap out `analyze_image()` with a real PyTorch/TensorFlow
    model's `.predict()` call and nothing else in the project needs to change,
    because the output contract (the dict below) stays the same.

To plug in a real model later:
  1. Train or download a pretrained deepfake classifier (e.g. a ResNet/Xception
     fine-tuned on FaceForensics++ or the DFDC dataset).
  2. Replace the body of `analyze_image()` with: run the model on `image`,
     get a probability, and return it in the same dict format.
"""

from datetime import datetime, timezone
import os
import tempfile

import cv2
from PIL import Image, ImageFilter
import numpy as np


AI_GENERATOR_MARKERS = (
    "chatgpt",
    "openai",
    "dall-e",
    "dalle",
    "midjourney",
    "stable diffusion",
    "stablediffusion",
    "firefly",
    "adobe firefly",
    "generated image",
    "generated-image",
    "ai generated",
    "ai-generated",
)


def _noise_score(image: Image.Image) -> float:
    """Estimate high-frequency noise irregularity (deepfakes often blend
    regions with mismatched noise/compression profiles)."""
    gray = image.convert("L")
    arr = np.asarray(gray, dtype=np.float32)
    blurred = np.asarray(gray.filter(ImageFilter.GaussianBlur(radius=2)), dtype=np.float32)
    residual = arr - blurred
    return float(np.std(residual))


def _edge_consistency_score(image: Image.Image) -> float:
    """Estimate blending-boundary artifacts using edge-map variance across
    quadrants of the image (real photos tend to have more uniform edge
    density than face-swapped/composited ones)."""
    gray = image.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    arr = np.asarray(edges, dtype=np.float32)
    h, w = arr.shape
    quadrants = [
        arr[: h // 2, : w // 2],
        arr[: h // 2, w // 2 :],
        arr[h // 2 :, : w // 2],
        arr[h // 2 :, w // 2 :],
    ]
    means = [q.mean() for q in quadrants]
    return float(np.std(means))


def _ai_provenance_signal(
    image: Image.Image, image_path: str, original_filename: str | None = None
) -> tuple[bool, str]:
    """Find explicit provenance clues left by common image generators.

    These clues are useful for exported ChatGPT/DALL-E files, but they are
    not proof: filenames and metadata can be changed or removed.
    """
    filename = (original_filename or os.path.basename(image_path)).lower()
    if any(marker in filename for marker in AI_GENERATOR_MARKERS):
        return True, "AI-generator name found in the filename"

    metadata = " ".join(
        str(value).lower()
        for value in image.getexif().values()
        if value is not None
    )
    if any(marker in metadata for marker in AI_GENERATOR_MARKERS):
        return True, "AI-generator metadata found in the image"

    return False, ""


def analyze_image(image_path: str, original_filename: str | None = None) -> dict:
    """
    Runs the (placeholder) manipulation analysis on an image file.

    Returns a dict matching the "AI Analysis Result" box in your diagram:
        {
          "deepfake_probability": float (0-1),
          "authenticity_flag": "Authentic" | "Potentially Manipulated" | "Manipulated",
          "manipulation_type": str,
          "timestamp": ISO8601 string
        }
    """
    source_image = Image.open(image_path)
    image = source_image.convert("RGB")

    noise = _noise_score(image)
    edge_var = _edge_consistency_score(image)
    ai_provenance, provenance_reason = _ai_provenance_signal(
        source_image, image_path, original_filename
    )

    # Normalize the two heuristic signals into a pseudo-probability.
    # These thresholds are illustrative -- tune them against your own
    # test images, or replace this whole function with a real model.
    noise_component = min(noise / 40.0, 1.0)
    edge_component = min(edge_var / 25.0, 1.0)
    visual_probability = 0.5 * noise_component + 0.5 * edge_component
    probability = max(visual_probability, 0.98 if ai_provenance else visual_probability)
    probability = round(probability, 4)

    if ai_provenance:
        flag = "AI-Generated"
        manipulation_type = f"Likely AI-generated ({provenance_reason})"
    elif probability < 0.35:
        flag = "Authentic"
        manipulation_type = "None detected"
    elif probability < 0.65:
        flag = "Potentially Manipulated"
        manipulation_type = "Uncertain — possible compression or partial edit"
    else:
        flag = "Manipulated"
        manipulation_type = "Possible face-blend / deepfake artifact"

    return {
        "deepfake_probability": probability,
        "authenticity_flag": flag,
        "manipulation_type": manipulation_type,
        "ai_generated": ai_provenance,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def analyze_video(video_path: str, original_filename: str | None = None) -> dict:
    """Sample a video and aggregate image analysis across its frames."""
    capture = cv2.VideoCapture(video_path)
    if not capture.isOpened():
        raise ValueError("The uploaded video could not be opened.")

    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
    duration_seconds = round(frame_count / fps, 2) if fps > 0 else 0
    sample_count = min(12, max(1, frame_count))
    sample_indexes = np.linspace(0, max(frame_count - 1, 0), sample_count, dtype=int)
    results = []

    with tempfile.TemporaryDirectory() as frame_directory:
        for position, frame_index in enumerate(sample_indexes):
            capture.set(cv2.CAP_PROP_POS_FRAMES, int(frame_index))
            success, frame = capture.read()
            if not success:
                continue
            frame_path = os.path.join(frame_directory, f"frame-{position}.jpg")
            if cv2.imwrite(frame_path, frame):
                results.append(analyze_image(frame_path, original_filename))
    capture.release()

    if not results:
        raise ValueError("No readable frames were found in the uploaded video.")

    probability = round(max(result["deepfake_probability"] for result in results), 4)
    ai_generated = any(result["ai_generated"] for result in results)
    if ai_generated:
        flag = "AI-Generated"
        manipulation_type = "Likely AI-generated video (generator provenance clue detected)"
    elif probability < 0.35:
        flag = "Authentic"
        manipulation_type = "No strong manipulation indicators detected in sampled frames"
    elif probability < 0.65:
        flag = "Potentially Manipulated"
        manipulation_type = "Possible edit or deepfake artifact in sampled frames"
    else:
        flag = "Manipulated"
        manipulation_type = "Strong manipulation indicators in sampled frames"

    return {
        "deepfake_probability": probability,
        "authenticity_flag": flag,
        "manipulation_type": manipulation_type,
        "ai_generated": ai_generated,
        "media_type": "video",
        "frames_analyzed": len(results),
        "duration_seconds": duration_seconds,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
