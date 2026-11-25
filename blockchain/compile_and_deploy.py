"""
compile_and_deploy.py
---------------------
Compiles contracts/VerityVerification.sol (using py-solc-x if available or
Node solc@0.8.20 standard-JSON compiler), saves ABI/bytecode artifacts, and
deploys to an Ethereum-compatible RPC node (e.g. Ganache).

Usage:
  python -m blockchain.compile_and_deploy --compile-only
  python -m blockchain.compile_and_deploy --rpc-url http://127.0.0.1:8545
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

from dotenv import load_dotenv
from web3 import Web3

BASE_DIR = Path(__file__).resolve().parent.parent
CONTRACT_SOURCE_PATH = BASE_DIR / "contracts" / "VerityVerification.sol"
ARTIFACT_PATH = BASE_DIR / "contracts" / "artifacts" / "VerityVerification.json"
DEFAULT_DEPLOYMENT_PATH = BASE_DIR / "data" / "deployment.json"


def _compile_with_solcx(input_json: dict[str, Any], solc_version: str) -> dict[str, Any] | None:
    try:
        import solcx

        installed = [str(v) for v in solcx.get_installed_solc_versions()]
        if solc_version not in installed:
            return None
        return solcx.compile_standard(input_json, solc_version=solc_version)
    except Exception:
        return None


def _find_node_solc_path() -> str | None:
    candidates = [
        BASE_DIR / "node_modules" / "solc" / "index.js",
        Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "solc" / "index.js",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate).replace("\\", "/")
    return None


def _compile_with_node_solc(input_json: dict[str, Any]) -> dict[str, Any]:
    node_bin = shutil.which("node")
    if not node_bin:
        raise RuntimeError("Neither installed solcx nor Node.js solc was found.")

    solc_module = _find_node_solc_path() or "solc"
    js_runner = (
        "const fs=require('fs');"
        f"const solc=require({json.dumps(solc_module)});"
        "const input=fs.readFileSync(0,'utf8');"
        "process.stdout.write(solc.compile(input));"
    )
    proc = subprocess.run(
        [node_bin, "-e", js_runner],
        input=json.dumps(input_json),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Node solc compilation failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def compile_contract(
    solc_version: str = "0.8.20",
    source_path: Path = CONTRACT_SOURCE_PATH,
    artifact_path: Path = ARTIFACT_PATH,
) -> dict[str, Any]:
    source_text = source_path.read_text(encoding="utf-8")
    source_sha256 = hashlib.sha256(source_text.encode("utf-8")).hexdigest()

    input_json = {
        "language": "Solidity",
        "sources": {
            "VerityVerification.sol": {"content": source_text},
        },
        "settings": {
            "optimizer": {"enabled": True, "runs": 200},
            "viaIR": True,
            "evmVersion": "paris",
            "outputSelection": {
                "*": {
                    "*": [
                        "abi",
                        "metadata",
                        "evm.bytecode.object",
                        "evm.deployedBytecode.object",
                    ]
                }
            },
        },
    }

    compiled = _compile_with_solcx(input_json, solc_version)
    compiler_backend = f"py-solc-x ({solc_version})"
    if compiled is None:
        compiled = _compile_with_node_solc(input_json)
        compiler_backend = f"solcjs ({solc_version})"

    errors = [
        err for err in compiled.get("errors", []) if err.get("severity") == "error"
    ]
    if errors:
        messages = "; ".join(err.get("formattedMessage", err.get("message", "")) for err in errors)
        raise RuntimeError(f"Solidity compilation errors: {messages}")

    contract_data = compiled["contracts"]["VerityVerification.sol"]["VerityVerification"]
    abi = contract_data["abi"]
    bytecode = contract_data["evm"]["bytecode"]["object"]
    if not bytecode:
        raise RuntimeError("Solidity compilation produced empty bytecode.")

    artifact = {
        "contract_name": "VerityVerification",
        "solc_version": solc_version,
        "compiler_backend": compiler_backend,
        "evm_version": "paris",
        "source_path": str(source_path.relative_to(BASE_DIR)).replace("\\", "/"),
        "source_sha256": source_sha256,
        "compiled_at": datetime.now(timezone.utc).isoformat(),
        "abi": abi,
        "bytecode": bytecode,
    }
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    return artifact


def load_or_compile_artifact(
    solc_version: str = "0.8.20",
    artifact_path: Path = ARTIFACT_PATH,
) -> dict[str, Any]:
    if artifact_path.exists():
        return json.loads(artifact_path.read_text(encoding="utf-8"))
    return compile_contract(solc_version=solc_version, artifact_path=artifact_path)


def deploy_contract(
    rpc_url: str,
    private_key: str = "",
    solc_version: str = "0.8.20",
    network_name: str = "Ganache Local",
    deployment_path: Path | None = DEFAULT_DEPLOYMENT_PATH,
) -> dict[str, Any]:
    artifact = compile_contract(solc_version=solc_version)
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))
    if not w3.is_connected():
        raise ConnectionError(f"Unable to connect to EVM RPC node at {rpc_url}")

    chain_id = int(w3.eth.chain_id)
    contract_factory = w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"])

    if private_key:
        account = w3.eth.account.from_key(private_key)
        deployer_address = account.address
        nonce = w3.eth.get_transaction_count(deployer_address, "pending")
        tx = contract_factory.constructor().build_transaction(
            {
                "from": deployer_address,
                "nonce": nonce,
                "chainId": chain_id,
                "gas": 3_500_000,
                "gasPrice": w3.eth.gas_price,
            }
        )
        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    else:
        accounts = w3.eth.accounts
        if not accounts:
            raise ValueError(
                "No private key supplied and EVM node exposes no unlocked accounts."
            )
        deployer_address = accounts[0]
        tx_hash = contract_factory.constructor().transact(
            {"from": deployer_address, "gas": 3_500_000}
        )

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    if receipt.status != 1 or not receipt.contractAddress:
        raise RuntimeError(f"Contract deployment transaction failed: {tx_hash.hex()}")

    raw_tx_hex = receipt.transactionHash.hex()
    tx_hex = raw_tx_hex if raw_tx_hex.startswith("0x") else f"0x{raw_tx_hex}"

    deployment_info = {
        "contract_name": "VerityVerification",
        "contract_address": Web3.to_checksum_address(receipt.contractAddress),
        "network_name": network_name,
        "rpc_url": rpc_url,
        "chain_id": chain_id,
        "deployer_address": Web3.to_checksum_address(deployer_address),
        "deployment_tx_hash": tx_hex,
        "deployment_block_number": int(receipt.blockNumber),
        "gas_used": int(receipt.gasUsed),
        "solc_version": solc_version,
        "compiler_backend": artifact["compiler_backend"],
        "source_sha256": artifact["source_sha256"],
        "deployed_at": datetime.now(timezone.utc).isoformat(),
    }

    if deployment_path is not None:
        deployment_path.parent.mkdir(parents=True, exist_ok=True)
        deployment_path.write_text(
            json.dumps(deployment_info, indent=2), encoding="utf-8"
        )

    return deployment_info


def main() -> None:
    load_dotenv(BASE_DIR / ".env", override=False)
    parser = argparse.ArgumentParser(description="Compile and deploy VerityVerification.sol")
    parser.add_argument("--compile-only", action="store_true", help="Compile without deploying")
    parser.add_argument(
        "--rpc-url",
        default=os.getenv("VERITY_RPC_URL", "http://127.0.0.1:8545"),
        help="Ethereum-compatible RPC endpoint",
    )
    parser.add_argument(
        "--network-name",
        default=os.getenv("VERITY_NETWORK_NAME", "Ganache Local"),
        help="Human-readable network label",
    )
    parser.add_argument(
        "--solc-version",
        default=os.getenv("VERITY_SOLC_VERSION", "0.8.20"),
        help="Solidity compiler version",
    )
    args = parser.parse_args()

    if args.compile_only:
        artifact = compile_contract(solc_version=args.solc_version)
        print(
            json.dumps(
                {
                    "status": "compiled",
                    "contract_name": artifact["contract_name"],
                    "solc_version": artifact["solc_version"],
                    "compiler_backend": artifact["compiler_backend"],
                    "source_sha256": artifact["source_sha256"],
                    "artifact_path": str(ARTIFACT_PATH),
                },
                indent=2,
            )
        )
        return

    private_key = os.getenv("VERITY_PRIVATE_KEY", "").strip()
    deployment = deploy_contract(
        rpc_url=args.rpc_url,
        private_key=private_key,
        solc_version=args.solc_version,
        network_name=args.network_name,
    )
    print(json.dumps(deployment, indent=2))


if __name__ == "__main__":
    main()
