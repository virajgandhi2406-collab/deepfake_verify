"""
Real Ganache blockchain integration tests for VERITY.

Requires a live Ganache/EVM RPC node (default: http://127.0.0.1:8545).
Exercises real Solidity compilation, deployment, mined transactions, receipt
validation, duplicate-handling, detector-version re-analysis, checkpoints,
RQ3 attack detection, PDF certificate verification, and temp file cleanup.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from fastapi.testclient import TestClient
import pytest
from web3 import Web3

from app.blockchain_client import VerityBlockchainClient
from app.main import app
from blockchain.compile_and_deploy import compile_contract, deploy_contract
from evaluation.rq3_ledger_attacks import evaluate_rq3

RPC_URL = os.getenv("VERITY_TEST_RPC_URL", "http://127.0.0.1:8545")


@pytest.fixture(scope="module")
def live_ganache_env(tmp_path_factory):
    w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 5}))
    if not w3.is_connected():
        pytest.fail(
            f"Ganache EVM node is required at {RPC_URL} for real blockchain integration tests."
        )

    tmp_dir = tmp_path_factory.mktemp("verity_bc_test")
    artifact = compile_contract()
    assert artifact["contract_name"] == "VerityVerification"

    deployment_path = tmp_dir / "deployment.json"
    deployment = deploy_contract(
        rpc_url=RPC_URL,
        network_name="Ganache Test",
        deployment_path=deployment_path,
    )
    assert deployment["contract_address"].startswith("0x")
    assert deployment["deployment_block_number"] >= 0

    return {
        "rpc_url": RPC_URL,
        "contract_address": deployment["contract_address"],
        "deployment_path": deployment_path,
        "ledger_path": tmp_dir / "test_ledger.json",
    }


def test_real_ganache_full_workflow(
    live_ganache_env, sample_image_pair, sample_video_file, monkeypatch
):
    img1, _ = sample_image_pair
    monkeypatch.setenv("VERITY_RPC_URL", live_ganache_env["rpc_url"])
    monkeypatch.setenv("VERITY_CONTRACT_ADDRESS", live_ganache_env["contract_address"])
    monkeypatch.setenv("VERITY_DEPLOYMENT_PATH", str(live_ganache_env["deployment_path"]))
    monkeypatch.setenv("VERITY_LEDGER_PATH", str(live_ganache_env["ledger_path"]))
    monkeypatch.setenv("VERITY_ENVIRONMENT", "development")
    monkeypatch.setenv("VERITY_AUTO_ANCHOR", "true")
    monkeypatch.setenv("VERITY_CHECKPOINT_INTERVAL", "10")

    client = TestClient(app)

    # 1. Blockchain status & contract info
    st_res = client.get("/blockchain/status")
    assert st_res.status_code == 200
    st = st_res.json()
    assert st["connected"] is True
    assert st["contract_deployed"] is True
    assert st["contract_address"] == live_ganache_env["contract_address"]

    info_res = client.get("/blockchain/contract-info")
    assert info_res.status_code == 200
    assert info_res.json()["contract_name"] == "VerityVerification"

    # 2. Verify image -> real mined transaction
    temp_before = set(Path(tempfile.gettempdir()).glob("verity_*"))
    with open(img1, "rb") as fh:
        v1 = client.post("/verify", files={"file": ("test_sample.jpg", fh, "image/jpeg")})
    assert v1.status_code == 200
    c1 = v1.json()
    assert c1["hash_status"] == "new registration"
    assert c1["blockchain"]["anchoring_status"] == "anchored"
    tx_hash_1 = c1["blockchain"]["tx_hash"]
    assert tx_hash_1.startswith("0x")
    assert c1["blockchain"]["block_number"] >= 1
    temp_after = set(Path(tempfile.gettempdir()).glob("verity_*"))
    assert temp_after == temp_before  # Temp file cleaned up

    # 3. Duplicate upload with same detector version -> returns existing without duplicate tx
    with open(img1, "rb") as fh:
        v_dup = client.post("/verify", files={"file": ("test_sample.jpg", fh, "image/jpeg")})
    assert v_dup.status_code == 200
    assert "exact match" in v_dup.json()["hash_status"]
    assert v_dup.json()["verification_id"] == c1["verification_id"]

    # 4. Same file + NEW detector version -> allowed! Creates new linked verification event on-chain
    with open(img1, "rb") as fh:
        v_re = client.post(
            "/verify?detector_version=xception-v1.0&config_id=ffpp-eval",
            files={"file": ("test_sample.jpg", fh, "image/jpeg")},
        )
    assert v_re.status_code == 200
    c_re = v_re.json()
    assert "re-analysis" in c_re["hash_status"]
    assert c_re["verification_id"] != c1["verification_id"]
    assert c_re["blockchain"]["anchoring_status"] == "anchored"
    assert c_re["blockchain"]["tx_hash"] != tx_hash_1

    # 5. On-chain content record lookup shows 2 linked events for same SHA-256
    bc_rec = client.get(f"/blockchain/record/{c1['content_hash_sha256']}")
    assert bc_rec.status_code == 200
    assert bc_rec.json()["count"] == 2

    # 6. Transaction receipt lookup
    tx_res = client.get(f"/blockchain/transaction/{tx_hash_1}")
    assert tx_res.status_code == 200
    assert tx_res.json()["status"] == "confirmed"
    assert tx_res.json()["events"][0]["event"] == "VerificationRegistered"

    # 7. Video verification -> real mined transaction
    with open(sample_video_file, "rb") as fh:
        vv = client.post(
            "/verify-video", files={"file": ("clip.mp4", fh, "video/mp4")}
        )
    assert vv.status_code == 200
    cv_cert = vv.json()
    assert cv_cert["media_type"] == "video"
    assert cv_cert["blockchain"]["anchoring_status"] == "anchored"

    # 8. Manual checkpoint creation & verification
    cp_res = client.post(
        "/blockchain/checkpoint", json={"note": "Integration test checkpoint"}
    )
    assert cp_res.status_code == 200
    cp_data = cp_res.json()
    assert cp_data["checkpoint"]["record_count"] == 3

    integ_res = client.get("/ledger/integrity")
    assert integ_res.status_code == 200
    integ = integ_res.json()
    assert integ["local_ledger"]["valid"] is True
    assert integ["blockchain_records"]["verified_on_chain"] == 3
    assert integ["checkpoint"]["valid"] is True
    assert integ["checkpoint"]["status"] == "verified"

    # 9. Certificate PDF & QR verification endpoint
    pdf_res = client.get(f"/certificate/{c1['verification_id']}")
    assert pdf_res.status_code == 200
    assert pdf_res.headers["content-type"] == "application/pdf"
    assert pdf_res.content.startswith(b"%PDF")

    qr_res = client.get(f"/verify-certificate/{c1['verification_id']}?format=json")
    assert qr_res.status_code == 200
    qr_json = qr_res.json()
    assert qr_json["verified"] is True
    assert qr_json["local_matches_on_chain"] is True

    # 10. Error handling: 404 & 415 & temp cleanup on failure
    assert client.get("/check/" + ("0" * 64)).status_code == 404
    bad_upload = client.post(
        "/verify", files={"file": ("bad.jpg", b"not-an-image", "image/jpeg")}
    )
    assert bad_upload.status_code == 415
    assert set(Path(tempfile.gettempdir()).glob("verity_*")) == temp_before

    # 11. RQ3 real-Ganache attack evaluation
    bc_client = VerityBlockchainClient()
    rq3 = evaluate_rq3(blockchain_client=bc_client)
    assert rq3["checkpoint_source"] == "real_evm_smart_contract"
    assert rq3["attacks"]["attack_1_field_edit_no_rehash"]["local_detected"] is True
    assert rq3["attacks"]["attack_2_broken_previous_hash"]["local_detected"] is True
    assert rq3["attacks"]["attack_3_record_reordering"]["local_detected"] is True
    assert rq3["attacks"]["attack_4_suffix_truncation"]["local_chain_detected"] is False
    assert rq3["attacks"]["attack_4_suffix_truncation"]["checkpoint_detected"] is True
    assert rq3["attacks"]["attack_5_complete_rewrite_with_rehash"]["local_chain_detected"] is False
    assert rq3["attacks"]["attack_5_complete_rewrite_with_rehash"]["checkpoint_detected"] is True
