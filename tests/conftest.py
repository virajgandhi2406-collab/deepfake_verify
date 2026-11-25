"""Shared pytest fixtures for VERITY unit and blockchain integration tests."""

from __future__ import annotations

from pathlib import Path
import cv2
import numpy as np
from PIL import Image
import pytest


@pytest.fixture
def sample_image_pair(tmp_path: Path) -> tuple[Path, Path]:
    """Create two visually distinct valid JPEG images."""
    p1 = tmp_path / "sample_one.jpg"
    p2 = tmp_path / "sample_two.jpg"

    y, x = np.mgrid[0:256, 0:256].astype(np.float32)
    img1 = np.stack(
        [
            np.clip(120 + 60 * np.sin(x / 10.0), 0, 255),
            np.clip(120 + 60 * np.cos(y / 12.0), 0, 255),
            np.clip(110 + 40 * np.sin((x + y) / 15.0), 0, 255),
        ],
        axis=-1,
    ).astype(np.uint8)
    Image.fromarray(img1, mode="RGB").save(p1, format="JPEG", quality=92)

    img2 = np.zeros((256, 256, 3), dtype=np.uint8)
    img2[:128, :128] = [230, 40, 40]
    img2[128:, 128:] = [30, 200, 220]
    Image.fromarray(img2, mode="RGB").save(p2, format="JPEG", quality=92)
    return p1, p2


@pytest.fixture
def sample_video_file(tmp_path: Path) -> Path:
    """Create a valid short MP4 video for OpenCV frame-sampling tests."""
    vid = tmp_path / "sample_video.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(vid), fourcc, 10.0, (320, 240))
    for i in range(15):
        frame = np.full((240, 320, 3), fill_value=(20 + i * 10) % 255, dtype=np.uint8)
        cv2.rectangle(frame, (30 + i * 8, 50), (110 + i * 8, 170), (40, 180, 240), -1)
        writer.write(frame)
    writer.release()
    return vid
