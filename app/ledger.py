"""
ledger.py
---------
Local hash-chained JSON audit ledger for VERITY with:
  - Deterministic canonical serialization (fixed field order, UTF-8, normalized floats)
  - Single-writer thread + cross-process file lock
  - Atomic file writes via temporary file + fsync + os.replace
  - Backward compatibility with existing v1 legacy blocks (indices 0..19)
  - Separate local hash-chain integrity checks and external blockchain
    checkpoint validation (detecting truncation and full-chain rewrites).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Iterator
import uuid

from app.config import get_settings

GENESIS_HASH = "0" * 64
SCHEMA_VERSION_V2 = "2.0-canonical"

CANONICAL_FIELDS_V2 = (
    "schema_version",
    "verification_id",
    "sequence_number",
    "content_sha256",
    "media_type",
    "file_size_bytes",
    "perceptual_fingerprints",
    "analysis_indicators",
    "heuristic_score",
    "verdict",
    "detector_version",
    "configuration_id",
    "server_timestamp",
    "previous_hash",
)

_THREAD_LOCK = threading.RLock()


@contextmanager
def _ledger_write_lock(ledger_path: Path) -> Iterator[None]:
    """Acquire both in-process thread lock and cross-process advisory file lock."""
    with _THREAD_LOCK:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = ledger_path.with_suffix(ledger_path.suffix + ".lock")
        lock_fh = open(lock_path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                lock_fh.seek(0)
                msvcrt.locking(lock_fh.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                if os.name == "nt":
                    import msvcrt

                    lock_fh.seek(0)
                    msvcrt.locking(lock_fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            lock_fh.close()


def _normalize_canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError("Non-finite float value is not permitted in canonical ledger record.")
        return round(value, 6)
    if isinstance(value, dict):
        return {str(k): _normalize_canonical_value(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_normalize_canonical_value(item) for item in value]
    return str(value)


def canonical_serialize_v2(record: dict[str, Any]) -> bytes:
    """
    Canonical UTF-8 serialization C_i of a v2 verification record excluding record_hash
    and post-anchoring blockchain receipt metadata.
    """
    ordered: dict[str, Any] = {}
    for key in CANONICAL_FIELDS_V2:
        ordered[key] = _normalize_canonical_value(record.get(key))
    return json.dumps(ordered, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def compute_record_hash_v2(record: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_serialize_v2(record)).hexdigest()


def _legacy_v1_hash(block: dict[str, Any]) -> str:
    block_copy = {k: v for k, v in block.items() if k != "block_hash"}
    encoded = json.dumps(block_copy, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def is_v2_record(record: dict[str, Any]) -> bool:
    return record.get("schema_version") == SCHEMA_VERSION_V2


def record_hash_of(record: dict[str, Any]) -> str:
    return str(record.get("record_hash") or record.get("block_hash") or "")


def previous_hash_of(record: dict[str, Any]) -> str:
    return str(record.get("previous_hash") or "")


def sequence_of(record: dict[str, Any]) -> int | None:
    if "sequence_number" in record:
        return record["sequence_number"]
    return record.get("index")


def content_sha256_of(record: dict[str, Any]) -> str:
    return str(record.get("content_sha256") or record.get("content_hash") or "")


def verification_id_of(record: dict[str, Any]) -> str:
    if record.get("verification_id"):
        return str(record["verification_id"])
    return record_hash_of(record)[:16]


def compute_ledger_root_hash(records: list[dict[str, Any]]) -> str:
    """
    Compute deterministic root digest over the ordered sequence of record hashes
    for external blockchain checkpoint anchoring.
    """
    parts = [f"VERITY-LEDGER-ROOT-V1|{len(records)}"]
    for idx, rec in enumerate(records):
        seq = sequence_of(rec)
        parts.append(f"{idx}:{seq}:{record_hash_of(rec)}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _resolve_ledger_path(ledger_path: Path | str | None = None) -> Path:
    if ledger_path is not None:
        return Path(ledger_path)
    return get_settings().ledger_path


def _load_chain_raw(ledger_path: Path) -> list[dict[str, Any]]:
    if not ledger_path.exists():
        return []
    text = ledger_path.read_text(encoding="utf-8")
    if not text.strip():
        return []
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("Ledger JSON root must be a list of verification records.")
    return data


def _save_chain_atomic(ledger_path: Path, chain: list[dict[str, Any]]) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(ledger_path.parent), prefix=".ledger_", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(chain, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, ledger_path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def build_canonical_record(
    *,
    sequence_number: int,
    previous_hash: str,
    content_sha256: str,
    media_type: str,
    file_size_bytes: int,
    perceptual_fingerprints: dict[str, Any],
    analysis_result: dict[str, Any],
    filename: str,
    verification_id: str | None = None,
) -> dict[str, Any]:
    """Construct a canonical v2 verification record with deterministic record_hash."""
    ver_id = verification_id or f"VRT-{sequence_number:04d}-{uuid.uuid4().hex[:8].upper()}"
    server_ts = analysis_result.get("timestamp") or datetime.now(timezone.utc).isoformat()
    detector_version = str(analysis_result.get("detector_version", "heuristic-v2.0.0"))
    configuration_id = str(analysis_result.get("config_id", "cfg-2026.11-baseline"))
    score = analysis_result.get("heuristic_score")
    if score is None:
        score = analysis_result.get("deepfake_probability")
    verdict = str(
        analysis_result.get("verdict")
        or analysis_result.get("authenticity_flag")
        or "Inconclusive"
    )

    indicators = analysis_result.get("indicators", {})
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION_V2,
        "verification_id": ver_id,
        "sequence_number": int(sequence_number),
        "index": int(sequence_number),
        "filename": filename,
        "content_sha256": content_sha256.lower(),
        "content_hash": content_sha256.lower(),
        "media_type": media_type,
        "file_size_bytes": int(file_size_bytes),
        "perceptual_fingerprints": perceptual_fingerprints,
        "perceptual_hash": str(
            perceptual_fingerprints.get("primary_phash")
            or perceptual_fingerprints.get("primary_reference")
            or ""
        ),
        "analysis_indicators": indicators,
        "heuristic_score": round(float(score), 6) if score is not None else None,
        "verdict": verdict,
        "detector_version": detector_version,
        "configuration_id": configuration_id,
        "analysis_config_id": f"{detector_version}:{configuration_id}",
        "server_timestamp": server_ts,
        "recorded_at": server_ts,
        "previous_hash": previous_hash,
        "ai_result": analysis_result,
    }
    record["record_hash"] = compute_record_hash_v2(record)
    record["block_hash"] = record["record_hash"]
    record["blockchain"] = {
        "anchoring_status": "pending",
        "tx_hash": None,
        "block_number": None,
        "contract_address": None,
        "chain_id": None,
        "network_name": None,
        "submitter": None,
        "event_key": None,
    }
    return record


def add_record(
    content_hash: str,
    perceptual_fingerprint: str | dict[str, Any],
    ai_result: dict[str, Any],
    filename: str,
    *,
    file_size_bytes: int = 0,
    media_type: str | None = None,
    verification_id: str | None = None,
    ledger_path: Path | str | None = None,
) -> dict[str, Any]:
    """Append a canonical verification record safely under a single-writer lock."""
    path = _resolve_ledger_path(ledger_path)
    resolved_media = media_type or str(ai_result.get("media_type", "image"))
    if isinstance(perceptual_fingerprint, str):
        fp_dict = {
            "algorithm": "dct-64-median-ac-v1",
            "mode": "image",
            "primary_phash": perceptual_fingerprint,
        }
    else:
        fp_dict = perceptual_fingerprint

    with _ledger_write_lock(path):
        chain = _load_chain_raw(path)
        prev_hash = record_hash_of(chain[-1]) if chain else GENESIS_HASH
        seq = len(chain)
        record = build_canonical_record(
            sequence_number=seq,
            previous_hash=prev_hash,
            content_sha256=content_hash,
            media_type=resolved_media,
            file_size_bytes=file_size_bytes,
            perceptual_fingerprints=fp_dict,
            analysis_result=ai_result,
            filename=filename,
            verification_id=verification_id,
        )
        chain.append(record)
        _save_chain_atomic(path, chain)
        return record


def attach_blockchain_receipt(
    verification_id: str,
    blockchain_meta: dict[str, Any],
    *,
    ledger_path: Path | str | None = None,
) -> dict[str, Any] | None:
    """
    Update post-anchoring blockchain receipt metadata on an existing record
    without altering its canonical record_hash.
    """
    path = _resolve_ledger_path(ledger_path)
    with _ledger_write_lock(path):
        chain = _load_chain_raw(path)
        for rec in chain:
            if verification_id_of(rec) == verification_id:
                rec["blockchain"] = blockchain_meta
                _save_chain_atomic(path, chain)
                return rec
    return None


def get_record_by_hash(
    content_hash: str,
    *,
    analysis_config_id: str | None = None,
    ledger_path: Path | str | None = None,
) -> dict[str, Any] | None:
    """
    Retrieve the latest verification record for a SHA-256 content hash.
    If analysis_config_id is given, match that specific detector/config version.
    """
    target = content_hash.strip().lower()
    chain = get_history(ledger_path=ledger_path)
    matches = [r for r in chain if content_sha256_of(r).lower() == target]
    if not matches:
        return None
    if analysis_config_id:
        for rec in reversed(matches):
            rec_cfg = rec.get("analysis_config_id")
            if rec_cfg == analysis_config_id:
                return rec
        return None
    return matches[-1]


def get_records_by_hash(
    content_hash: str, *, ledger_path: Path | str | None = None
) -> list[dict[str, Any]]:
    target = content_hash.strip().lower()
    return [r for r in get_history(ledger_path=ledger_path) if content_sha256_of(r).lower() == target]


def get_record_by_verification_id(
    verification_id: str, *, ledger_path: Path | str | None = None
) -> dict[str, Any] | None:
    target = verification_id.strip()
    for rec in get_history(ledger_path=ledger_path):
        if verification_id_of(rec) == target:
            return rec
    return None


def get_history(*, ledger_path: Path | str | None = None) -> list[dict[str, Any]]:
    path = _resolve_ledger_path(ledger_path)
    return _load_chain_raw(path)


def verify_local_chain_integrity(
    *, ledger_path: Path | str | None = None
) -> dict[str, Any]:
    """
    Validate local hash-chain integrity, distinguishing:
      - malformed_storage
      - sequence_error
      - record_hash_mismatch
      - previous_hash_mismatch
    """
    path = _resolve_ledger_path(ledger_path)
    try:
        chain = _load_chain_raw(path)
    except Exception as exc:
        return {
            "valid": False,
            "error_type": "malformed_storage",
            "reason": f"Malformed ledger storage: {exc}",
            "length": 0,
            "broken_at_index": 0,
        }

    if not chain:
        return {
            "valid": True,
            "length": 0,
            "latest_record_hash": None,
            "ledger_root_hash": compute_ledger_root_hash([]),
            "note": "Ledger is empty.",
        }

    for i, rec in enumerate(chain):
        if not isinstance(rec, dict):
            return {
                "valid": False,
                "error_type": "malformed_storage",
                "broken_at_index": i,
                "reason": "Ledger entry is not a JSON object.",
                "length": len(chain),
            }

        seq = sequence_of(rec)
        if seq != i:
            return {
                "valid": False,
                "error_type": "sequence_error",
                "broken_at_index": i,
                "expected_sequence": i,
                "actual_sequence": seq,
                "reason": f"Sequence number mismatch at position {i}: expected {i}, got {seq}.",
                "length": len(chain),
            }

        stored_hash = record_hash_of(rec)
        if is_v2_record(rec):
            recomputed = compute_record_hash_v2(rec)
        else:
            recomputed = _legacy_v1_hash(rec)

        if not stored_hash or recomputed != stored_hash:
            return {
                "valid": False,
                "error_type": "record_hash_mismatch",
                "broken_at_index": i,
                "expected_hash": recomputed,
                "stored_hash": stored_hash,
                "reason": f"Record hash mismatch at index {i} (record fields were altered).",
                "length": len(chain),
            }

        expected_prev = GENESIS_HASH if i == 0 else record_hash_of(chain[i - 1])
        actual_prev = previous_hash_of(rec)
        if actual_prev != expected_prev:
            return {
                "valid": False,
                "error_type": "previous_hash_mismatch",
                "broken_at_index": i,
                "expected_previous_hash": expected_prev,
                "actual_previous_hash": actual_prev,
                "reason": f"Previous-hash link broken at index {i}.",
                "length": len(chain),
            }

    return {
        "valid": True,
        "length": len(chain),
        "latest_sequence": len(chain) - 1,
        "latest_record_hash": record_hash_of(chain[-1]),
        "ledger_root_hash": compute_ledger_root_hash(chain),
        "legacy_v1_records": sum(1 for r in chain if not is_v2_record(r)),
        "canonical_v2_records": sum(1 for r in chain if is_v2_record(r)),
    }


def verify_against_checkpoint(
    chain: list[dict[str, Any]], checkpoint: dict[str, Any] | None
) -> dict[str, Any]:
    """
    Validate the local ledger against an externally anchored smart-contract checkpoint.
    Detects truncation and complete history rewrites that local hash-chaining alone
    cannot detect.
    """
    if checkpoint is None:
        return {
            "status": "unanchored",
            "valid": None,
            "checkpoint_present": False,
            "reason": "No external blockchain checkpoint has been anchored yet.",
        }

    cp_count = int(checkpoint["record_count"])
    cp_root = str(checkpoint["ledger_root_hash"]).lower()
    cp_id = int(checkpoint["checkpoint_id"])

    if len(chain) < cp_count:
        return {
            "status": "truncation_detected",
            "valid": False,
            "checkpoint_present": True,
            "checkpoint_id": cp_id,
            "anchored_record_count": cp_count,
            "local_record_count": len(chain),
            "anchored_root_hash": cp_root,
            "reason": (
                f"Local ledger has {len(chain)} records, fewer than anchored checkpoint #{cp_id} "
                f"({cp_count} records) — ledger truncation or deletion detected."
            ),
        }

    prefix_root = compute_ledger_root_hash(chain[:cp_count])
    if prefix_root != cp_root:
        return {
            "status": "rewrite_detected",
            "valid": False,
            "checkpoint_present": True,
            "checkpoint_id": cp_id,
            "anchored_record_count": cp_count,
            "local_record_count": len(chain),
            "anchored_root_hash": cp_root,
            "computed_prefix_root_hash": prefix_root,
            "reason": (
                f"Local ledger prefix [0..{cp_count - 1}] root hash does not match anchored "
                f"blockchain checkpoint #{cp_id} — complete ledger rewrite or historical tampering detected."
            ),
        }

    unanchored_tail = len(chain) - cp_count
    return {
        "status": "verified",
        "valid": True,
        "checkpoint_present": True,
        "checkpoint_id": cp_id,
        "anchored_record_count": cp_count,
        "local_record_count": len(chain),
        "unanchored_suffix_count": unanchored_tail,
        "anchored_root_hash": cp_root,
        "computed_prefix_root_hash": prefix_root,
        "checkpoint_timestamp": checkpoint.get("timestamp"),
        "reason": (
            f"Anchored prefix ({cp_count} records) matches smart-contract checkpoint #{cp_id}."
            + (
                f" {unanchored_tail} newer local record(s) are not yet covered by a checkpoint."
                if unanchored_tail > 0
                else " Entire local ledger is covered by the checkpoint."
            )
        ),
    }


def verify_chain_integrity(
    *,
    ledger_path: Path | str | None = None,
    blockchain_client: Any = None,
) -> dict[str, Any]:
    """
    Combined integrity report keeping local hash-chain checks, individual
    blockchain record checks, and external checkpoint checks strictly separate.
    """
    path = _resolve_ledger_path(ledger_path)
    local_report = verify_local_chain_integrity(ledger_path=path)

    chain: list[dict[str, Any]] = []
    if local_report.get("error_type") != "malformed_storage":
        try:
            chain = _load_chain_raw(path)
        except Exception:
            chain = []

    blockchain_records_check: dict[str, Any] = {
        "status": "unavailable",
        "checked_records": 0,
        "verified_on_chain": 0,
        "mismatched_on_chain": 0,
        "unanchored_local_only": 0,
        "details": [],
        "error": None,
    }
    checkpoint_report: dict[str, Any] = {
        "status": "unavailable",
        "valid": None,
        "checkpoint_present": False,
        "reason": "Blockchain node not queried.",
    }

    if blockchain_client is not None:
        try:
            status = blockchain_client.get_status()
            if status.get("connected") and status.get("contract_deployed"):
                latest_cp = blockchain_client.get_latest_checkpoint()
                checkpoint_report = verify_against_checkpoint(chain, latest_cp)

                verified = 0
                mismatched = 0
                unanchored = 0
                details = []
                for rec in chain:
                    bc = rec.get("blockchain") or {}
                    rec_hash = record_hash_of(rec)
                    ver_id = verification_id_of(rec)
                    on_chain_res = blockchain_client.verify_record_hash_on_chain(rec_hash)
                    if on_chain_res["exists_on_chain"]:
                        verified += 1
                    elif bc.get("anchoring_status") == "anchored":
                        mismatched += 1
                        details.append(
                            {
                                "verification_id": ver_id,
                                "sequence_number": sequence_of(rec),
                                "issue": "Local record marked anchored, but record_hash not found on-chain.",
                            }
                        )
                    else:
                        unanchored += 1

                bc_status = "verified" if mismatched == 0 and verified > 0 else (
                    "mismatch" if mismatched > 0 else "no_anchored_records"
                )
                blockchain_records_check = {
                    "status": bc_status,
                    "checked_records": len(chain),
                    "verified_on_chain": verified,
                    "mismatched_on_chain": mismatched,
                    "unanchored_local_only": unanchored,
                    "details": details,
                    "error": None,
                }
            else:
                msg = status.get("error") or "Blockchain node disconnected."
                blockchain_records_check["error"] = msg
                checkpoint_report["reason"] = msg
        except Exception as exc:
            blockchain_records_check["error"] = str(exc)
            checkpoint_report["reason"] = str(exc)

    return {
        "valid": bool(local_report["valid"])
        and (checkpoint_report.get("valid") is not False)
        and blockchain_records_check.get("mismatched_on_chain", 0) == 0,
        "length": local_report.get("length", 0),
        "broken_at_index": local_report.get("broken_at_index"),
        "reason": local_report.get("reason"),
        "local_ledger": local_report,
        "blockchain_records": blockchain_records_check,
        "checkpoint": checkpoint_report,
    }
