"""Normalization package: canonicalization, validation and quality flagging."""

from __future__ import annotations

from axel.data.normalization.normalize import (
    InvalidRecord,
    flag_record,
    normalize_raw,
    normalize_records,
)

__all__ = ["InvalidRecord", "flag_record", "normalize_raw", "normalize_records"]
