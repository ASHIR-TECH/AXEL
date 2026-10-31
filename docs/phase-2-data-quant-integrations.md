# Phase 2 Extensions — Cross-Asset Data, PIT Gates, PBO and Execution Realism

Status: **implemented and tested** (`894 passed`, `ruff` clean, no-LLM boundary clean).
Spec: `phases/AXEL_DEVELOPER_1_DATA_QUANT_ASSET_INTEGRATIONS.md`.

This document describes the Developer 1 data/quant slice added on top of the
Phase 2 substrate. It extends the existing ingestion, normalization, feature,
backtesting and validation modules; it does not replace them and it does not
place orders.

---

## 1. Canonical cross-asset identity

A stock, a perpetual future, a dated option and a binary prediction contract
are not interchangeable. `axel/data/instruments.py` gives each one a stable
identity and the metadata needed to decide whether an order can be represented
at all.

```python
from datetime import UTC, datetime
from axel.data.instruments import (
    AssetClass, ContractType, make_instrument_id,
    instrument_symbol, instrument_venue,
)

btc_perp = make_instrument_id(
    AssetClass.CRYPTO, "binance", "BTCUSDT",
    contract_type=ContractType.PERPETUAL,
)
# crypto:binance:BTCUSDT:perpetual

call = make_instrument_id(
    AssetClass.OPTION, "deribit", "BTC-20261231-100000-C",
    contract_type=ContractType.OPTION,
    expiry=datetime(2026, 12, 31, 8, tzinfo=UTC),
    strike=100_000.0,
    option_type="call",
)
# option:deribit:BTC-20261231-100000-C:option:20261231T0800Z:K100000:C

instrument_symbol(call)   # "BTC-20261231-100000-C"
instrument_venue(call)    # "deribit"
```

Rules:

- The canonical layout is
  `<asset_class>:<venue>:<symbol>[:<contract_type>][:<expiry>][:K<strike>][:C|P]`.
- Provider adapters extract the venue-local symbol with `instrument_symbol()`.
  Never use the last colon-separated segment: option IDs end in `:C`/`:P` and
  perpetual IDs end in `:perpetual`.
- Missing execution metadata is `None`, never guessed.
  `Instrument.require_execution_metadata()` fails closed when a consumer needs
  tick size, lot size or minimum quantity.
- `InstrumentRegistry` rejects two different instruments sharing either a
  canonical ID or a provider identity.
- IDs and instrument metadata are serializable and carry a deterministic
  fingerprint.

---

## 2. Capability-aware provider contract

The new async interface lives in `axel/data/providers/base.py` and coexists
with the original synchronous `Provider` protocol used by Alpaca/FRED/SEC/GDELT.

```python
class Capability(StrEnum):
    INSTRUMENTS = "instruments"
    HISTORICAL_BARS = "historical_bars"
    TRADES = "trades"
    ORDER_BOOK = "order_book"
    QUOTES = "quotes"
    FUNDING_RATES = "funding_rates"
    OPEN_INTEREST = "open_interest"
    PREDICTION_MARKETS = "prediction_markets"
    PREDICTION_QUOTES = "prediction_quotes"
    PREDICTION_ORDER_BOOK = "prediction_order_book"
    PREDICTION_TRADES = "prediction_trades"
    PREDICTION_BARS = "prediction_bars"
    HEALTH = "health"
```

Every adapter declares an explicit `ProviderCapabilities` set. Calling an
unsupported method raises `CapabilityError`; it never returns fabricated,
empty or silently approximated data.

| Adapter | Asset class | Capabilities | Notes |
| --- | --- | --- | --- |
| `BinancePublicMarketData` | Crypto spot/perpetual | instruments, bars, trades, order book, funding, open interest, health | Public endpoints only. Spot and USD-M futures base URLs are separate. |
| `OandaMarketData` | FX | instruments, bars, quotes, health | v20 API. Account ID required for account-scoped calls. |
| `DeribitOptionsMarketData` | Options | instruments, bars, trades, order book, quotes, health | Public JSON-RPC only. Parses contract terms into `Instrument`. |
| `KalshiPredictionMarketData` | Binary prediction | instruments, markets, prediction quote/book/trades/bars, health | Public read-only endpoints. No order/portfolio/settlement capability. |

Shared adapter behavior:

- Retries are bounded and only for transport errors, 429 and 5xx.
- Responses are validated before a canonical record is constructed.
- Pagination is bounded (`MAX_PAGES`) and repeated cursors fail closed.
- Venue timestamps are preserved; ingestion time is separate.
- `health_check()` returns `ProviderHealth` and never raises for a failed probe.

### Adapter limitations

- **Binance** is public market data only. It has no quote capability because the
  adapter does not fabricate a top-of-book quote from an unrelated endpoint.
- **OANDA** monthly `1M` candles are rejected rather than approximated: month
  length is not fixed, and lowercasing `1M` would silently request one-minute
  `1m` candles. Use broker rollover data for actual financing; the policy-rate
  differential is not the broker's charge.
- **Deribit** supports a configurable currency (default BTC) and returns only
  option instruments from `get_instruments()`. Trade pagination is inclusive
  and deduplicates by `trade_id`.
- **Kalshi** uses `Prediction*` records, not equity-shaped records. Scalar/range
  markets are skipped by `get_instruments()` because they do not share binary
  YES/NO semantics; `get_markets()` returns all market types. The order-book
  endpoint exposes no venue timestamp, so capture time is used and provenance
  is flagged `capture_time_used`. YES asks are derived as `1 - no_bid` and
  rounded to six decimals to avoid float-noise false crossings.
- No adapter enables live leverage, places orders, or reads private account
  state.

---

## 3. Shared event envelope and replay

`axel/data/events.py` defines one immutable envelope for every point-in-time
fact (instrument, bar, trade, quote, book, funding, macro, filing, news,
prediction quote, research signal).

```python
from axel.data.events import EventEnvelope, InMemoryEventLog, envelope_for_market_record

envelope = envelope_for_market_record(trade, event_type="trade")
log = InMemoryEventLog()
log.append(envelope)          # AppendOutcome.APPENDED
log.append(envelope)          # AppendOutcome.DUPLICATE
```

Guarantees:

- `event_id` is derived from source, event type, instrument, event timestamp and
  payload hash. Reusing an ID with different content raises
  `EventConflictError`.
- `published_timestamp` and `received_timestamp` cannot precede
  `event_timestamp`; `available_timestamp` defaults to
  `max(event_timestamp, published_timestamp)` and cannot precede either.
- Payloads are detached and hashed at construction. The hash is verified on
  `from_dict()`.
- `replay()` orders by availability, receipt and event ID, and can filter by
  time or event type.
- `latest_available(instrument_id, at)` returns only events knowable at `at`.
- `InMemoryEventLog` is for tests and single-process replay. Production must
  persist the same envelope in durable storage.

---

## 4. Fail-closed quality and point-in-time gates

`axel/data/quality.py` returns a `QualityReport`; `report.raise_if_invalid()`
raises `DataQualityError`. There is no "warn and continue" path for errors.

```python
from axel.data.quality import (
    DataQualityPolicy, assert_pit, check_bar_series, check_trade_series,
)

assert_pit(records, decision_time=decision_time)   # raises on look-ahead

report = check_bar_series(
    bars,
    interval_seconds=86_400,
    now=decision_time,
    policy=DataQualityPolicy(max_age_seconds=90_000, max_gap_multiplier=1.5),
    expected_instrument_id=instrument_id,
)
report.raise_if_invalid()
```

Checks include:

- naive timestamps, `available_at < event_time`, future data and look-ahead;
- stale data and timestamp drift against a supplied clock;
- duplicate keys, out-of-order records and gaps in a bar series;
- invalid OHLC, negative volume, crossed/empty books, sequence gaps;
- unknown trade side (configurable as an error), instrument mismatch and schema
  mismatch.

`assert_pit()` is the hard gate for research and backtests: a record with no
`available_at` is an error by default, because PIT safety cannot be proven.

---

## 5. Prediction-market semantics

Binary contracts use dedicated records in `axel/data/prediction.py`:

- `PredictionQuote` — YES/NO bid/ask, last price, volume, open interest, status.
- `PredictionTrade` — executed `yes_price`, with `no_price` exposed as the
  complement, taker side and trade ID.
- `PredictionBar` — OHLCV in probability units.
- `PredictionOrderBook` — YES/NO bids/asks in probability units.
- `PredictionMarketInfo` — rules, status, close/expiry/settlement times,
  result and settlement source.

Why not reuse `BarRecord`/`QuoteTick`? A binary price is a probability in
`[0, 1]`; `0` and `1` are valid prices. The generic records require strictly
positive prices, so reusing them would either reject valid observations or
weaken validation for every other asset class.

Conventions:

- All prices are probabilities; `0.0` and `1.0` are valid.
- A locked book (best bid == best ask) is recorded. A crossed book is rejected.
- `PredictionTrade.yes_price` is always in YES units; `no_price = 1 - yes_price`.
- Settlement is explicit: `PredictionMarketInfo.is_settled` is true only when
  status is `settled` and the result is not `unresolved`.
- A model probability, a market-implied probability and a settled outcome are
  different fields. Do not collapse them.

---

## 6. CSCV / probability of backtest overfitting

`axel/validation/pbo.py` implements exact combinatorially symmetric
cross-validation (CSCV) and PBO.

```python
from axel.validation.pbo import calculate_pbo

report = calculate_pbo(
    returns,                      # T x N: T periods, N candidate strategies
    experiment_id="trend-v3",
    strategy_ids=("fast", "slow", "breakout"),
    n_partitions=16,
    max_splits=None,              # None = all C(16, 8) splits
)
report.pbo
report.is_acceptable(threshold=0.5)
report.to_dict()
```

Rules and outputs:

- `returns[t][n]` must cover the same periods; do not forward-fill missing
  observations.
- All `C(n_partitions, n_partitions/2)` splits are evaluated by default. A
  capped run records that it is not exact.
- PBO is the fraction of splits where the in-sample winner ranks below the
  out-of-sample median.
- The report records partitions, per-split IS/OOS Sharpe, ranks, logits,
  selected strategy, performance decay, warnings, a returns hash and a
  calculation version for reproducibility.
- PBO is evidence, not permission: a low PBO does not promote a strategy by
  itself, and a high PBO must block promotion.

---

## 7. Execution realism in the backtester

The backtester still decides at time `t` using only bars with
`available_at <= t` and fills at the next bar open. Three cost/realism layers
are now explicit.

### Funding (perpetual futures)

Pass canonical `FundingRateObservation` records to `run(funding_rates=...)`.
Funding is applied at the first schedule timestamp at or after `event_time`,
before any execution at that timestamp, so it is charged against the position
actually held. A positive rate means longs pay shorts
(`FundingRateObservation.funding_payment` owns the sign convention).

A funding observation that is not available at its funding event fails closed:
the backtest will not retroactively charge a position that did not hold it.

### Financing and borrow

`FinancingModel` charges annualised basis points on absolute notional using
calendar time (weekends and holidays accrue):

```python
from axel.data.ml import Backtester, FinancingModel

backtester = Backtester(
    financing=FinancingModel(
        long_financing_bps_per_year=250,   # margin debit
        short_borrow_bps_per_year=800,     # stock borrow
    )
)
```

Long and short rates are separate because a broker's borrow fee is not the
policy rate. Accrued carry is attributed to the closing `Trade.carry` and
included in `Trade.pnl`; totals appear in `BacktestResult.funding_paid`,
`financing_paid` and `total_carry`.

### Partial fills and rejections

`ExecutionModel` makes liquidity assumptions explicit:

```python
from axel.data.ml import ExecutionModel

backtester = Backtester(
    execution=ExecutionModel(
        max_participation_rate=0.10,  # at most 10% of bar volume
        min_fill_quantity=1.0,
        allow_partial_fills=True,
        reject_zero_volume=True,
    )
)
```

- A zero-volume bar rejects (`no_liquidity`) by default.
- A request above the participation cap partially fills and records the
  unfilled remainder, or rejects entirely when `allow_partial_fills=False`.
- Fills below `min_fill_quantity` reject (`below_minimum`) instead of rounding
  up.
- `Fill` records `requested_quantity`, `rejected_quantity` and
  `rejection_reason`; `BacktestResult.rejections` carries the full rejection
  log.
- Unfilled remainders are retried naturally on the next bar because the
  strategy re-targets every bar. There is no hidden order queue.

### Documented assumptions

Every `BacktestResult` carries `execution_assumptions`, a tuple of
`(key, description)` pairs covering the decision clock, commission, slippage,
fill model, financing, funding, liquidation, calendar, taxes and settlement.
`dict(result.execution_assumptions)` is directly serializable.

Current limitations, stated rather than hidden:

- Forced terminal liquidation and delisting fills assume full execution at the
  terminal/last price with slippage and commission.
- Taxes are not modelled.
- Settlement is cash with immediate proceeds; there is no T+1 lag.
- The loop is bar-based; it does not model queue position, latency or
  intra-bar order-book evolution.
- Contract expiry is represented in `Instrument`, but the equity-shaped
  backtester does not yet roll expiring derivatives.

### Partial-close accounting fix

Partial position reductions now allocate entry commission, risk and carry by
the fraction of the *position* closed, not the fraction of the *order* size.
Previously a 1% reduction could absorb 100% of the entry cost; a regression test
covers the proportional allocation.

---

## 8. Verification

```bash
/tmp/opencode/axel-venv/bin/pytest -q
/tmp/opencode/axel-venv/bin/ruff check .
/tmp/opencode/axel-venv/bin/python scripts/check_no_llm_imports.py
```

Focused suites:

```bash
pytest tests/data/test_instruments.py
pytest tests/data/test_events.py tests/data/test_quality.py
pytest tests/data/test_binance_provider.py tests/data/test_oanda_provider.py
pytest tests/data/test_deribit_provider.py tests/data/test_kalshi_provider.py
pytest tests/data/test_pbo.py
pytest tests/data/test_backtester_execution.py
```

The architectural boundary check confirms zero LLM imports in
`axel/risk/`, `axel/execution/` and `axel/portfolio/`. All new AI/LLM-facing
work remains at the proposal/research layer; deterministic risk and execution
stay authoritative.
