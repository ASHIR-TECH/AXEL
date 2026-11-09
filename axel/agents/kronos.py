"""Proposal-only adapter for a fine-tuned Kronos forecast artifact.

This module turns model output into ``SignalModel`` rows. It has no broker,
risk or execution import and cannot size, approve or submit anything. The
deterministic core remains the only authority for capital.

The training side produces the artifact described in
``axel/training/artifact.py``; the GPU work plugs its predictor into
``KronosForecastAnalyst`` and nothing else.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from sqlalchemy.orm import Session

from axel.core.ids import generate_id
from axel.db.models import SignalModel
from axel.training.artifact import ModelArtifact, load_artifact

ANALYST_NAME = "KRONOS"
FEATURES_REF_LIMIT = 128


@dataclass(frozen=True)
class Forecast:
    """One model prediction for one symbol at one decision time."""

    symbol: str
    as_of: datetime
    expected_return: float
    probability_up: float
    horizon_bars: int
    quantiles: tuple[tuple[float, float], ...] = ()

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        if not 0.0 <= self.probability_up <= 1.0:
            raise ValueError("probability_up must be in [0, 1]")
        if self.horizon_bars < 1:
            raise ValueError("horizon_bars must be positive")


class Predictor(Protocol):
    """The single integration point for the trained model."""

    def predict(self, symbol: str, closes: list[float], as_of: datetime) -> Forecast: ...


@dataclass(frozen=True)
class KronosForecastAnalyst:
    """Wrap a trained predictor and emit proposal-only signals."""

    artifact: ModelArtifact
    predictor: Predictor

    def __post_init__(self) -> None:
        if self.artifact.task != "forecast":
            raise ValueError(f"artifact task must be 'forecast', got {self.artifact.task!r}")

    def forecast(self, symbol: str, closes: list[float], as_of: datetime) -> Forecast:
        if not closes:
            raise ValueError("at least one close is required")
        forecast = self.predictor.predict(symbol, closes, as_of)
        if forecast.symbol != symbol:
            raise ValueError(
                f"predictor returned {forecast.symbol!r} for requested symbol {symbol!r}"
            )
        if forecast.horizon_bars != self.artifact.horizon_bars:
            raise ValueError(
                f"predictor horizon {forecast.horizon_bars} does not match "
                f"artifact horizon {self.artifact.horizon_bars}"
            )
        return forecast

    @staticmethod
    def direction(forecast: Forecast) -> str:
        if forecast.expected_return > 0:
            return "LONG"
        if forecast.expected_return < 0:
            return "SHORT"
        return "NEUTRAL"

    @staticmethod
    def strength(forecast: Forecast) -> float:
        """Confidence in [0, 1]: distance of the up-probability from a coin flip."""
        return round(min(1.0, abs(2.0 * forecast.probability_up - 1.0)), 4)

    def to_signal(self, forecast: Forecast) -> SignalModel:
        payload = {
            "m": self.artifact.model_id,
            "v": self.artifact.version,
            "r": round(forecast.expected_return, 6),
            "p": round(forecast.probability_up, 4),
        }
        features_ref = json.dumps(payload, separators=(",", ":"))[:FEATURES_REF_LIMIT]
        return SignalModel(
            id=generate_id("sig"),
            ts=forecast.as_of,
            as_of=forecast.as_of,
            section=self.artifact.section,
            symbol=forecast.symbol,
            analyst=ANALYST_NAME,
            direction=self.direction(forecast),
            strength=self.strength(forecast),
            horizon=f"{forecast.horizon_bars}b",
            features_ref=features_ref,
        )

    def emit(self, session: Session, forecast: Forecast) -> SignalModel:
        """Persist one signal. This is the only side effect the adapter has."""
        row = self.to_signal(forecast)
        session.add(row)
        return row


@dataclass(frozen=True)
class StubPredictor:
    """Deterministic predictor used by tests and dry runs."""

    expected_return: float = 0.0
    probability_up: float = 0.5
    horizon_bars: int = 1

    def predict(self, symbol: str, closes: list[float], as_of: datetime) -> Forecast:
        return Forecast(
            symbol=symbol,
            as_of=as_of,
            expected_return=self.expected_return,
            probability_up=self.probability_up,
            horizon_bars=self.horizon_bars,
        )


def load_analyst(directory: Path, predictor: Predictor) -> KronosForecastAnalyst:
    """Load an artifact directory and bind it to a predictor."""
    return KronosForecastAnalyst(artifact=load_artifact(directory), predictor=predictor)


__all__ = [
    "ANALYST_NAME",
    "FEATURES_REF_LIMIT",
    "Forecast",
    "KronosForecastAnalyst",
    "Predictor",
    "StubPredictor",
    "load_analyst",
]
