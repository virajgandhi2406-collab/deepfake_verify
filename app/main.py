"""
main.py
-------
The Verification Backend API. This is the glue layer connecting:
  Input Digital Content -> AI Analysis -> Hashing -> Ledger -> Certificate
                                                                  |
                                                    Verification Interface (frontend)

Run with:  uvicorn app.main:app --reload --port 8000
Then open: http://127.0.0.1:8000/docs   (interactive API docs)
       or: frontend/index.html          (the dashboard)
"""

import os
import shutil
import tempfile

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.detector import analyze_image, analyze_video
from app.hasher import sha256_of_file, perceptual_hash
from app.ledger import add_record, get_record_by_hash, get_history, verify_chain_integrity
from app.certificate import generate_certificate

app = FastAPI(title="Blockchain-Based Content Verification API")

# Allow the local HTML dashboard (opened as a file or on another port) to call this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/verify")
async def verify_content(file: UploadFile = File(...)):
    """
    Phase 1 + 2 + registration:
      1. Save uploaded file temporarily
      2. Run AI manipulation analysis
      3. Compute SHA-256 + perceptual hash
      4. Check if this exact content was already registered (hash match/mismatch)
      5. Write a new record to the ledger
      6. Return an authentication certificate
    """
    suffix = os.path.splitext(file.filename)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        content_hash = sha256_of_file(tmp_path)
        phash = perceptual_hash(tmp_path)

        existing = get_record_by_hash(content_hash)
        if existing:
            # Re-run the detector so records created before detector updates
            # also receive the current AI-provenance classification.
            refreshed = dict(existing)
            refreshed["ai_result"] = analyze_image(tmp_path, file.filename)
            certificate = generate_certificate(refreshed)
            certificate["hash_status"] = "match (already verified previously)"
            return certificate

        ai_result = analyze_image(tmp_path, file.filename)
        block = add_record(content_hash, phash, ai_result, file.filename)
        certificate = generate_certificate(block)
        certificate["hash_status"] = "new registration"
        return certificate
    finally:
        os.remove(tmp_path)


@app.post("/verify-video")
async def verify_video(file: UploadFile = File(...)):
    """Verify a video by sampling frames and recording its file hash."""
    suffix = os.path.splitext(file.filename)[1].lower()
    if suffix not in {".mp4", ".mov", ".avi", ".mkv", ".webm"}:
        raise HTTPException(status_code=415, detail="Upload an MP4, MOV, AVI, MKV, or WEBM video.")

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        content_hash = sha256_of_file(tmp_path)
        existing = get_record_by_hash(content_hash)
        if existing:
            refreshed = dict(existing)
            refreshed["ai_result"] = analyze_video(tmp_path, file.filename)
            certificate = generate_certificate(refreshed)
            certificate["hash_status"] = "match (already verified previously)"
            return certificate

        ai_result = analyze_video(tmp_path, file.filename)
        block = add_record(content_hash, "video-frame-analysis", ai_result, file.filename)
        certificate = generate_certificate(block)
        certificate["hash_status"] = "new registration"
        return certificate
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        os.remove(tmp_path)


@app.get("/check/{content_hash}")
async def check_hash(content_hash: str):
    """Look up whether a given SHA-256 hash is already on the ledger."""
    record = get_record_by_hash(content_hash)
    if not record:
        raise HTTPException(status_code=404, detail="No record found for this hash.")
    return generate_certificate(record)


@app.get("/history")
async def history():
    """Full verification history log (for the dashboard's audit trail)."""
    return get_history()


@app.get("/ledger/integrity")
async def ledger_integrity():
    """Confirms the ledger chain hasn't been tampered with."""
    return verify_chain_integrity()


@app.get("/health")
async def health():
    return {"status": "ok"}
