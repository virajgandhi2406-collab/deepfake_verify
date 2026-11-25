"""Unit tests for hashing, forensic detection, canonical ledger, security, and evaluation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from PIL import Image, PngImagePlugin
import pytest

from app.certificate import generate_certificate, generate_certificate_pdf
from app.detector import (
    VERDICT_ELEVATED,
    VERDICT_LOW,
    VERDICT_REVIEW,
    HeuristicForensicDetector,
)
from app.hasher import (
    compute_video_perceptual_fingerprint,
    evaluate_similarity,
    hamming_distance,
    perceptual_hash,
    sha256_of_file,
)
from app.ledger import (
    add_record,
    compute_ledger_root_hash,
    compute_record_hash_v2,
    get_record_by_hash,
    verify_against_checkpoint,
    verify_local_chain_integrity,
)
from app.security import MediaValidationError, validate_media_file
from evaluation.dataset_manifest import DatasetSample, validate_manifest
from evaluation.rq2_fingerprints import evaluate_rq2


def test_sha256_and_dct_perceptual_hash(sample_image_pair, tmp_path: Path):
    p1, p2 = sample_image_pair
    sha1 = sha256_of_file(p1)
    assert len(sha1) == 64

    # One-byte change alters SHA-256 completely
    mutated = tmp_path / "mutated.jpg"
    raw = bytearray(p1.read_bytes())
    raw[-1] ^= 0xFF
    mutated.write_bytes(bytes(raw))
    assert sha256_of_file(mutated) != sha1

    # DCT perceptual hash stability under resize vs distinctness
    ph1 = perceptual_hash(p1)
    ph2 = perceptual_hash(p2)
    assert len(ph1) == 16
    assert hamming_distance(ph1, ph1) == 0

    resized = tmp_path / "resized.jpg"
    with Image.open(p1) as img:
        img.resize((128, 128), Image.LANCZOS).save(resized, format="JPEG", quality=85)
    ph_resized = perceptual_hash(resized)
    sim = evaluate_similarity(ph1, ph_resized, threshold=10)
    assert sim["visually_similar"] is True
    assert hamming_distance(ph1, ph2) > sim["hamming_distance"]


def test_video_perceptual_fingerprint_not_single_frame():
    fp = compute_video_perceptual_fingerprint(["0000ffff0000ffff", "1111eeee1111eeee"])
    assert fp["mode"] == "sampled-frame-set"
    assert fp["frame_count"] == 2
    assert len(fp["frame_phashes"]) == 2
    assert fp["primary_reference"].startswith("vframes:")


def test_detector_missing_metadata_not_treated_as_manipulation(sample_image_pair, tmp_path: Path):
    p1, _ = sample_image_pair
    detector = HeuristicForensicDetector()
    res = detector.analyze_image(p1, "plain_photo.jpg")
    prov = res["indicators"]["provenance"]
    assert prov["metadata_status"] == "unavailable_evidence"
    assert res["verdict"] in {VERDICT_LOW, VERDICT_REVIEW, VERDICT_ELEVATED}
    assert res["is_calibrated_probability"] is False

    # Filename marker alone is weak contextual note, not automatic manipulation proof
    named = tmp_path / "ChatGPT_test_filename.jpg"
    named.write_bytes(p1.read_bytes())
    res_named = detector.analyze_image(named, named.name)
    assert res_named["indicators"]["provenance"]["weak_filename_marker_found"] is True
    assert res_named["ai_generated_provenance"] is False

    # Embedded PNG generator metadata IS flagged
    png_path = tmp_path / "gen_meta.png"
    with Image.open(p1) as img:
        meta = PngImagePlugin.PngInfo()
        meta.add_text("Software", "OpenAI DALL-E Renderer")
        img.save(png_path, format="PNG", pnginfo=meta)
    res_meta = detector.analyze_image(png_path, "gen_meta.png")
    assert res_meta["ai_generated_provenance"] is True
    assert res_meta["verdict"] == VERDICT_ELEVATED


def test_video_analysis_samples_frames(sample_video_file: Path):
    detector = HeuristicForensicDetector()
    res = detector.analyze_video(sample_video_file, sample_video_file.name)
    assert res["media_type"] == "video"
    assert 1 <= res["frames_analyzed"] <= 12
    assert len(res["frame_results"]) == res["frames_analyzed"]
    assert res["audio_analysis_performed"] is False
    assert "mean_frame_score" in res and "max_frame_score" in res


def test_invalid_file_signature_rejected(tmp_path: Path):
    fake_img = tmp_path / "not_an_image.jpg"
    fake_img.write_text("this is plain text pretending to be jpeg", encoding="utf-8")
    with pytest.raises(MediaValidationError) as exc:
        validate_media_file(fake_img, expected_kind="image", original_filename=fake_img.name)
    assert exc.value.status_code == 415


def test_ledger_canonical_integrity_corruption_and_concurrency(tmp_path: Path):
    ledger_path = tmp_path / "ledger.json"

    def _append_worker(idx: int):
        sha = f"{idx:064x}"
        return add_record(
            sha,
            "0000111122223333",
            {
                "detector_version": "heuristic-v2.0.0",
                "config_id": "cfg-2026.11-baseline",
                "heuristic_score": 0.2,
                "verdict": VERDICT_LOW,
                "indicators": {},
                "timestamp": "2026-11-25T10:00:00+00:00",
            },
            f"file_{idx}.jpg",
            file_size_bytes=1024,
            ledger_path=ledger_path,
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(_append_worker, range(8)))

    valid_res = verify_local_chain_integrity(ledger_path=ledger_path)
    assert valid_res["valid"] is True
    assert valid_res["length"] == 8

    # Re-analysis with new detector config allowed on same SHA-256
    same_sha = f"{0:064x}"
    add_record(
        same_sha,
        "0000111122223333",
        {
            "detector_version": "heuristic-v2.1.0",
            "config_id": "cfg-2026.12",
            "heuristic_score": 0.45,
            "verdict": VERDICT_REVIEW,
            "indicators": {},
            "timestamp": "2026-11-25T10:05:00+00:00",
        },
        "file_0.jpg",
        file_size_bytes=1024,
        ledger_path=ledger_path,
    )
    assert get_record_by_hash(same_sha, analysis_config_id="heuristic-v2.0.0:cfg-2026.11-baseline", ledger_path=ledger_path) is not None
    assert get_record_by_hash(same_sha, analysis_config_id="heuristic-v2.1.0:cfg-2026.12", ledger_path=ledger_path) is not None

    # External checkpoint detects rewrite even when local hashes are recalculated
    chain = json.loads(ledger_path.read_text(encoding="utf-8"))
    cp = {
        "checkpoint_id": 1,
        "record_count": len(chain),
        "ledger_root_hash": compute_ledger_root_hash(chain),
        "timestamp": "2026-11-25T10:06:00+00:00",
    }
    assert verify_against_checkpoint(chain, cp)["valid"] is True

    # Tamper field without rehash -> local check detects record_hash_mismatch
    tampered = json.loads(json.dumps(chain))
    tampered[2]["verdict"] = VERDICT_ELEVATED
    tampered_path = tmp_path / "tampered.json"
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
    t_res = verify_local_chain_integrity(ledger_path=tampered_path)
    assert t_res["valid"] is False
    assert t_res["error_type"] == "record_hash_mismatch"

    # Full rewrite with recalculated hashes -> local passes, checkpoint catches it!
    prev = "0" * 64
    for rec in tampered:
        rec["previous_hash"] = prev
        h = compute_record_hash_v2(rec)
        rec["record_hash"] = h
        rec["block_hash"] = h
        prev = h
    rewritten_path = tmp_path / "rewritten.json"
    rewritten_path.write_text(json.dumps(tampered), encoding="utf-8")
    assert verify_local_chain_integrity(ledger_path=rewritten_path)["valid"] is True
    cp_res = verify_against_checkpoint(tampered, cp)
    assert cp_res["valid"] is False
    assert cp_res["status"] == "rewrite_detected"


def test_pdf_certificate_generation(tmp_path: Path):
    rec = add_record(
        "a" * 64,
        "0123456789abcdef",
        {
            "detector_version": "heuristic-v2.0.0",
            "config_id": "cfg-2026.11-baseline",
            "heuristic_score": 0.21,
            "verdict": VERDICT_LOW,
            "manipulation_type": "Low indicators",
            "indicators": {},
            "timestamp": "2026-11-25T11:00:00+00:00",
        },
        "private_user_photo.jpg",
        file_size_bytes=2048,
        ledger_path=tmp_path / "cert_ledger.json",
    )
    cert = generate_certificate(rec)
    assert "private_user_photo" not in cert["filename"]  # Privacy sanitization
    pdf_bytes = generate_certificate_pdf(cert, on_chain_verified=True)
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1200


def test_dataset_manifest_leakage_detection(tmp_path: Path):
    f1 = tmp_path / "a.jpg"
    f2 = tmp_path / "a_resized.jpg"
    f1.write_bytes(b"123")
    f2.write_bytes(b"456")
    samples = [
        DatasetSample("S1", str(f1), "image", "cam", "ok", "genuine", "none", "orig", "GRP1", "dev"),
        DatasetSample("S2", str(f2), "image", "cam", "ok", "benign_transformed", "resize", "deriv", "GRP1", "test"),
    ]
    res = validate_manifest(samples)
    assert res["valid"] is False
    assert "GRP1" in res["leakage_groups"]


def test_rq2_evaluation_runs():
    res = evaluate_rq2()
    assert res["research_question"] == "RQ2"
    assert res["exact_sha256_consistency"]["identical_upload_match_rate"] == 1.0
    assert res["exact_sha256_consistency"]["one_byte_change_detection_rate"] == 1.0
