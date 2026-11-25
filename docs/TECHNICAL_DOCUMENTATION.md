# VERITY — Technical Documentation

**Official Research Title:** Blockchain-Based Verification System for Digital Content Authenticity and Deepfake Detection  
**Institution:** Dr. Vishwanath Karad MIT World Peace University, Pune (School of Computer Science & Engineering, Department of Computer Science and Applications)  
**Program & Academic Year:** M.Sc. Computer Science | 2026–27  
**Group No.:** 27 | **Guide:** Dr. Anuradha Kanade  

## Group Members & Preserved Contributions
- **Ansari Faizalam Mahmoodbhai (PRN 1272260053):** Led research formulation, literature review, problem formulation, methodology, dual-layer hashing and ledger architecture, evaluation design, and paper authoring.
- **Gandhi Viraj Prashant Kumar (PRN 1272260230):** Handled dashboard and user interaction design, upload-to-result demonstration workflow, frontend-API integration, and certificate presentation.
- **Vraj Modi Jitendra Kumar (PRN 1272260212):** Supported test-case preparation, sample-media organization, hashing/lookup/integrity evaluation checklists, and submission consistency review.

---

## 1. Transition from JSON Hash-Chained Ledger to Blockchain-Anchored Implementation

In the initial prototype (`v1`), VERITY maintained verification records solely in a local JSON hash-chained file (`data/ledger.json`, blocks `0–19`). While local hash-chaining detects single-field modifications and broken `previous_hash` links, a local administrator with write access to `ledger.json` could still truncate trailing blocks or rewrite the entire file and recompute all hashes.

In the upgraded architecture (`v2`), VERITY preserves the local JSON ledger as an auditable off-chain mechanism with **canonical serialization (`2.0-canonical`)**, **single-writer locking**, and **atomic writes**, while integrating an Ethereum-compatible Solidity smart contract (`contracts/VerityVerification.sol`) via `Web3.py` as the authoritative anchoring layer:
- **Legacy Compatibility:** Existing v1 records (`0–19`) remain intact and are validated using the v1 hash rule; newly created records (`2.0-canonical`) use deterministic canonical UTF-8 serialization (`CANONICAL_FIELDS_V2`).
- **On-Chain Anchoring:** Each new verification submits a real transaction to `VerityVerification.sol` containing `contentSha256`, perceptual fingerprint reference, `verificationId`, `mediaType`, `resultCategory`, `analysisConfigId`, `recordHash`, and `localSequence`.
- **Periodic Checkpointing:** Ledger root hashes (`compute_ledger_root_hash`) can be anchored via `registerCheckpoint(...)` to detect both suffix truncation and complete local ledger rewrites.

---

## 2. Smart Contract Architecture (`contracts/VerityVerification.sol`)

- **Compiler:** Solidity `^0.8.20` (`evmVersion: paris`, `viaIR: true`, optimizer `runs: 200`).
- **Key Separation of Duplicate vs. Re-Analysis:**
  - `eventKey = keccak256(abi.encode(contentSha256, analysisConfigId))`
  - **Same file + same `analysisConfigId`:** Reverts with `DuplicateVerificationEvent(eventKey)` on duplicate registration; the backend checks `hasVerificationEvent(...)` and retrieves the existing record instead.
  - **Same file + different `analysisConfigId`:** Allowed! Creates a distinct verification event linked to the same `contentSha256` in `_contentToEventKeys[contentSha256]`.
- **On-Chain Storage Minimization:** Original images/videos and personal filenames are never stored on-chain.
- **Access Control:** `onlyOwner` manages `authorizedSubmitters`; `onlyAuthorized` gates `registerVerification` and `registerCheckpoint`; all read/verification methods are `public`/`external view` for independent auditing.
- **Events:** `VerificationRegistered`, `CheckpointRegistered`, `SubmitterAuthorizationChanged`.

---

## 3. Canonical Verification Record Structure (`2.0-canonical`)

Canonical hash `record_hash = SHA256(C_i)` is computed over the deterministic UTF-8 JSON encoding (sorted keys, `separators=(",", ":")`, floats rounded to 6 decimal places, explicit `null` for absent values) of:
1. `schema_version` (`"2.0-canonical"`)
2. `verification_id` (e.g., `VRT-0020-A1B2C3D4`)
3. `sequence_number` (`0, 1, 2, ...`)
4. `content_sha256` (64-hex lowercase SHA-256 of raw uploaded bytes)
5. `media_type` (`"image"` or `"video"`)
6. `file_size_bytes`
7. `perceptual_fingerprints` (64-bit DCT pHash for images; sampled-frame pHash set + SHA-256 digest for videos)
8. `analysis_indicators` (noise residual, 4x4 regional edge density, consistency, metadata provenance)
9. `heuristic_score`
10. `verdict` (`Low Evidence` | `Review Required` | `Elevated Evidence of Manipulation` | `Inconclusive`)
11. `detector_version`
12. `configuration_id`
13. `server_timestamp` (ISO-8601 UTC)
14. `previous_hash` (`0x00...` 64 zeros for genesis, else `record_hash` of record `i-1`)

Post-anchoring `blockchain` receipt metadata (`tx_hash`, `block_number`, `contract_address`, `chain_id`, `submitter`, `event_key`) is attached alongside the record without altering `record_hash`.

---

## 4. Threat Model & Operational Controls

| Threat | Mitigation in VERITY | Residual Boundary |
|---|---|---|
| Single-field edit on local ledger | Canonical `record_hash` validation (`record_hash_mismatch`) | — |
| Broken chain link / reordering | `previous_hash` and `sequence_number` checks | — |
| Suffix truncation / deletion | External smart-contract `LedgerCheckpoint` `record_count` check | Unanchored suffix after latest checkpoint |
| Complete local rewrite + rehash | External `ledger_root_hash` comparison against `VerityVerification.sol` | Unanchored suffix after latest checkpoint |
| Concurrent append race | `threading.RLock()` + OS file lock + atomic temp-file `os.replace` | Single-node storage lock |
| Malicious / malformed uploads | Byte size limits, magic-byte inspection, `Image.MAX_IMAGE_PIXELS` limit, guaranteed temp cleanup | Advanced zero-day codec exploits in underlying C libs |
| Privacy leakage | Public endpoints mask original filenames unless `VERITY_EXPOSE_FILENAMES=true`; GPS/owner EXIF stripped | SHA-256 enables correlation if an attacker already holds the exact file |
| Misleading detector output | Heuristic score explicitly labeled as uncalibrated suspicion score; missing EXIF treated as unavailable evidence | Heuristic baseline has false positives/negatives vs. trained CNNs |

---

## 5. Experimental Methodology & Evaluation Metrics

- **RQ1 (`evaluation/rq1_detection.py`):** Evaluates labeled manifests (`evaluation/dataset_manifest.py`) enforcing `source_group_id` split isolation to prevent data leakage. Positive class = `{manipulated, ai_generated}`. Reports confusion matrix, precision, recall, F1, FPR, balanced accuracy, coverage, and inconclusive count.
- **RQ2 (`evaluation/rq2_fingerprints.py`):** Measures exact SHA-256 consistency (identical, 1-byte change, metadata edit) and 64-bit DCT perceptual hash Hamming-distance distributions across resizing (50%, 75%), JPEG recompression (q=85, 60, 35), metadata edits, and unrelated images.
- **RQ3 (`evaluation/rq3_ledger_attacks.py`):** Executes 5 tampering scenarios + direct on-chain verification against `VerityVerification.sol`, demonstrating exactly what local hash-chaining catches vs. what external blockchain checkpoints catch.
- **Runtime (`evaluation/runtime_benchmark.py`):** Records median and p95 latencies per stage (`hashing`, `decode`, `analysis`, `ledger_write`, `blockchain_tx`, `total`) alongside hardware/software versions.
