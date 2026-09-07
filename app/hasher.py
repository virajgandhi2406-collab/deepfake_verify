"""
hasher.py
---------
Implements the "Digital Fingerprint (Cryptographic Hash)" block from your
diagram. Computes a SHA-256 hash of the raw file bytes, and a perceptual
hash (pHash-style, via simple DCT) so near-identical re-compressions can
still be recognized as "the same content" while byte-level tampering is
caught by the SHA-256 mismatch.
"""

import hashlib
from PIL import Image
import numpy as np


def sha256_of_file(file_path: str) -> str:
    """Exact-match cryptographic hash. Any single-bit change in the file
    changes this completely -- this is what proves tampering."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def perceptual_hash(file_path: str, hash_size: int = 8) -> str:
    """Simple average-hash perceptual fingerprint: robust to resizing/
    re-compression, used to detect 'is this basically the same image'
    even if the exact bytes differ slightly."""
    image = Image.open(file_path).convert("L").resize(
        (hash_size, hash_size), Image.LANCZOS
    )
    pixels = np.asarray(image, dtype=np.float32)
    avg = pixels.mean()
    bits = (pixels > avg).flatten()
    hash_int = 0
    for bit in bits:
        hash_int = (hash_int << 1) | int(bit)
    return format(hash_int, f"0{hash_size * hash_size // 4}x")
