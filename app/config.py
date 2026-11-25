"""
config.py
---------
Centralized configuration for VERITY loaded from environment variables and .env.
Never stores or commits private keys in source code.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=False)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw.strip())


@dataclass(frozen=True)
class VeritySettings:
    environment: str
    public_base_url: str
    rpc_url: str
    network_name: str
    chain_id: int
    contract_address: str
    private_key: str
    solc_version: str
    detector_version: str
    config_id: str
    phash_threshold: int
    ledger_path: Path
    deployment_path: Path
    artifact_path: Path
    auto_anchor: bool
    checkpoint_interval: int
    max_image_bytes: int
    max_video_bytes: int
    max_image_pixels: int
    expose_filenames: bool
    admin_api_key: str

    @property
    def analysis_config_id(self) -> str:
        return f"{self.detector_version}:{self.config_id}"


def load_deployment_metadata(deployment_path: Path | None = None) -> dict:
    path = deployment_path or Path(
        os.getenv("VERITY_DEPLOYMENT_PATH") or (BASE_DIR / "data" / "deployment.json")
    )
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def get_settings() -> VeritySettings:
    deployment_path = Path(
        os.getenv("VERITY_DEPLOYMENT_PATH") or (BASE_DIR / "data" / "deployment.json")
    )
    deployment = load_deployment_metadata(deployment_path)

    default_ledger = (
        Path("/tmp/verity_ledger.json")
        if os.getenv("VERCEL")
        else (BASE_DIR / "data" / "ledger.json")
    )
    ledger_raw = os.getenv("VERITY_LEDGER_PATH") or os.getenv("LEDGER_PATH")
    ledger_path = Path(ledger_raw) if ledger_raw else default_ledger

    artifact_path = BASE_DIR / "contracts" / "artifacts" / "VerityVerification.json"

    contract_address = (
        os.getenv("VERITY_CONTRACT_ADDRESS", "").strip()
        or str(deployment.get("contract_address", "")).strip()
    )
    chain_id_env = os.getenv("VERITY_CHAIN_ID", "").strip()
    chain_id = int(chain_id_env) if chain_id_env else int(deployment.get("chain_id", 1337))

    return VeritySettings(
        environment=os.getenv("VERITY_ENVIRONMENT", "development").strip().lower(),
        public_base_url=os.getenv("VERITY_PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
        rpc_url=os.getenv("VERITY_RPC_URL", "http://127.0.0.1:8545").strip(),
        network_name=os.getenv("VERITY_NETWORK_NAME", "Ganache Local").strip(),
        chain_id=chain_id,
        contract_address=contract_address,
        private_key=os.getenv("VERITY_PRIVATE_KEY", "").strip(),
        solc_version=os.getenv("VERITY_SOLC_VERSION", "0.8.20").strip(),
        detector_version=os.getenv("VERITY_DETECTOR_VERSION", "heuristic-v2.0.0").strip(),
        config_id=os.getenv("VERITY_CONFIG_ID", "cfg-2026.11-baseline").strip(),
        phash_threshold=_env_int("VERITY_PHASH_THRESHOLD", 10),
        ledger_path=ledger_path,
        deployment_path=deployment_path,
        artifact_path=artifact_path,
        auto_anchor=_env_bool("VERITY_AUTO_ANCHOR", True),
        checkpoint_interval=max(1, _env_int("VERITY_CHECKPOINT_INTERVAL", 5)),
        max_image_bytes=_env_int("VERITY_MAX_IMAGE_MB", 25) * 1024 * 1024,
        max_video_bytes=_env_int("VERITY_MAX_VIDEO_MB", 100) * 1024 * 1024,
        max_image_pixels=_env_int("VERITY_MAX_IMAGE_PIXELS", 40_000_000),
        expose_filenames=_env_bool("VERITY_EXPOSE_FILENAMES", False),
        admin_api_key=os.getenv("VERITY_ADMIN_API_KEY", "").strip(),
    )
