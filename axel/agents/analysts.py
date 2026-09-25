"""Schema-first Stocks analysts. Input text is data, never an instruction."""

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from axel.core.contracts import Signal
from axel.core.types import Direction, Section


@dataclass(frozen=True)
class UntrustedDocument:
    source: str
    text: str
    published_at: datetime

    def reference(self) -> str:
        return sha256(f"{self.source}\0{self.text}".encode()).hexdigest()


class StocksAnalyst:
    """Deterministic baseline analyst; an LLM adapter may only replace the scoring function."""
    name = "technical"

    def signal(self, symbol: str, current_price: float, moving_average: float, as_of: datetime | None = None) -> Signal:
        if current_price <= 0 or moving_average <= 0:
            raise ValueError("prices must be positive")
        direction = Direction.LONG if current_price >= moving_average else Direction.SHORT
        strength = min(abs(current_price / moving_average - 1) * 10, 1.0)
        return Signal(section=Section.STOCKS, symbol=symbol.upper(), analyst=self.name,
                      direction=direction, strength=strength, as_of=as_of or datetime.now(UTC))


def sanitize_document(document: UntrustedDocument) -> dict[str, str]:
    """Preserve provenance and make it explicit that external text cannot issue commands."""
    return {"source": document.source, "content_hash": document.reference(), "untrusted_text": document.text}
