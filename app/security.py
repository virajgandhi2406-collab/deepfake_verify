"""
security.py
-----------
Input validation, file-signature verification, decoded-image resource limits,
safe temporary file lifecycle, and metadata privacy controls for VERITY.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import tempfile
from typing import Iterator

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError

from app.config import VeritySettings, get_settings

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}

SENSITIVE_EXIF_TAGS = {
    "GPSInfo",
    "MakerNote",
    "UserComment",
    "CameraOwnerName",
    "BodySerialNumber",
    "LensSerialNumber",
    "Artist",
    "Copyright",
}


class MediaValidationError(ValueError):
    """Raised when an uploaded media file fails signature, size, or format checks."""

    def __init__(self, message: str, status_code: int = 415) -> None:
        super().__init__(message)
        self.status_code = status_code


def inspect_file_signature(file_path: str | Path) -> str:
    """Inspect magic bytes of a file and return detected container/image type."""
    with open(file_path, "rb") as fh:
        header = fh.read(32)

    if len(header) < 4:
        raise MediaValidationError("Uploaded file is empty or too small to identify.", 400)

    if header.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp"
    if header.startswith(b"BM"):
        return "bmp"
    if header.startswith((b"II*\x00", b"MM\x00*")):
        return "tiff"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        brand = header[8:12]
        if brand in {b"qt  "}:
            return "mov"
        return "mp4"
    if header[:4] == b"RIFF" and header[8:12] == b"AVI ":
        return "avi"
    if header.startswith(b"\x1a\x45\xdf\xa3"):
        return "matroska_webm"

    raise MediaValidationError(
        "Unsupported or unrecognized file signature. Upload a valid image (JPG, PNG, WEBP, BMP, TIFF) or video (MP4, MOV, AVI, MKV, WEBM).",
        415,
    )


def validate_media_file(
    file_path: str | Path,
    *,
    expected_kind: str,
    original_filename: str | None = None,
    settings: VeritySettings | None = None,
) -> dict:
    """Validate file size, extension alignment, and binary signature."""
    cfg = settings or get_settings()
    path = Path(file_path)
    size_bytes = path.stat().st_size
    if size_bytes == 0:
        raise MediaValidationError("Uploaded file is empty.", 400)

    max_bytes = cfg.max_image_bytes if expected_kind == "image" else cfg.max_video_bytes
    if size_bytes > max_bytes:
        limit_mb = max_bytes // (1024 * 1024)
        raise MediaValidationError(
            f"Uploaded {expected_kind} exceeds maximum allowed size of {limit_mb} MB.",
            413,
        )

    detected_format = inspect_file_signature(path)
    image_formats = {"jpeg", "png", "webp", "bmp", "tiff"}
    video_formats = {"mp4", "mov", "avi", "matroska_webm"}

    if expected_kind == "image" and detected_format not in image_formats:
        raise MediaValidationError(
            f"File signature '{detected_format}' is not a supported image format.",
            415,
        )
    if expected_kind == "video" and detected_format not in video_formats:
        raise MediaValidationError(
            f"File signature '{detected_format}' is not a supported video format.",
            415,
        )

    ext = Path(original_filename or path.name).suffix.lower()
    allowed_exts = IMAGE_EXTENSIONS if expected_kind == "image" else VIDEO_EXTENSIONS
    if ext and ext not in allowed_exts:
        raise MediaValidationError(
            f"Unsupported {expected_kind} file extension '{ext}'.",
            415,
        )

    return {
        "file_size_bytes": size_bytes,
        "detected_format": detected_format,
        "extension": ext,
    }


def safe_open_image(
    file_path: str | Path, settings: VeritySettings | None = None
) -> Image.Image:
    """Open an image while enforcing decoded pixel limits against decompression bombs."""
    cfg = settings or get_settings()
    Image.MAX_IMAGE_PIXELS = cfg.max_image_pixels
    try:
        img = Image.open(file_path)
        width, height = img.size
        if width <= 0 or height <= 0:
            raise MediaValidationError("Decoded image has invalid dimensions.", 422)
        if width * height > cfg.max_image_pixels:
            raise MediaValidationError(
                f"Image pixel count ({width * height}) exceeds resource limit ({cfg.max_image_pixels}).",
                413,
            )
        img.load()
        return img
    except Image.DecompressionBombError as exc:
        raise MediaValidationError(
            "Image exceeds maximum safe decoded pixel limit (decompression bomb protection).",
            413,
        ) from exc
    except (UnidentifiedImageError, OSError) as exc:
        raise MediaValidationError(f"Image could not be safely decoded: {exc}", 422) from exc


@contextmanager
def managed_temp_upload(
    upload: UploadFile, max_bytes: int, default_suffix: str = ".bin"
) -> Iterator[Path]:
    """Stream an UploadFile to a secure temp file with size enforcement and guaranteed cleanup."""
    raw_name = Path(upload.filename or "upload").name
    suffix = Path(raw_name).suffix.lower() or default_suffix
    tmp_fd, tmp_name = tempfile.mkstemp(prefix="verity_", suffix=suffix)
    tmp_path = Path(tmp_name)
    total_read = 0
    try:
        with os.fdopen(tmp_fd, "wb") as out:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                total_read += len(chunk)
                if total_read > max_bytes:
                    limit_mb = max_bytes // (1024 * 1024)
                    raise MediaValidationError(
                        f"Upload exceeded maximum allowed size of {limit_mb} MB.",
                        413,
                    )
                out.write(chunk)
        yield tmp_path
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


def sanitize_filename_for_public(
    filename: str | None,
    content_sha256: str,
    media_type: str,
    expose_filenames: bool = False,
) -> str:
    """Avoid leaking personal filenames on public endpoints unless explicitly enabled."""
    if expose_filenames and filename:
        return Path(filename).name
    ext = Path(filename or "").suffix.lower()
    short_hash = content_sha256[:12] if content_sha256 else "unknown"
    return f"{media_type}-{short_hash}{ext}"
