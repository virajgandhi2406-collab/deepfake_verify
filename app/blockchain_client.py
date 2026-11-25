"""
blockchain_client.py
--------------------
Web3.py integration for VerityVerification.sol on an Ethereum-compatible
network (Ganache local development node or external EVM RPC).

Handles real contract transactions, receipt validation, event decoding,
on-chain record lookup, and external ledger checkpointing.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import threading
from typing import Any

from web3 import Web3
from web3.exceptions import ContractLogicError, TransactionNotFound

from app.config import VeritySettings, get_settings, load_deployment_metadata
from blockchain.compile_and_deploy import load_or_compile_artifact


def _to_bytes32(hex_str: str) -> bytes:
    cleaned = hex_str.strip().lower()
    if cleaned.startswith("0x"):
        cleaned = cleaned[2:]
    if len(cleaned) != 64:
        raise ValueError(f"Expected 64-hex-character SHA-256 digest, got length {len(cleaned)}")
    return bytes.fromhex(cleaned)


def _format_hex(value: bytes | str) -> str:
    if isinstance(value, str):
        return value if value.startswith("0x") else f"0x{value}"
    raw = value.hex()
    return raw if raw.startswith("0x") else f"0x{raw}"


def _format_sha256(value: bytes | str) -> str:
    if isinstance(value, str):
        return value[2:] if value.startswith("0x") else value
    return value.hex()


def _iso_from_unix(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()


class VerityBlockchainClient:
    """Thread-safe Web3.py client for VerityVerification.sol."""

    def __init__(self, settings: VeritySettings | None = None) -> None:
        self._settings = settings or get_settings()
        self._tx_lock = threading.Lock()
        self._w3: Web3 | None = None

    def _connect(self) -> tuple[Web3, Any, dict[str, Any]]:
        settings = get_settings() if self._settings is None else self._settings
        w3 = Web3(Web3.HTTPProvider(settings.rpc_url, request_kwargs={"timeout": 15}))
        if not w3.is_connected():
            raise ConnectionError(f"EVM RPC node unreachable at {settings.rpc_url}")

        if not settings.contract_address:
            raise RuntimeError(
                "Smart contract address is not configured. Deploy VerityVerification.sol first."
            )

        checksum_addr = Web3.to_checksum_address(settings.contract_address)
        code = w3.eth.get_code(checksum_addr)
        if not code or code == b"\x00":
            raise RuntimeError(
                f"No contract bytecode deployed at {checksum_addr} on {settings.rpc_url}."
            )

        artifact = load_or_compile_artifact(
            solc_version=settings.solc_version,
            artifact_path=settings.artifact_path,
        )
        contract = w3.eth.contract(address=checksum_addr, abi=artifact["abi"])
        return w3, contract, artifact

    def _resolve_signer(self, w3: Web3) -> tuple[str, str | None]:
        settings = get_settings() if self._settings is None else self._settings
        if settings.private_key:
            acct = w3.eth.account.from_key(settings.private_key)
            return Web3.to_checksum_address(acct.address), settings.private_key

        if settings.environment == "development":
            accounts = w3.eth.accounts
            if accounts:
                return Web3.to_checksum_address(accounts[0]), None

        raise RuntimeError(
            "No signing key configured (VERITY_PRIVATE_KEY) and no unlocked development account available."
        )

    def _send_contract_tx(self, w3: Web3, fn_call: Any, gas_limit: int = 750_000) -> dict[str, Any]:
        with self._tx_lock:
            sender_address, private_key = self._resolve_signer(w3)
            chain_id = int(w3.eth.chain_id)
            if private_key:
                nonce = w3.eth.get_transaction_count(sender_address, "pending")
                tx = fn_call.build_transaction(
                    {
                        "from": sender_address,
                        "nonce": nonce,
                        "chainId": chain_id,
                        "gas": gas_limit,
                        "gasPrice": w3.eth.gas_price,
                    }
                )
                signed = w3.eth.account.from_key(private_key).sign_transaction(tx)
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            else:
                tx_hash = fn_call.transact({"from": sender_address, "gas": gas_limit})

            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            if int(receipt.status) != 1:
                raise RuntimeError(
                    f"Blockchain transaction reverted: {_format_hex(receipt.transactionHash)}"
                )

            block = w3.eth.get_block(receipt.blockNumber)
            return {
                "tx_hash": _format_hex(receipt.transactionHash),
                "block_number": int(receipt.blockNumber),
                "block_hash": _format_hex(receipt.blockHash),
                "block_timestamp": _iso_from_unix(int(block.timestamp)),
                "gas_used": int(receipt.gasUsed),
                "status": int(receipt.status),
                "submitter": sender_address,
                "chain_id": chain_id,
                "receipt": receipt,
            }

    @staticmethod
    def _serialize_record(raw: tuple | list) -> dict[str, Any]:
        return {
            "event_key": _format_hex(raw[0]),
            "content_sha256": _format_sha256(raw[1]),
            "perceptual_fingerprint": str(raw[2]),
            "verification_id": str(raw[3]),
            "media_type": str(raw[4]),
            "result_category": str(raw[5]),
            "analysis_config_id": str(raw[6]),
            "record_hash": _format_sha256(raw[7]),
            "local_sequence": int(raw[8]),
            "on_chain_timestamp_unix": int(raw[9]),
            "on_chain_timestamp": _iso_from_unix(int(raw[9])),
            "submitter": Web3.to_checksum_address(raw[10]),
            "exists": bool(raw[11]),
        }

    @staticmethod
    def _serialize_checkpoint(raw: tuple | list) -> dict[str, Any]:
        return {
            "checkpoint_id": int(raw[0]),
            "ledger_root_hash": _format_sha256(raw[1]),
            "record_count": int(raw[2]),
            "last_sequence_number": int(raw[3]),
            "timestamp_unix": int(raw[4]),
            "timestamp": _iso_from_unix(int(raw[4])),
            "submitter": Web3.to_checksum_address(raw[5]),
            "note": str(raw[6]),
            "exists": bool(raw[7]),
        }

    def get_status(self) -> dict[str, Any]:
        settings = get_settings() if self._settings is None else self._settings
        deployment = load_deployment_metadata(settings.deployment_path)
        base = {
            "connected": False,
            "rpc_url": settings.rpc_url,
            "network_name": settings.network_name,
            "configured_chain_id": settings.chain_id,
            "contract_address": settings.contract_address or None,
            "contract_deployed": False,
            "signer_mode": "private_key" if settings.private_key else "node_unlocked_dev",
            "latest_block": None,
            "actual_chain_id": None,
            "submitter_address": None,
            "total_on_chain_records": 0,
            "total_checkpoints": 0,
            "latest_anchored_ledger_hash": None,
            "deployment_tx_hash": deployment.get("deployment_tx_hash"),
            "deployment_block_number": deployment.get("deployment_block_number"),
            "error": None,
        }
        try:
            w3 = Web3(Web3.HTTPProvider(settings.rpc_url, request_kwargs={"timeout": 8}))
            if not w3.is_connected():
                base["error"] = f"RPC node unreachable at {settings.rpc_url}"
                return base
            base["connected"] = True
            base["actual_chain_id"] = int(w3.eth.chain_id)
            base["latest_block"] = int(w3.eth.block_number)
            try:
                addr, _ = self._resolve_signer(w3)
                base["submitter_address"] = addr
            except Exception as exc:
                base["error"] = str(exc)

            if not settings.contract_address:
                base["error"] = "Contract address not configured."
                return base

            w3, contract, _ = self._connect()
            base["contract_deployed"] = True
            base["owner"] = Web3.to_checksum_address(contract.functions.owner().call())
            base["total_on_chain_records"] = int(contract.functions.getTotalRecords().call())
            base["total_checkpoints"] = int(contract.functions.getCheckpointsCount().call())
            latest_root = contract.functions.latestAnchoredLedgerHash().call()
            latest_root_hex = _format_sha256(latest_root)
            base["latest_anchored_ledger_hash"] = (
                None if latest_root_hex == "0" * 64 else latest_root_hex
            )
            return base
        except Exception as exc:
            base["error"] = str(exc)
            return base

    def get_contract_info(self) -> dict[str, Any]:
        settings = get_settings() if self._settings is None else self._settings
        deployment = load_deployment_metadata(settings.deployment_path)
        w3, contract, artifact = self._connect()
        latest_root = _format_sha256(contract.functions.latestAnchoredLedgerHash().call())
        return {
            "contract_name": artifact.get("contract_name", "VerityVerification"),
            "contract_address": Web3.to_checksum_address(settings.contract_address),
            "network_name": settings.network_name,
            "chain_id": int(w3.eth.chain_id),
            "solc_version": artifact.get("solc_version"),
            "compiler_backend": artifact.get("compiler_backend"),
            "source_sha256": artifact.get("source_sha256"),
            "owner": Web3.to_checksum_address(contract.functions.owner().call()),
            "total_records": int(contract.functions.getTotalRecords().call()),
            "total_checkpoints": int(contract.functions.getCheckpointsCount().call()),
            "latest_anchored_ledger_hash": None if latest_root == "0" * 64 else latest_root,
            "latest_checkpoint_record_count": int(
                contract.functions.latestCheckpointRecordCount().call()
            ),
            "deployment_tx_hash": deployment.get("deployment_tx_hash"),
            "deployment_block_number": deployment.get("deployment_block_number"),
            "abi_functions": [
                item["name"] for item in artifact["abi"] if item.get("type") == "function"
            ],
            "abi_events": [
                item["name"] for item in artifact["abi"] if item.get("type") == "event"
            ],
        }

    def get_verification_event(
        self, content_sha256_hex: str, analysis_config_id: str
    ) -> dict[str, Any] | None:
        w3, contract, _ = self._connect()
        digest = _to_bytes32(content_sha256_hex)
        exists = contract.functions.hasVerificationEvent(digest, analysis_config_id).call()
        if not exists:
            return None
        raw = contract.functions.getVerificationEvent(digest, analysis_config_id).call()
        return self._serialize_record(raw)

    def get_records_by_content_hash(self, content_sha256_hex: str) -> list[dict[str, Any]]:
        _, contract, _ = self._connect()
        digest = _to_bytes32(content_sha256_hex)
        items = contract.functions.getRecordsByContentHash(digest).call()
        return [self._serialize_record(item) for item in items]

    def get_verification_by_id(self, verification_id: str) -> dict[str, Any] | None:
        _, contract, _ = self._connect()
        try:
            raw = contract.functions.getVerificationById(verification_id).call()
            return self._serialize_record(raw)
        except ContractLogicError:
            return None

    def verify_record_hash_on_chain(self, record_hash_hex: str) -> dict[str, Any]:
        _, contract, _ = self._connect()
        digest = _to_bytes32(record_hash_hex)
        exists, event_key, content_sha256, ts = contract.functions.verifyRecordHashOnChain(
            digest
        ).call()
        return {
            "exists_on_chain": bool(exists),
            "record_hash": record_hash_hex.lower(),
            "event_key": _format_hex(event_key) if exists else None,
            "content_sha256": _format_sha256(content_sha256) if exists else None,
            "on_chain_timestamp": _iso_from_unix(int(ts)) if exists else None,
        }

    def register_verification(
        self,
        *,
        content_sha256_hex: str,
        perceptual_fingerprint: str,
        verification_id: str,
        media_type: str,
        result_category: str,
        analysis_config_id: str,
        record_hash_hex: str,
        local_sequence: int,
    ) -> dict[str, Any]:
        settings = get_settings() if self._settings is None else self._settings
        w3, contract, _ = self._connect()
        content_bytes = _to_bytes32(content_sha256_hex)
        record_bytes = _to_bytes32(record_hash_hex)

        if contract.functions.hasVerificationEvent(content_bytes, analysis_config_id).call():
            existing = self._serialize_record(
                contract.functions.getVerificationEvent(
                    content_bytes, analysis_config_id
                ).call()
            )
            return {
                "anchoring_status": "already_anchored",
                "contract_address": Web3.to_checksum_address(settings.contract_address),
                "chain_id": int(w3.eth.chain_id),
                "network_name": settings.network_name,
                "on_chain_record": existing,
            }

        fn_call = contract.functions.registerVerification(
            content_bytes,
            perceptual_fingerprint,
            verification_id,
            media_type,
            result_category,
            analysis_config_id,
            record_bytes,
            int(local_sequence),
        )
        tx_meta = self._send_contract_tx(w3, fn_call)
        receipt = tx_meta.pop("receipt")
        decoded_events = contract.events.VerificationRegistered().process_receipt(receipt)
        event_args = dict(decoded_events[0]["args"]) if decoded_events else {}

        on_chain_record = self._serialize_record(
            contract.functions.getVerificationEvent(
                content_bytes, analysis_config_id
            ).call()
        )
        return {
            "anchoring_status": "anchored",
            "tx_hash": tx_meta["tx_hash"],
            "block_number": tx_meta["block_number"],
            "block_hash": tx_meta["block_hash"],
            "block_timestamp": tx_meta["block_timestamp"],
            "gas_used": tx_meta["gas_used"],
            "tx_status": tx_meta["status"],
            "submitter": tx_meta["submitter"],
            "contract_address": Web3.to_checksum_address(settings.contract_address),
            "chain_id": tx_meta["chain_id"],
            "network_name": settings.network_name,
            "event_key": _format_hex(event_args["eventKey"])
            if "eventKey" in event_args
            else on_chain_record["event_key"],
            "on_chain_record": on_chain_record,
        }

    def register_checkpoint(
        self,
        *,
        ledger_root_hash_hex: str,
        record_count: int,
        last_sequence_number: int,
        note: str = "VERITY periodic checkpoint",
    ) -> dict[str, Any]:
        settings = get_settings() if self._settings is None else self._settings
        w3, contract, _ = self._connect()
        root_bytes = _to_bytes32(ledger_root_hash_hex)

        fn_call = contract.functions.registerCheckpoint(
            root_bytes,
            int(record_count),
            int(last_sequence_number),
            note,
        )
        tx_meta = self._send_contract_tx(w3, fn_call, gas_limit=500_000)
        receipt = tx_meta.pop("receipt")
        decoded = contract.events.CheckpointRegistered().process_receipt(receipt)
        checkpoint_id = int(decoded[0]["args"]["checkpointId"]) if decoded else int(
            contract.functions.getCheckpointsCount().call()
        )
        checkpoint = self._serialize_checkpoint(
            contract.functions.getCheckpoint(checkpoint_id).call()
        )
        return {
            "status": "anchored",
            "tx_hash": tx_meta["tx_hash"],
            "block_number": tx_meta["block_number"],
            "block_hash": tx_meta["block_hash"],
            "block_timestamp": tx_meta["block_timestamp"],
            "gas_used": tx_meta["gas_used"],
            "submitter": tx_meta["submitter"],
            "contract_address": Web3.to_checksum_address(settings.contract_address),
            "chain_id": tx_meta["chain_id"],
            "network_name": settings.network_name,
            "checkpoint": checkpoint,
        }

    def get_checkpoints(self) -> list[dict[str, Any]]:
        _, contract, _ = self._connect()
        count = int(contract.functions.getCheckpointsCount().call())
        return [
            self._serialize_checkpoint(contract.functions.getCheckpoint(i).call())
            for i in range(1, count + 1)
        ]

    def get_latest_checkpoint(self) -> dict[str, Any] | None:
        _, contract, _ = self._connect()
        count = int(contract.functions.getCheckpointsCount().call())
        if count == 0:
            return None
        return self._serialize_checkpoint(contract.functions.getLatestCheckpoint().call())

    def verify_anchored_ledger_hash(self, ledger_root_hash_hex: str) -> dict[str, Any]:
        _, contract, _ = self._connect()
        root_bytes = _to_bytes32(ledger_root_hash_hex)
        matched, cp_id, rec_count, ts = contract.functions.verifyAnchoredLedgerHash(
            root_bytes
        ).call()
        return {
            "matched": bool(matched),
            "checkpoint_id": int(cp_id) if matched else None,
            "record_count": int(rec_count) if matched else None,
            "timestamp": _iso_from_unix(int(ts)) if matched else None,
            "ledger_root_hash": ledger_root_hash_hex.lower(),
        }

    def get_all_on_chain_records(self, offset: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        _, contract, _ = self._connect()
        keys = contract.functions.getEventKeysSlice(int(offset), int(limit)).call()
        records = []
        for key in keys:
            raw = contract.functions.getVerificationByKey(key).call()
            records.append(self._serialize_record(raw))
        return records

    def get_transaction_details(self, tx_hash: str) -> dict[str, Any] | None:
        w3, contract, _ = self._connect()
        normalized = tx_hash if tx_hash.startswith("0x") else f"0x{tx_hash}"
        try:
            tx = w3.eth.get_transaction(normalized)
            receipt = w3.eth.get_transaction_receipt(normalized)
        except TransactionNotFound:
            return None

        block = w3.eth.get_block(receipt.blockNumber)
        verification_events = []
        for log in contract.events.VerificationRegistered().process_receipt(receipt):
            args = dict(log["args"])
            verification_events.append(
                {
                    "event": "VerificationRegistered",
                    "event_key": _format_hex(args["eventKey"]),
                    "content_sha256": _format_sha256(args["contentSha256"]),
                    "verification_id": args["verificationId"],
                    "media_type": args["mediaType"],
                    "result_category": args["resultCategory"],
                    "analysis_config_id": args["analysisConfigId"],
                    "record_hash": _format_sha256(args["recordHash"]),
                    "submitter": Web3.to_checksum_address(args["submitter"]),
                    "timestamp": _iso_from_unix(int(args["timestamp"])),
                }
            )

        checkpoint_events = []
        for log in contract.events.CheckpointRegistered().process_receipt(receipt):
            args = dict(log["args"])
            checkpoint_events.append(
                {
                    "event": "CheckpointRegistered",
                    "checkpoint_id": int(args["checkpointId"]),
                    "ledger_root_hash": _format_sha256(args["ledgerRootHash"]),
                    "record_count": int(args["recordCount"]),
                    "last_sequence_number": int(args["lastSequenceNumber"]),
                    "submitter": Web3.to_checksum_address(args["submitter"]),
                    "timestamp": _iso_from_unix(int(args["timestamp"])),
                }
            )

        return {
            "tx_hash": _format_hex(receipt.transactionHash),
            "status": "confirmed" if int(receipt.status) == 1 else "reverted",
            "status_code": int(receipt.status),
            "block_number": int(receipt.blockNumber),
            "block_hash": _format_hex(receipt.blockHash),
            "block_timestamp": _iso_from_unix(int(block.timestamp)),
            "from": Web3.to_checksum_address(tx["from"]),
            "to": Web3.to_checksum_address(tx["to"]) if tx.get("to") else None,
            "nonce": int(tx["nonce"]),
            "gas_used": int(receipt.gasUsed),
            "chain_id": int(w3.eth.chain_id),
            "events": verification_events + checkpoint_events,
        }
