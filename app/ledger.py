"""
ledger.py
---------
Implements the "Blockchain Network" block from your diagram: Tamper-Resistant
Record + Smart-Contract-style rules + Distributed Ledger Tech.

For a prototype, a full multi-node blockchain (Ethereum/Hyperledger) is
overkill and hard to demo. Instead this is a SIMULATED single-chain ledger:
each record cryptographically includes the hash of the previous record,
exactly like a real blockchain's block-linking. This gives you the same
core property you need to demonstrate -- "if any past record is edited,
every subsequent hash breaks and the tampering is detectable."

Note for your report: mention this is a simulated/permissioned ledger
standing in for a deployed smart contract (e.g. on Ganache/Ethereum). The
`add_record` / `verify_chain` functions are written so you could later
swap them for real `web3.py` calls to a Solidity contract without changing
any other part of the system.
"""

import json
import hashlib
import os
from datetime import datetime, timezone

LEDGER_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "ledger.json")


def _block_hash(block: dict) -> str:
    """Hash a block's contents (excluding its own hash field)."""
    block_copy = {k: v for k, v in block.items() if k != "block_hash"}
    encoded = json.dumps(block_copy, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _load_chain() -> list:
    if not os.path.exists(LEDGER_PATH):
        return []
    with open(LEDGER_PATH, "r") as f:
        return json.load(f)


def _save_chain(chain: list) -> None:
    os.makedirs(os.path.dirname(LEDGER_PATH), exist_ok=True)
    with open(LEDGER_PATH, "w") as f:
        json.dump(chain, f, indent=2)


def add_record(content_hash: str, perceptual_hash: str, ai_result: dict, filename: str) -> dict:
    """Append a new verification record to the chain, linked to the
    previous block's hash (this is the 'Smart Contract governing
    verification rules' step in your diagram)."""
    chain = _load_chain()
    previous_hash = chain[-1]["block_hash"] if chain else "0" * 64

    block = {
        "index": len(chain),
        "filename": filename,
        "content_hash": content_hash,
        "perceptual_hash": perceptual_hash,
        "ai_result": ai_result,
        "previous_hash": previous_hash,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    block["block_hash"] = _block_hash(block)

    chain.append(block)
    _save_chain(chain)
    return block


def get_record_by_hash(content_hash: str) -> dict | None:
    """Look up a previously verified file by its exact SHA-256 hash. This
    is how a second person checking the same file gets a Hash Match /
    Mismatch verdict."""
    chain = _load_chain()
    for block in chain:
        if block["content_hash"] == content_hash:
            return block
    return None


def get_history() -> list:
    return _load_chain()


def verify_chain_integrity() -> dict:
    """Walks the whole chain and confirms no block has been silently
    edited after the fact -- demonstrates the ledger's tamper-evidence."""
    chain = _load_chain()
    for i, block in enumerate(chain):
        recomputed = _block_hash(block)
        if recomputed != block["block_hash"]:
            return {"valid": False, "broken_at_index": i, "reason": "block hash mismatch"}
        if i > 0 and block["previous_hash"] != chain[i - 1]["block_hash"]:
            return {"valid": False, "broken_at_index": i, "reason": "previous_hash link broken"}
    return {"valid": True, "length": len(chain)}
