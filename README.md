# Blockchain-Based Content Verification & Deepfake Detection — Prototype

This is a working prototype of the architecture from your research notes:

```
Input Content -> AI Analysis (detector.py) -> Hashing (hasher.py)
              -> Ledger / "Blockchain" (ledger.py) -> Certificate (certificate.py)
              -> API (main.py) -> Dashboard (frontend/index.html)
```

## What's real vs. simulated (be upfront about this in your paper)

| Component | Status |
|---|---|
| SHA-256 + perceptual hashing | **Fully real** |
| Hash-chained ledger (tamper-evidence) | **Fully real** (simulated single-chain ledger, not a deployed multi-node blockchain — see note in `ledger.py`) |
| Deepfake detection | **Heuristic placeholder** (real image-statistics analysis, not a trained CNN — see note in `detector.py`) |
| API + dashboard | **Fully real** |

This is a normal and expected scope for a student prototype: it proves the *architecture* works end-to-end, and both placeholder pieces are written so you can swap in a real trained model or a real Solidity smart contract later without touching anything else.

---

## Which app to run this in

**Recommended: Visual Studio Code** (free, https://code.visualstudio.com)
1. Install VS Code, then install the **Python extension** (search "Python" in the Extensions panel, by Microsoft).
2. Open this folder in VS Code: `File > Open Folder` → select `deepfake_verify`.
3. Open a terminal inside VS Code: `Terminal > New Terminal`.
4. Follow the "Run it" steps below in that terminal.

Any other setup works too — PyCharm, or even a plain terminal — VS Code is just the easiest for a mixed Python + HTML project like this.

You'll also need **Python 3.10+** installed (https://python.org — check "Add to PATH" during install on Windows).

---

## Run it

```bash
# 1. Create a virtual environment (recommended, keeps things clean)
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # Mac/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start the backend API
uvicorn app.main:app --reload --port 8000
```

Leave that terminal running. You should see:
```
Uvicorn running on http://127.0.0.1:8000
```

Then either:
- Open **http://127.0.0.1:8000/docs** for the interactive API (upload files, see raw JSON), or
- Open **`frontend/index.html`** directly in your browser (double-click it) for the dashboard.

---

## Project structure

```
deepfake_verify/
├── app/
│   ├── detector.py      # Phase 1: AI manipulation analysis
│   ├── hasher.py         # Digital fingerprint (SHA-256 + perceptual hash)
│   ├── ledger.py         # Phase 2: simulated blockchain ledger
│   ├── certificate.py    # Authentication certificate generator
│   └── main.py           # FastAPI backend tying it all together
├── frontend/
│   └── index.html        # Phase 3: verification dashboard
├── data/
│   └── ledger.json        # created automatically on first run — your "chain"
└── requirements.txt
```

## API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/verify` | Upload a file → runs AI analysis, hashing, registers on ledger, returns certificate |
| GET | `/check/{content_hash}` | Look up an existing record by its SHA-256 hash |
| GET | `/history` | Full verification history (audit trail) |
| GET | `/ledger/integrity` | Confirms the chain hasn't been tampered with |

## Demo script for evaluators

1. Upload an image → show the verdict, deepfake score, and hash appear in the dashboard.
2. Upload the **same** image again → show it returns "hash match (already verified previously)" instead of creating a new record — proves duplicate/authenticity checking works.
3. Open `data/ledger.json`, manually edit one field in an old record, save it, then hit `/ledger/integrity` → show it now returns `"valid": false` — this is your tamper-evidence proof, the core selling point of the blockchain layer.

## Next steps to strengthen this for your paper

1. Swap `detector.py`'s heuristic for a real pretrained deepfake classifier (search "FaceForensics++ pretrained pytorch" on GitHub).
2. Swap `ledger.py`'s JSON hash-chain for a real Solidity smart contract deployed on Ganache, called via `web3.py` — the function signatures are already designed to make this a drop-in replacement.
3. Add video support (currently images only) by sampling frames with `opencv-python` and running `analyze_image()` per frame.
