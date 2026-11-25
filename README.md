# VERITY — Blockchain-Based Verification System for Digital Content Authenticity and Deepfake Detection

**Institution:** Dr. Vishwanath Karad MIT World Peace University, Pune (School of Computer Science & Engineering — Department of Computer Science and Applications)  
**Program & Academic Year:** M.Sc. Computer Science | 2026–27  
**Group No.:** 27 | **Faculty Guide:** Dr. Anuradha Kanade  
**Authors:** Ansari Faizalam Mahmoodbhai (PRN 1272260053), Gandhi Viraj Prashant Kumar (PRN 1272260230), Vraj Modi Jitendra Kumar (PRN 1272260212)

---

## Project Overview

VERITY is an integrated digital media verification system designed to answer **three separate verification questions** independently:

1. **Manipulation Evidence (RQ1):** Does the uploaded image or sampled video show statistical or provenance indicators of manipulation?
2. **File Identity & Similarity (RQ2):** Does the file match an exact previously verified SHA-256 digest, or appear visually similar under 64-bit DCT perceptual hashing?
3. **Record Integrity (RQ3):** Has the verification record or ledger history been modified since registration, verified across both a canonical local hash-chain and an Ethereum-compatible Solidity smart contract (`VerityVerification.sol`)?

---

## System Architecture

```
Upload (Image/Video)
  ├──► Security & Signature Validation (app/security.py)
  ├──► Exact SHA-256 (raw bytes) + 64-bit DCT pHash (app/hasher.py)
  ├──► Modular Forensic Baseline / OpenCV Video Sampler (app/detector.py)
  ├──► Canonical Hash-Chained Audit Ledger (app/ledger.py -> data/ledger.json)
  ├──► Solidity Smart Contract via Web3.py (contracts/VerityVerification.sol)
  └──► JSON + PDF/QR Certificate & Verification Endpoints (app/certificate.py, app/main.py)
```

### Key Technologies
- **Backend:** Python 3.12+, FastAPI, Uvicorn, NumPy, Pillow, OpenCV (`opencv-python-headless`), ReportLab, QRCode
- **Blockchain:** Solidity `0.8.20`, Ganache (local EVM), Web3.py, `py-solc-x` / `solc`
- **Frontend:** Semantic HTML5, dark cybersecurity CSS, modular Vanilla JavaScript (`frontend/`)
- **Testing & Evaluation:** Pytest (unit + real Ganache integration), RQ1/RQ2/RQ3/Runtime modules (`evaluation/`)

---

## Windows Installation & Setup

### 1. Python Environment & Dependencies
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Install Ganache & Solidity Compiler
```powershell
npm.cmd install -g ganache solc@0.8.20
```
*(Or via Python `py-solc-x` if native `solc.exe` execution is permitted on your machine.)*

### 3. Configure Environment Variables
Copy `.env.example` to `.env` (`.env` is gitignored — **never commit private keys**):
```powershell
Copy-Item .env.example .env
```

### 4. Start Ganache Local Blockchain
In a dedicated terminal:
```powershell
ganache.cmd --port 8545 --chain.chainId 1337 --wallet.deterministic
```
Copy one of the printed private keys into `VERITY_PRIVATE_KEY=` inside `.env` (or leave empty in `VERITY_ENVIRONMENT=development` to sign via Ganache's unlocked account #0).

### 5. Compile & Deploy `VerityVerification.sol`
```powershell
python -m blockchain.compile_and_deploy --compile-only
python -m blockchain.compile_and_deploy --rpc-url http://127.0.0.1:8545
```
This writes compiled ABI/bytecode to `contracts/artifacts/VerityVerification.json` and deployment metadata (`contract_address`, `chain_id`, `deployment_tx_hash`, `deployment_block_number`) to `data/deployment.json`. Optionally set `VERITY_CONTRACT_ADDRESS` in `.env`.

### 6. Start FastAPI Backend & Dashboard
```powershell
uvicorn app.main:app --reload --port 8000
```
Open:
- **Cybersecurity Dashboard:** `http://127.0.0.1:8000/`
- **Interactive OpenAPI Docs:** `http://127.0.0.1:8000/docs`

---

## API Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/verify` | Analyze image, compute SHA-256 + DCT pHash, record locally, anchor on-chain |
| `POST` | `/verify-video` | Sample up to 12 equidistant frames via OpenCV, record & anchor on-chain |
| `GET` | `/check/{content_hash}` | Lookup SHA-256 across local re-analysis history and smart contract |
| `GET` | `/history` | Filterable verification audit trail (privacy-sanitized by default) |
| `GET` | `/stats` | Live dashboard telemetry from local ledger and EVM node |
| `GET` | `/ledger/integrity` | Separate local hash-chain, on-chain record, and external checkpoint checks |
| `GET` | `/blockchain/status` | Live EVM RPC connection & smart contract status |
| `GET` | `/blockchain/contract-info` | Smart contract address, owner, compiler, record/checkpoint counts |
| `GET` | `/blockchain/record/{content_hash}` | Retrieve all on-chain events linked to a content SHA-256 |
| `GET` | `/blockchain/transaction/{tx_hash}` | Fetch mined EVM tx, receipt, block number, and decoded events |
| `GET` | `/blockchain/records` | Enumerate on-chain verification records |
| `GET` | `/blockchain/checkpoints` | List all on-chain ledger checkpoints |
| `POST` | `/blockchain/checkpoint` | Anchor current `ledger_root_hash` checkpoint on-chain |
| `GET` | `/certificate/{verification_id}` | Download PDF verification certificate with embedded QR code |
| `GET` | `/verify-certificate/{verification_id}` | Independent verification endpoint resolved by certificate QR code |

---

## Running Automated Tests & Research Evaluations

```powershell
# Unit tests (no Ganache required)
python -m pytest tests/test_unit.py -v

# Real Ganache blockchain integration tests (requires Ganache running on :8545)
python -m pytest tests/test_blockchain_integration.py -v

# Research evaluation suite (RQ2, RQ3 with real Ganache, Runtime benchmark)
python -m evaluation.run_all

# Optional RQ1 evaluation with your own labeled dataset manifest
python -m evaluation.run_all --manifest path/to/manifest.json --split test
```

---

## Production / Cloud Deployment Notes (Render / Vercel)
- **Never point a public cloud deployment at `http://127.0.0.1:8545`.** Local Ganache is only reachable on your local machine.
- For Render or Vercel deployments, set `VERITY_ENVIRONMENT=production`, point `VERITY_RPC_URL` to a hosted EVM testnet/RPC (e.g., Sepolia / Polygon Amoy), set `VERITY_CHAIN_ID`, `VERITY_CONTRACT_ADDRESS`, and `VERITY_PRIVATE_KEY` in the platform's secret environment variables, and use persistent storage instead of ephemeral `/tmp` for off-chain audit records.
- Vercel serverless functions have execution-time and upload-size limits (e.g., ~4.5 MB payload limit); Docker/Render (`Dockerfile` / `render.yaml`) is recommended for video verification.

---

## Research Limitations & Boundaries
- **Heuristic Baseline:** Image/video scores are uncalibrated statistical suspicion indicators (`Low Evidence`, `Review Required`, `Elevated Evidence of Manipulation`, `Inconclusive`), **not** trained deep-learning probabilities.
- **Video Sampling:** Equidistant sampling (up to 12 frames) may miss brief localized manipulations and does not analyze audio or audiovisual synchronization.
- **Blockchain Boundary:** On-chain anchoring proves that a verification record and SHA-256 digest existed in a recorded state at a mined block timestamp; it **never** proves that the original captured scene was genuine.
- **Experimental Status:** RQ2, RQ3, and runtime benchmarks can be executed locally using `evaluation/run_all.py`; full RQ1 real-world benchmark accuracy remains pending an externally licensed labeled corpus (e.g., FaceForensics++).

See [docs/TECHNICAL_DOCUMENTATION.md](file:///c:/Users/viraj/Desktop/deepfake_verify/docs/TECHNICAL_DOCUMENTATION.md) for full architecture, canonical serialization, threat model, and evaluation protocol details.
