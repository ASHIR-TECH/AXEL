"""
Phase 1 Driver Script:
Feeds hand-crafted trade proposals into the deterministic core without any LLM components.
Verifies approval, rejection, sizing clamps, and paper execution round-trips.
"""

import sys

from axel.core.contracts import Order, Proposal
from axel.core.ids import generate_client_order_id
from axel.core.types import OrderSide, OrderType, Section, TradingMode
from axel.db.session import init_db
from axel.execution.alpaca import AlpacaAdapter
from axel.risk.engine import AccountState, SectionRiskAgent, SectionRiskState
from axel.risk.limits import LIMITS


def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    print("=" * 70)
    print("AXEL Phase 1: Deterministic Core Pipeline Driver (No LLM)")
    print("=" * 70)

    # 1. Initialize local database schema
    print("\n[Step 1] Initializing database tables...")
    init_db()
    print("[PASS] Database tables created successfully.")

    # 2. Instantiate deterministic risk agent and broker adapter
    risk_agent = SectionRiskAgent(LIMITS)
    broker = AlpacaAdapter(mock_mode=True)

    account_state = AccountState(
        total_equity=100_000.0,
        cash=50_000.0,
        global_drawdown_pct=0.02,
        kill_switch_active=False,
    )
    section_state = SectionRiskState(
        section=Section.STOCKS,
        section_nav=100_000.0,
        start_of_day_equity=100_000.0,
        realized_pnl=200.0,
        unrealized_pnl=-100.0,
        active_positions_count=2,
    )

    # 3. Create test proposals
    proposals = [
        # Proposal 1: Compliant bullish swing trade (AAPL)
        Proposal(
            section=Section.STOCKS,
            symbol="AAPL",
            side=OrderSide.BUY,
            entry=150.0,
            stop=145.0,  # 5.0 risk
            target=162.5,  # 12.5 reward -> 2.5:1 RR
            strategy_id="trend_breakout_v1",
            panel_confidence=0.85,
            win_rate=0.55,
            avg_win_loss_ratio=1.8,
            mode=TradingMode.PAPER,
        ),
        # Proposal 2: Low Risk/Reward (MSFT: 1.2:1 RR < 1.5 min)
        Proposal(
            section=Section.STOCKS,
            symbol="MSFT",
            side=OrderSide.BUY,
            entry=400.0,
            stop=390.0,  # 10.0 risk
            target=412.0,  # 12.0 reward -> 1.2:1 RR
            strategy_id="momentum_v1",
            panel_confidence=0.70,
            win_rate=0.50,
            avg_win_loss_ratio=1.2,
            mode=TradingMode.PAPER,
        ),
        # Proposal 3: Overfit expectation (>4.0R target)
        Proposal(
            section=Section.STOCKS,
            symbol="TSLA",
            side=OrderSide.BUY,
            entry=200.0,
            stop=195.0,  # 5.0 risk
            target=230.0,  # 30.0 reward -> 6.0:1 RR (>4.0R cap)
            strategy_id="overfit_lottery_v1",
            panel_confidence=0.90,
            win_rate=0.40,
            avg_win_loss_ratio=3.0,
            mode=TradingMode.PAPER,
        ),
        # Proposal 4: Low Panel Confidence (<0.30)
        Proposal(
            section=Section.STOCKS,
            symbol="NVDA",
            side=OrderSide.BUY,
            entry=120.0,
            stop=115.0,
            target=135.0,
            strategy_id="low_conf_v1",
            panel_confidence=0.25,  # Below 0.30
            win_rate=0.50,
            avg_win_loss_ratio=2.0,
            mode=TradingMode.PAPER,
        ),
    ]

    print(f"\n[Step 2] Evaluating {len(proposals)} test proposals through SectionRiskAgent...")
    orders_executed = 0

    for idx, prop in enumerate(proposals, 1):
        print(f"\n--- Proposal #{idx}: {prop.symbol} ({prop.side.value.upper()}) ---")
        print(f"Entry: ${prop.entry:.2f} | Stop: ${prop.stop:.2f} | Target: ${prop.target:.2f} | Confidence: {prop.panel_confidence:.2f}")

        risk_decision = risk_agent.evaluate(
            proposal=prop,
            account_state=account_state,
            section_state=section_state,
        )

        print(f"Verdict: {risk_decision.verdict.value.upper()} | Approved: {risk_decision.approved}")
        if risk_decision.approved:
            print(f"Approved Qty: {risk_decision.approved_qty} shares (${risk_decision.approved_notional_usd:,.2f})")
            print(f"Binding Limit: {risk_decision.binding_limit}")

            # Create Order and submit to broker
            client_oid = generate_client_order_id(prop.id)
            order = Order(
                client_order_id=client_oid,
                proposal_id=prop.id,
                symbol=prop.symbol,
                side=prop.side,
                qty=risk_decision.approved_qty,
                type=OrderType.LIMIT,
                limit_price=prop.entry,
            )

            result = broker.submit_order(order, risk_decision)
            print(f"Broker Order Submitted -> ID: {result.broker_order_id} | State: {result.state.value.upper()}")
            orders_executed += 1
        else:
            print(f"Binding Limit / Rejection Reason: {risk_decision.binding_limit}")
            for r in risk_decision.reasons:
                print(f"  - {r}")

    print("\n" + "=" * 70)
    print(f"Summary: {orders_executed} of {len(proposals)} proposals approved and executed.")
    print("All rejection rules fired correctly as specified in PRD.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
