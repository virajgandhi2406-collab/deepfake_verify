"""
certificate.py
---------------
Implements the "Authentication Certificate" block from your diagram:
metadata, ownership, source, verification history bundled into one
shareable record.
"""


def generate_certificate(block: dict) -> dict:
    """Builds a human-readable authentication certificate from a ledger
    block. This is what the Verification Interface displays as the final
    verdict card."""
    ai = block["ai_result"]
    return {
        "certificate_id": block["block_hash"][:16],
        "filename": block["filename"],
        "verdict": ai["authenticity_flag"],
        "deepfake_probability": ai["deepfake_probability"],
        "ai_generated": ai.get("ai_generated", False),
        "media_type": ai.get("media_type", "image"),
        "frames_analyzed": ai.get("frames_analyzed"),
        "duration_seconds": ai.get("duration_seconds"),
        "manipulation_type": ai["manipulation_type"],
        "content_hash_sha256": block["content_hash"],
        "perceptual_hash": block["perceptual_hash"],
        "recorded_at": block["recorded_at"],
        "ledger_index": block["index"],
        "ledger_block_hash": block["block_hash"],
        "previous_block_hash": block["previous_hash"],
    }
