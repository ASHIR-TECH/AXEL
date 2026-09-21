"""
ID generation utilities and idempotency key helpers for AXEL.
"""

import hashlib
import uuid


def generate_id(prefix: str = "") -> str:
    """Generate a prefixed UUID4 string."""
    raw = uuid.uuid4().hex
    return f"{prefix}_{raw}" if prefix else raw


def generate_client_order_id(proposal_id: str, attempt: int = 1) -> str:
    """
    Deterministic client order ID for broker idempotency.
    Alpaca and other brokers enforce max length (Alpaca max 128 chars)
    and use it to reject duplicates.
    """
    clean_prop = str(proposal_id).replace("-", "")
    content = f"{clean_prop}:{attempt}".encode()
    order_hash = hashlib.sha256(content).hexdigest()[:16]
    return f"ord_{clean_prop[:12]}_{order_hash}"
