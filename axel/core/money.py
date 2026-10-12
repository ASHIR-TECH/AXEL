from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Money is quantised to cents; quantities/prices keep provider precision.
MONEY_QUANTUM = Decimal("0.01")


def to_decimal(value: Decimal | float | str) -> Decimal:
    """Convert ``value`` to an exact ``Decimal`` without binary-float noise."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):  # bool is an int subclass; reject explicitly.
        raise TypeError("Refusing to convert bool to Decimal.")
    if isinstance(value, int):  # int is checked before float; see annotation note.
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        return Decimal(value)
    raise TypeError(f"Cannot convert {type(value).__name__} to Decimal.")


def quantize_money(value: Decimal | float | str) -> Decimal:
    """Quantise a monetary value to cents using banker-safe half-up rounding."""
    try:
        return to_decimal(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:  # pragma: no cover - defensive
        raise ValueError(f"Invalid monetary value: {value!r}") from exc
