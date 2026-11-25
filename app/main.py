"""
main.py
-------
VERITY FastAPI Backend:
Blockchain-Based Verification System for Digital Content Authenticity and Deepfake Detection.

Separates three independent verification questions:
  1. Does the uploaded content show evidence of manipulation? (Forensic Detector)
  2. Does the file match a previously examined file? (SHA-256 & DCT Perceptual Hash)
  3. Has its verification record been modified? (Canonical Local Ledger & Solidity Smart Contract)
"""

from __future__ import annotations

from datetime import datetime
import html as html_lib
from pathlib import Path
import time
from typing import Any

from fastapi import FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.blockchain_client import VerityBlockchainClient
from app.certificate import generate_certificate, generate_certificate_pdf
from app.config import get_settings
from app.detector import get_active_detector
from app.hasher import find_similar_image_records, perceptual_hash, sha256_of_file
from app.ledger import (
    add_record,
    attach_blockchain_receipt,
    compute_ledger_root_hash,
    content_sha256_of,
    get_history,
    get_record_by_hash,
    get_record_by_verification_id,
    get_records_by_hash,
    record_hash_of,
    sequence_of,
    verification_id_of,
    verify_chain_integrity,
    verify_local_chain_integrity,
)
from app.security import (
    MediaValidationError,
    managed_temp_upload,
    sanitize_filename_for_public,
    validate_media_file,
)

app = FastAPI(
    title="VERITY — Blockchain-Based Digital Content Verification API",
    description=(
        "M.Sc. Computer Science Research Prototype (MIT-WPU Pune, 2026-27): "
        "Heuristic image/video forensics, SHA-256 & DCT perceptual hashing, "
        "canonical hash-chained audit ledger, and Ethereum/Ganache Solidity smart contract anchoring."
    ),
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class CheckpointRequest(BaseModel):
    note: str = "Manual VERITY ledger checkpoint"


def _get_blockchain_client() -> VerityBlockchainClient:
    return VerityBlockchainClient(get_settings())


def _require_admin_key_if_configured(x_admin_key: str | None) -> None:
    configured = get_settings().admin_api_key
    if configured and x_admin_key != configured:
        raise HTTPException(status_code=403, detail="Invalid or missing X-Admin-Key.")


def _anchor_record_on_blockchain(
    record: dict[str, Any],
    client: VerityBlockchainClient,
) -> dict[str, Any]:
    """Submit a canonical verification record to VerityVerification.sol and attach real receipt."""
    settings = get_settings()
    if not settings.auto_anchor:
        receipt = {
            "anchoring_status": "disabled",
            "tx_hash": None,
            "block_number": None,
            "contract_address": settings.contract_address or None,
            "chain_id": settings.chain_id,
            "network_name": settings.network_name,
            "submitter": None,
            "event_key": None,
            "error": "Auto-anchoring disabled via configuration.",
        }
        attach_blockchain_receipt(verification_id_of(record), receipt)
        return receipt

    fp = record.get("perceptual_fingerprints", {})
    phash_ref = str(
        fp.get("primary_phash")
        or fp.get("primary_reference")
        or record.get("perceptual_hash")
        or "unavailable"
    )
    try:
        tx_res = client.register_verification(
            content_sha256_hex=content_sha256_of(record),
            perceptual_fingerprint=phash_ref,
            verification_id=verification_id_of(record),
            media_type=str(record.get("media_type", "image")),
            result_category=str(record.get("verdict", "Inconclusive")),
            analysis_config_id=str(record.get("analysis_config_id")),
            record_hash_hex=record_hash_of(record),
            local_sequence=int(sequence_of(record) or 0),
        )
        if tx_res.get("anchoring_status") == "already_anchored":
            on_chain = tx_res["on_chain_record"]
            receipt = {
                "anchoring_status": "already_anchored",
                "tx_hash": None,
                "block_number": None,
                "contract_address": tx_res["contract_address"],
                "chain_id": tx_res["chain_id"],
                "network_name": tx_res["network_name"],
                "submitter": on_chain.get("submitter"),
                "event_key": on_chain.get("event_key"),
                "on_chain_timestamp": on_chain.get("on_chain_timestamp"),
                "error": None,
            }
        else:
            receipt = {
                "anchoring_status": "anchored",
                "tx_hash": tx_res["tx_hash"],
                "block_number": tx_res["block_number"],
                "block_hash": tx_res["block_hash"],
                "block_timestamp": tx_res["block_timestamp"],
                "gas_used": tx_res["gas_used"],
                "tx_status": tx_res["tx_status"],
                "contract_address": tx_res["contract_address"],
                "chain_id": tx_res["chain_id"],
                "network_name": tx_res["network_name"],
                "submitter": tx_res["submitter"],
                "event_key": tx_res["event_key"],
                "error": None,
            }
        attach_blockchain_receipt(verification_id_of(record), receipt)
        return receipt
    except Exception as exc:
        receipt = {
            "anchoring_status": "pending_blockchain_unavailable",
            "tx_hash": None,
            "block_number": None,
            "contract_address": settings.contract_address or None,
            "chain_id": settings.chain_id,
            "network_name": settings.network_name,
            "submitter": None,
            "event_key": None,
            "error": str(exc),
        }
        attach_blockchain_receipt(verification_id_of(record), receipt)
        return receipt


def _maybe_auto_checkpoint(client: VerityBlockchainClient) -> dict[str, Any] | None:
    settings = get_settings()
    if not settings.auto_anchor:
        return None
    chain = get_history()
    if not chain or len(chain) % settings.checkpoint_interval != 0:
        return None
    local_check = verify_local_chain_integrity()
    if not local_check.get("valid"):
        return None
    try:
        root_hash = compute_ledger_root_hash(chain)
        last_seq = int(sequence_of(chain[-1]) or (len(chain) - 1))
        return client.register_checkpoint(
            ledger_root_hash_hex=root_hash,
            record_count=len(chain),
            last_sequence_number=last_seq,
            note=f"Automatic periodic checkpoint at {len(chain)} records",
        )
    except Exception:
        return None


def _sanitize_history_item(record: dict[str, Any], expose_filenames: bool) -> dict[str, Any]:
    sha256_hex = content_sha256_of(record)
    media_type = record.get("media_type") or record.get("ai_result", {}).get("media_type", "image")
    safe_item = dict(record)
    safe_item["verification_id"] = verification_id_of(record)
    safe_item["content_sha256"] = sha256_hex
    safe_item["filename"] = sanitize_filename_for_public(
        record.get("filename"), sha256_hex, media_type, expose_filenames=expose_filenames
    )
    return safe_item


@app.post("/verify")
async def verify_content(
    file: UploadFile = File(...),
    detector_version: str | None = Query(
        default=None,
        description="Optional override detector version label to record re-analysis events.",
    ),
    config_id: str | None = Query(
        default=None,
        description="Optional override analysis configuration ID for re-analysis.",
    ),
):
    """
    Verify an uploaded image:
      1. Enforce upload size, file signature, and decoded pixel limits.
      2. Compute exact SHA-256 on raw bytes before decoding + 64-bit DCT perceptual hash.
      3. Check exact match (SHA-256 + analysis_config_id) and perceptual similarity separately.
      4. Run forensic heuristic analysis, write canonical ledger record, anchor on Solidity contract.
      5. Return verification certificate with genuine blockchain receipt & timing metrics.
    """
    t_total_start = time.perf_counter()
    settings = get_settings()
    detector = get_active_detector()
    if detector_version:
        detector.detector_version = detector_version.strip()
    if config_id:
        detector.config_id = config_id.strip()

    try:
        with managed_temp_upload(file, settings.max_image_bytes, ".img") as tmp_path:
            meta = validate_media_file(
                tmp_path,
                expected_kind="image",
                original_filename=file.filename,
                settings=settings,
            )

            t_hash_start = time.perf_counter()
            content_hash = sha256_of_file(tmp_path)
            phash = perceptual_hash(tmp_path)
            hashing_ms = round((time.perf_counter() - t_hash_start) * 1000.0, 2)

            history_records = get_history()
            similarity_matches = find_similar_image_records(
                phash,
                history_records,
                exact_sha256=content_hash,
                threshold=settings.phash_threshold,
            )

            existing_exact_same_cfg = get_record_by_hash(
                content_hash, analysis_config_id=detector.analysis_config_id
            )
            client = _get_blockchain_client()

            if existing_exact_same_cfg:
                try:
                    on_chain_existing = client.get_verification_event(
                        content_hash, detector.analysis_config_id
                    )
                except Exception:
                    on_chain_existing = None

                certificate = generate_certificate(
                    existing_exact_same_cfg,
                    hash_status="exact match (already verified with current analysis config)",
                    similarity_matches=similarity_matches,
                )
                certificate["reanalysis_count"] = len(get_records_by_hash(content_hash))
                certificate["on_chain_verification"] = on_chain_existing
                certificate["timings_ms"] = {
                    "hashing_ms": hashing_ms,
                    "total_ms": round((time.perf_counter() - t_total_start) * 1000.0, 2),
                }
                return certificate

            prior_records = get_records_by_hash(content_hash)
            ai_result = detector.analyze_image(tmp_path, file.filename)

            t_ledger_start = time.perf_counter()
            fp_obj = {
                "algorithm": "dct-64-median-ac-v1",
                "mode": "image",
                "primary_phash": phash,
            }
            record = add_record(
                content_hash,
                fp_obj,
                ai_result,
                file.filename or "uploaded-image",
                file_size_bytes=meta["file_size_bytes"],
                media_type="image",
            )
            ledger_write_ms = round((time.perf_counter() - t_ledger_start) * 1000.0, 2)

            t_bc_start = time.perf_counter()
            bc_receipt = _anchor_record_on_blockchain(record, client)
            record["blockchain"] = bc_receipt
            checkpoint_info = _maybe_auto_checkpoint(client)
            blockchain_tx_ms = round((time.perf_counter() - t_bc_start) * 1000.0, 2)

            hash_status = (
                "re-analysis (same file, new analysis configuration)"
                if prior_records
                else "new registration"
            )
            certificate = generate_certificate(
                record,
                hash_status=hash_status,
                similarity_matches=similarity_matches,
            )
            certificate["reanalysis_count"] = len(prior_records) + 1
            certificate["auto_checkpoint"] = checkpoint_info
            certificate["timings_ms"] = {
                "hashing_ms": hashing_ms,
                "decode_ms": ai_result.get("decode_time_ms"),
                "analysis_ms": ai_result.get("analysis_time_ms"),
                "ledger_write_ms": ledger_write_ms,
                "blockchain_tx_ms": blockchain_tx_ms,
                "total_ms": round((time.perf_counter() - t_total_start) * 1000.0, 2),
            }
            return certificate
    except MediaValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.post("/verify-video")
async def verify_video(
    file: UploadFile = File(...),
    detector_version: str | None = Query(default=None),
    config_id: str | None = Query(default=None),
):
    """Verify a video by sampling up to 12 equidistant frames and anchoring file SHA-256."""
    t_total_start = time.perf_counter()
    settings = get_settings()
    detector = get_active_detector()
    if detector_version:
        detector.detector_version = detector_version.strip()
    if config_id:
        detector.config_id = config_id.strip()

    try:
        with managed_temp_upload(file, settings.max_video_bytes, ".mp4") as tmp_path:
            meta = validate_media_file(
                tmp_path,
                expected_kind="video",
                original_filename=file.filename,
                settings=settings,
            )

            t_hash_start = time.perf_counter()
            content_hash = sha256_of_file(tmp_path)
            hashing_ms = round((time.perf_counter() - t_hash_start) * 1000.0, 2)

            existing_exact_same_cfg = get_record_by_hash(
                content_hash, analysis_config_id=detector.analysis_config_id
            )
            client = _get_blockchain_client()

            if existing_exact_same_cfg:
                try:
                    on_chain_existing = client.get_verification_event(
                        content_hash, detector.analysis_config_id
                    )
                except Exception:
                    on_chain_existing = None
                certificate = generate_certificate(
                    existing_exact_same_cfg,
                    hash_status="exact match (already verified with current analysis config)",
                )
                certificate["reanalysis_count"] = len(get_records_by_hash(content_hash))
                certificate["on_chain_verification"] = on_chain_existing
                certificate["timings_ms"] = {
                    "hashing_ms": hashing_ms,
                    "total_ms": round((time.perf_counter() - t_total_start) * 1000.0, 2),
                }
                return certificate

            prior_records = get_records_by_hash(content_hash)
            ai_result = detector.analyze_video(tmp_path, file.filename)

            t_ledger_start = time.perf_counter()
            fp_obj = ai_result["video_perceptual_fingerprint"]
            record = add_record(
                content_hash,
                fp_obj,
                ai_result,
                file.filename or "uploaded-video",
                file_size_bytes=meta["file_size_bytes"],
                media_type="video",
            )
            ledger_write_ms = round((time.perf_counter() - t_ledger_start) * 1000.0, 2)

            t_bc_start = time.perf_counter()
            bc_receipt = _anchor_record_on_blockchain(record, client)
            record["blockchain"] = bc_receipt
            checkpoint_info = _maybe_auto_checkpoint(client)
            blockchain_tx_ms = round((time.perf_counter() - t_bc_start) * 1000.0, 2)

            hash_status = (
                "re-analysis (same file, new analysis configuration)"
                if prior_records
                else "new registration"
            )
            certificate = generate_certificate(record, hash_status=hash_status)
            certificate["reanalysis_count"] = len(prior_records) + 1
            certificate["auto_checkpoint"] = checkpoint_info
            certificate["timings_ms"] = {
                "hashing_ms": hashing_ms,
                "analysis_ms": ai_result.get("analysis_time_ms"),
                "ledger_write_ms": ledger_write_ms,
                "blockchain_tx_ms": blockchain_tx_ms,
                "total_ms": round((time.perf_counter() - t_total_start) * 1000.0, 2),
            }
            return certificate
    except MediaValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.get("/check/{content_hash}")
async def check_hash(content_hash: str):
    """Look up a SHA-256 hash across the local ledger and smart contract."""
    cleaned = content_hash.strip().lower()
    if len(cleaned) != 64 or not all(ch in "0123456789abcdef" for ch in cleaned):
        raise HTTPException(status_code=400, detail="content_hash must be a 64-character SHA-256 hex string.")

    records = get_records_by_hash(cleaned)
    client = _get_blockchain_client()
    on_chain_records = []
    try:
        on_chain_records = client.get_records_by_content_hash(cleaned)
    except Exception:
        on_chain_records = []

    if not records and not on_chain_records:
        raise HTTPException(status_code=404, detail="No verification record found for this SHA-256 hash.")

    if records:
        cert = generate_certificate(records[-1], hash_status="exact SHA-256 match found")
        cert["reanalysis_history"] = [
            {
                "verification_id": verification_id_of(r),
                "sequence_number": sequence_of(r),
                "analysis_config_id": r.get("analysis_config_id", "legacy-v1"),
                "verdict": r.get("verdict") or r.get("ai_result", {}).get("authenticity_flag"),
                "heuristic_score": r.get("heuristic_score")
                if r.get("heuristic_score") is not None
                else r.get("ai_result", {}).get("deepfake_probability"),
                "recorded_at": r.get("server_timestamp") or r.get("recorded_at"),
                "tx_hash": (r.get("blockchain") or {}).get("tx_hash"),
            }
            for r in records
        ]
        cert["on_chain_records"] = on_chain_records
        return cert

    return {
        "content_hash_sha256": cleaned,
        "hash_status": "found on blockchain (not present in local ledger)",
        "on_chain_records": on_chain_records,
    }


@app.get("/history")
async def history(
    q: str | None = Query(default=None, description="Search by SHA-256 prefix or verification ID"),
    media_type: str | None = Query(default=None, description="Filter by image or video"),
    verdict: str | None = Query(default=None, description="Filter by verdict label"),
    date_from: str | None = Query(default=None, description="ISO date YYYY-MM-DD lower bound"),
    date_to: str | None = Query(default=None, description="ISO date YYYY-MM-DD upper bound"),
    x_admin_key: str | None = Header(default=None),
):
    """Return verification history with privacy controls and search/filter support."""
    settings = get_settings()
    include_raw_names = settings.expose_filenames or (
        bool(settings.admin_api_key) and x_admin_key == settings.admin_api_key
    )

    records = get_history()
    filtered: list[dict[str, Any]] = []
    q_lower = q.strip().lower() if q else None
    verdict_lower = verdict.strip().lower() if verdict else None
    media_lower = media_type.strip().lower() if media_type else None

    for rec in records:
        rec_sha = content_sha256_of(rec).lower()
        ver_id = verification_id_of(rec).lower()
        rec_media = str(
            rec.get("media_type") or rec.get("ai_result", {}).get("media_type", "image")
        ).lower()
        rec_verdict = str(
            rec.get("verdict") or rec.get("ai_result", {}).get("authenticity_flag", "")
        ).lower()
        rec_ts = str(rec.get("server_timestamp") or rec.get("recorded_at") or "")

        if q_lower and q_lower not in rec_sha and q_lower not in ver_id:
            continue
        if media_lower and rec_media != media_lower:
            continue
        if verdict_lower and rec_verdict != verdict_lower:
            continue
        if date_from and rec_ts[:10] < date_from:
            continue
        if date_to and rec_ts[:10] > date_to:
            continue

        filtered.append(_sanitize_history_item(rec, include_raw_names))

    return filtered


@app.get("/stats")
async def dashboard_stats():
    """Compute genuine dashboard statistics from the local ledger and live smart contract."""
    records = get_history()
    unique_hashes = {content_sha256_of(r) for r in records if content_sha256_of(r)}
    images_count = 0
    videos_count = 0
    low_count = 0
    review_count = 0
    elevated_count = 0
    inconclusive_count = 0
    anchored_local = 0
    pending_local = 0

    for r in records:
        m = str(r.get("media_type") or r.get("ai_result", {}).get("media_type", "image")).lower()
        if m == "video":
            videos_count += 1
        else:
            images_count += 1

        v = str(r.get("verdict") or r.get("ai_result", {}).get("authenticity_flag", ""))
        if v in {"Low Evidence", "Authentic"}:
            low_count += 1
        elif v in {"Review Required", "Potentially Manipulated"}:
            review_count += 1
        elif v in {"Elevated Evidence of Manipulation", "Manipulated", "AI-Generated"}:
            elevated_count += 1
        else:
            inconclusive_count += 1

        bc_status = (r.get("blockchain") or {}).get("anchoring_status")
        if bc_status in {"anchored", "already_anchored"}:
            anchored_local += 1
        else:
            pending_local += 1

    client = _get_blockchain_client()
    bc_status_info = client.get_status()
    latest_checkpoint = None
    if bc_status_info.get("connected") and bc_status_info.get("contract_deployed"):
        try:
            latest_checkpoint = client.get_latest_checkpoint()
        except Exception:
            latest_checkpoint = None

    return {
        "total_verification_events": len(records),
        "unique_content_files": len(unique_hashes),
        "images_analyzed": images_count,
        "videos_analyzed": videos_count,
        "verdict_counts": {
            "low_evidence": low_count,
            "review_required": review_count,
            "elevated_evidence": elevated_count,
            "inconclusive": inconclusive_count,
        },
        "local_anchored_events": anchored_local,
        "pending_blockchain_anchors": pending_local,
        "blockchain": bc_status_info,
        "latest_checkpoint": latest_checkpoint,
        "detector": {
            "version": get_settings().detector_version,
            "config_id": get_settings().config_id,
        },
    }


@app.get("/ledger/integrity")
async def ledger_integrity():
    """Validate local hash chain, on-chain records, and latest blockchain checkpoint separately."""
    return verify_chain_integrity(blockchain_client=_get_blockchain_client())


@app.get("/blockchain/status")
async def blockchain_status():
    """Return live Ethereum/Ganache node and smart contract status."""
    return _get_blockchain_client().get_status()


@app.get("/blockchain/contract-info")
async def blockchain_contract_info():
    """Return deployed VerityVerification.sol metadata and on-chain counters."""
    try:
        return _get_blockchain_client().get_contract_info()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/blockchain/record/{content_hash}")
async def blockchain_record(content_hash: str):
    """Retrieve all on-chain verification events linked to a content SHA-256 hash."""
    cleaned = content_hash.strip().lower()
    if len(cleaned) != 64 or not all(ch in "0123456789abcdef" for ch in cleaned):
        raise HTTPException(status_code=400, detail="Invalid 64-character SHA-256 hex digest.")
    try:
        records = _get_blockchain_client().get_records_by_content_hash(cleaned)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if not records:
        raise HTTPException(status_code=404, detail="No on-chain verification events found for this content hash.")
    return {"content_sha256": cleaned, "count": len(records), "records": records}


@app.get("/blockchain/records")
async def blockchain_records(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Enumerate on-chain verification records stored in VerityVerification.sol."""
    try:
        client = _get_blockchain_client()
        items = client.get_all_on_chain_records(offset=offset, limit=limit)
        status = client.get_status()
        return {
            "total_on_chain": status.get("total_on_chain_records", len(items)),
            "offset": offset,
            "limit": limit,
            "records": items,
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/blockchain/transaction/{tx_hash}")
async def blockchain_transaction(tx_hash: str):
    """Retrieve genuine EVM transaction receipt and decoded contract events."""
    try:
        tx = _get_blockchain_client().get_transaction_details(tx_hash)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found on the connected EVM node.")
    return tx


@app.get("/blockchain/checkpoints")
async def blockchain_checkpoints():
    """Retrieve all on-chain ledger checkpoints."""
    try:
        checkpoints = _get_blockchain_client().get_checkpoints()
        return {"count": len(checkpoints), "checkpoints": checkpoints}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/blockchain/checkpoint")
async def create_blockchain_checkpoint(
    payload: CheckpointRequest | None = None,
    x_admin_key: str | None = Header(default=None),
):
    """Anchor a new ledger root hash checkpoint to VerityVerification.sol."""
    _require_admin_key_if_configured(x_admin_key)
    local_status = verify_local_chain_integrity()
    if not local_status.get("valid"):
        raise HTTPException(
            status_code=409,
            detail=f"Refusing to checkpoint corrupted local ledger: {local_status.get('reason')}",
        )

    chain = get_history()
    if not chain:
        raise HTTPException(status_code=400, detail="Cannot anchor a checkpoint for an empty ledger.")

    root_hash = compute_ledger_root_hash(chain)
    last_seq = int(sequence_of(chain[-1]) or (len(chain) - 1))
    note = payload.note if payload else "Manual VERITY ledger checkpoint"
    try:
        return _get_blockchain_client().register_checkpoint(
            ledger_root_hash_hex=root_hash,
            record_count=len(chain),
            last_sequence_number=last_seq,
            note=note,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/certificate/{verification_id}")
async def download_certificate(verification_id: str):
    """Generate and download a PDF verification certificate with embedded QR code."""
    record = get_record_by_verification_id(verification_id)
    if not record:
        raise HTTPException(status_code=404, detail="Verification record not found.")

    cert = generate_certificate(record)
    on_chain_verified: bool | None = None
    try:
        res = _get_blockchain_client().verify_record_hash_on_chain(record_hash_of(record))
        on_chain_verified = bool(res.get("exists_on_chain"))
    except Exception:
        on_chain_verified = None

    pdf_bytes = generate_certificate_pdf(cert, on_chain_verified=on_chain_verified)
    filename = f"verity-certificate-{verification_id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/verify-certificate/{verification_id}")
async def verify_certificate(
    verification_id: str,
    request: Request,
    format: str | None = Query(default=None),
):
    """
    Independent verification endpoint resolved by certificate QR codes.
    Verifies both local ledger record and smart-contract on-chain state.
    """
    local_record = get_record_by_verification_id(verification_id)
    local_integrity = verify_local_chain_integrity()
    client = _get_blockchain_client()

    on_chain_record = None
    record_hash_check = None
    bc_error = None
    try:
        on_chain_record = client.get_verification_by_id(verification_id)
        if local_record:
            record_hash_check = client.verify_record_hash_on_chain(
                record_hash_of(local_record)
            )
        elif on_chain_record:
            record_hash_check = client.verify_record_hash_on_chain(
                on_chain_record["record_hash"]
            )
    except Exception as exc:
        bc_error = str(exc)

    if not local_record and not on_chain_record:
        raise HTTPException(status_code=404, detail="Verification ID not found in local ledger or blockchain.")

    cert = generate_certificate(local_record) if local_record else None
    hashes_match = bool(
        local_record
        and on_chain_record
        and record_hash_of(local_record).lower() == on_chain_record["record_hash"].lower()
        and content_sha256_of(local_record).lower() == on_chain_record["content_sha256"].lower()
    )

    payload = {
        "verification_id": verification_id,
        "verified": bool(
            (local_record is None or local_integrity.get("valid"))
            and (on_chain_record is not None)
            and (local_record is None or hashes_match)
        ),
        "local_record_found": local_record is not None,
        "local_chain_valid": bool(local_integrity.get("valid")),
        "on_chain_record_found": on_chain_record is not None,
        "local_matches_on_chain": hashes_match if (local_record and on_chain_record) else None,
        "record_hash_check": record_hash_check,
        "on_chain_record": on_chain_record,
        "certificate": cert,
        "blockchain_error": bc_error,
        "interpretation": (
            "A verified record proves that the recorded SHA-256 digest and analysis metadata "
            "match the anchored verification event; it does not prove absolute authenticity of the original scene."
        ),
    }

    accept = request.headers.get("accept", "")
    if format == "html" or (format != "json" and "text/html" in accept and "application/json" not in accept):
        status_label = "VERIFIED ON-CHAIN" if payload["verified"] else "LOCAL / PARTIAL RECORD"
        sha_display = (cert or {}).get("content_hash_sha256") or (on_chain_record or {}).get("content_sha256", "N/A")
        verdict_display = (cert or {}).get("verdict") or (on_chain_record or {}).get("result_category", "N/A")
        html_doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VERITY Certificate Verification | {html_lib.escape(verification_id)}</title>
<style>body{{font-family:system-ui,sans-serif;background:#070d1a;color:#e2e8f0;margin:0;padding:32px}}
.card{{max-width:760px;margin:0 auto;background:#0f192e;border:1px solid #1e3256;border-radius:14px;padding:28px}}
.badge{{display:inline-block;padding:6px 12px;border-radius:999px;font-weight:700;font-size:12px;background:#102a43;color:#60a5fa}}
code{{font-family:ui-monospace,monospace;word-break:break-all;color:#93c5fd}}
.row{{padding:10px 0;border-bottom:1px solid #1a2a47}}.label{{font-size:11px;text-transform:uppercase;color:#94a3b8}}</style>
</head><body><div class="card"><span class="badge">{html_lib.escape(status_label)}</span>
<h1>VERITY Certificate Verification</h1>
<div class="row"><div class="label">Verification ID</div><code>{html_lib.escape(verification_id)}</code></div>
<div class="row"><div class="label">Content SHA-256</div><code>{html_lib.escape(str(sha_display))}</code></div>
<div class="row"><div class="label">Analysis Verdict</div><div>{html_lib.escape(str(verdict_display))}</div></div>
<div class="row"><div class="label">Local Chain Integrity</div><div>{html_lib.escape(str(payload['local_chain_valid']))}</div></div>
<div class="row"><div class="label">On-Chain Record Found</div><div>{html_lib.escape(str(payload['on_chain_record_found']))}</div></div>
<p style="color:#94a3b8;font-size:13px;margin-top:18px">{html_lib.escape(payload['interpretation'])}</p>
<p><a href="/certificate/{html_lib.escape(verification_id)}" style="color:#60a5fa">Download PDF Certificate</a></p>
</div></body></html>"""
        return HTMLResponse(content=html_doc)

    return payload


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "VERITY",
        "detector_version": get_settings().detector_version,
        "config_id": get_settings().config_id,
    }


frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
