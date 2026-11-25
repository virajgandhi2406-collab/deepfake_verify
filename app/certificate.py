"""
certificate.py
--------------
Builds structured verification certificates and generates downloadable PDF
certificates with QR codes resolving to the VERITY verification endpoint.
"""

from __future__ import annotations

import io
from typing import Any

import qrcode
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.config import get_settings
from app.ledger import content_sha256_of, record_hash_of, sequence_of, verification_id_of
from app.security import sanitize_filename_for_public


def generate_certificate(
    record: dict[str, Any],
    *,
    hash_status: str | None = None,
    similarity_matches: list[dict[str, Any]] | None = None,
    public_base_url: str | None = None,
    expose_filenames: bool | None = None,
) -> dict[str, Any]:
    """Build a canonical verification certificate dict from a verification record."""
    settings = get_settings()
    base_url = (public_base_url or settings.public_base_url).rstrip("/")
    show_names = settings.expose_filenames if expose_filenames is None else expose_filenames

    ai = record.get("ai_result", {})
    ver_id = verification_id_of(record)
    sha256_hex = content_sha256_of(record)
    media_type = record.get("media_type") or ai.get("media_type", "image")
    rec_hash = record_hash_of(record)
    seq = sequence_of(record)

    fp = record.get("perceptual_fingerprints")
    if isinstance(fp, dict):
        primary_phash = fp.get("primary_phash") or fp.get("primary_reference") or record.get("perceptual_hash", "")
    else:
        primary_phash = record.get("perceptual_hash", "")
        fp = {"algorithm": "legacy-v1", "primary_phash": primary_phash}

    score = record.get("heuristic_score")
    if score is None:
        score = ai.get("heuristic_score", ai.get("deepfake_probability"))
    verdict = record.get("verdict") or ai.get("verdict") or ai.get("authenticity_flag", "Inconclusive")

    bc = record.get("blockchain") or {
        "anchoring_status": "local_only_legacy",
        "tx_hash": None,
        "block_number": None,
        "contract_address": None,
        "chain_id": None,
        "network_name": None,
        "submitter": None,
        "event_key": None,
    }

    verification_url = f"{base_url}/verify-certificate/{ver_id}"
    safe_name = sanitize_filename_for_public(
        record.get("filename"), sha256_hex, media_type, expose_filenames=show_names
    )

    cert = {
        "verification_id": ver_id,
        "certificate_id": ver_id,
        "filename": safe_name,
        "raw_filename_stored": bool(record.get("filename")) and show_names,
        "media_type": media_type,
        "file_size_bytes": record.get("file_size_bytes"),
        "verdict": verdict,
        "heuristic_score": score,
        "deepfake_probability": score,
        "is_calibrated_probability": ai.get("is_calibrated_probability", False),
        "detector_version": record.get("detector_version") or ai.get("detector_version", "legacy-v1"),
        "configuration_id": record.get("configuration_id") or ai.get("config_id", "legacy-v1"),
        "analysis_config_id": record.get("analysis_config_id") or ai.get("analysis_config_id", "legacy-v1"),
        "ai_generated": ai.get("ai_generated_provenance", ai.get("ai_generated", False)),
        "frames_analyzed": ai.get("frames_analyzed"),
        "duration_seconds": ai.get("duration_seconds"),
        "aggregation_rule": ai.get("aggregation_rule"),
        "mean_frame_score": ai.get("mean_frame_score"),
        "max_frame_score": ai.get("max_frame_score"),
        "frame_results": ai.get("frame_results"),
        "manipulation_type": ai.get("manipulation_type", ""),
        "analysis_indicators": record.get("analysis_indicators") or ai.get("indicators", {}),
        "content_hash_sha256": sha256_hex,
        "perceptual_hash": primary_phash,
        "perceptual_fingerprints": fp,
        "recorded_at": record.get("server_timestamp") or record.get("recorded_at"),
        "ledger_index": seq,
        "ledger_record_hash": rec_hash,
        "ledger_block_hash": rec_hash,
        "previous_block_hash": record.get("previous_hash", ""),
        "blockchain": bc,
        "verification_url": verification_url,
        "pdf_download_url": f"{base_url}/certificate/{ver_id}",
        "hash_status": hash_status or "verified record",
        "similarity_matches": similarity_matches or [],
        "limitations": [
            "A recorded SHA-256 hash establishes exact file identity relative to this verification record, not absolute authenticity of the original scene.",
            "The heuristic score is an uncalibrated statistical indicator, not a trained deep-learning manipulation probability.",
            "For videos, equidistant frame sampling (up to 12 frames) may miss short manipulated segments and does not inspect audio.",
        ],
    }
    return cert


def _qr_image_reader(url: str) -> ImageReader:
    qr = qrcode.QRCode(box_size=6, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0f172a", back_color="white").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return ImageReader(buf)


def generate_certificate_pdf(
    certificate: dict[str, Any], *, on_chain_verified: bool | None = None
) -> bytes:
    """Render a downloadable VERITY verification certificate PDF with QR code."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    # Header band
    c.setFillColor(colors.HexColor("#0B1426"))
    c.rect(0, height - 34 * mm, width, 34 * mm, stroke=0, fill=1)

    c.setFillColor(colors.HexColor("#60A5FA"))
    c.setFont("Helvetica-Bold", 9)
    c.drawString(18 * mm, height - 11 * mm, "VERITY DIGITAL CONTENT VERIFICATION CERTIFICATE")

    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 17)
    c.drawString(18 * mm, height - 19 * mm, "Content Authenticity & Provenance Report")

    c.setFillColor(colors.HexColor("#94A3B8"))
    c.setFont("Helvetica", 8.5)
    c.drawString(
        18 * mm,
        height - 26 * mm,
        "M.Sc. Computer Science Research | Dr. Vishwanath Karad MIT World Peace University, Pune (2026-27)",
    )

    y = height - 44 * mm

    def section_title(title: str) -> None:
        nonlocal y
        c.setFillColor(colors.HexColor("#1E3A8A"))
        c.setFont("Helvetica-Bold", 10.5)
        c.drawString(18 * mm, y, title)
        c.setStrokeColor(colors.HexColor("#CBD5E1"))
        c.line(18 * mm, y - 2 * mm, width - 18 * mm, y - 2 * mm)
        y -= 7 * mm

    def kv_row(label: str, value: str, mono: bool = False) -> None:
        nonlocal y
        c.setFillColor(colors.HexColor("#475569"))
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(18 * mm, y, f"{label}:")
        c.setFillColor(colors.HexColor("#0F172A"))
        c.setFont("Courier" if mono else "Helvetica", 8.5)
        text = str(value or "N/A")
        if len(text) > 78:
            text = text[:75] + "..."
        c.drawString(62 * mm, y, text)
        y -= 5.4 * mm

    section_title("1. Verification Identity & Media Summary")
    kv_row("Verification ID", str(certificate.get("verification_id")), mono=True)
    kv_row("Reference Label", str(certificate.get("filename")))
    kv_row("Media Type", str(certificate.get("media_type", "image")).upper())
    kv_row("File Size (bytes)", str(certificate.get("file_size_bytes") or "N/A"))
    kv_row("Recorded Timestamp", str(certificate.get("recorded_at")))

    y -= 2 * mm
    section_title("2. Cryptographic & Perceptual Fingerprints")
    kv_row("SHA-256 Digest", str(certificate.get("content_hash_sha256")), mono=True)
    kv_row("Perceptual Hash", str(certificate.get("perceptual_hash")), mono=True)
    kv_row("Local Record Hash", str(certificate.get("ledger_record_hash")), mono=True)
    kv_row("Local Sequence #", str(certificate.get("ledger_index")))

    y -= 2 * mm
    section_title("3. Forensic Analysis Summary (Heuristic Baseline)")
    score = certificate.get("heuristic_score")
    score_str = f"{score:.4f} (uncalibrated suspicion score)" if isinstance(score, (int, float)) else "N/A"
    kv_row("Evidence Verdict", str(certificate.get("verdict")))
    kv_row("Heuristic Score", score_str)
    kv_row("Detector / Config", str(certificate.get("analysis_config_id")))
    kv_row("Assessment Note", str(certificate.get("manipulation_type")))
    if certificate.get("frames_analyzed"):
        kv_row(
            "Video Sampling",
            f"{certificate.get('frames_analyzed')} frames | Duration: {certificate.get('duration_seconds')}s | {certificate.get('aggregation_rule') or ''}",
        )

    y -= 2 * mm
    section_title("4. Blockchain Anchoring & Smart Contract Proof")
    bc = certificate.get("blockchain") or {}
    chain_status = (
        "VERIFIED ON-CHAIN"
        if on_chain_verified is True
        else ("NOT FOUND ON-CHAIN" if on_chain_verified is False else str(bc.get("anchoring_status", "N/A")).upper())
    )
    kv_row("Blockchain Status", chain_status)
    kv_row("Network / Chain ID", f"{bc.get('network_name') or 'N/A'} (Chain ID: {bc.get('chain_id') or 'N/A'})")
    kv_row("Smart Contract", str(bc.get("contract_address") or "Not anchored"), mono=True)
    kv_row("Transaction Hash", str(bc.get("tx_hash") or "Not anchored"), mono=True)
    kv_row("Block Number", str(bc.get("block_number") if bc.get("block_number") is not None else "N/A"))
    kv_row("Submitting Wallet", str(bc.get("submitter") or "N/A"), mono=True)

    # QR code + verification endpoint
    y -= 3 * mm
    qr_url = str(certificate.get("verification_url", ""))
    qr_reader = _qr_image_reader(qr_url)
    qr_size = 28 * mm
    c.drawImage(qr_reader, width - 18 * mm - qr_size, y - qr_size + 4 * mm, qr_size, qr_size)

    section_title("5. Independent Verification & Research Limitations")
    c.setFillColor(colors.HexColor("#334155"))
    c.setFont("Helvetica", 8)
    lines = [
        f"Verification Endpoint: {qr_url}",
        "Interpretation Notice: A recorded SHA-256 hash establishes exact file identity relative to",
        "this verification record; it does NOT establish that the original media or scene is genuine.",
        "Heuristic forensic scores are baseline statistical indicators, not calibrated probabilities.",
    ]
    for line in lines:
        c.drawString(18 * mm, y, line)
        y -= 4.5 * mm

    c.setFillColor(colors.HexColor("#64748B"))
    c.setFont("Helvetica-Oblique", 7.5)
    c.drawString(
        18 * mm,
        14 * mm,
        "Generated by VERITY | Blockchain-Based Verification System for Digital Content Authenticity and Deepfake Detection",
    )

    c.showPage()
    c.save()
    return buf.getvalue()
