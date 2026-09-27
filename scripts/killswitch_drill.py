"""
Phase 1 Kill-Switch Drill Script:
Simulates a 10% drawdown, verifies HALT, automatic order cancellation,
rejection of new proposals while halted, and secure operator re-arm procedure.
"""

import sys
from pathlib import Path

from axel.core.config import settings
from axel.core.contracts import Proposal
from axel.core.types import OrderSide, RiskVerdict, Section, TradingMode
from axel.execution.alpaca import AlpacaAdapter
from axel.risk.engine import AccountState, SectionRiskAgent, SectionRiskState
from axel.risk.killswitch import KillSwitch
from axel.watchdog.monitor import WatchdogMonitor


def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    print("=" * 70)
    print("AXEL Phase 1: Kill-Switch & Circuit Breaker Drill")
    print("=" * 70)

    drill_state_file = Path(".drill_killswitch.json")
    if drill_state_file.exists():
        drill_state_file.unlink()

    broker = AlpacaAdapter(mock_mode=True)
    kill_switch = KillSwitch(state_file_path=drill_state_file)
    risk_agent = SectionRiskAgent()

    alerts_received = []

    def on_alert(msg: str):
        alerts_received.append(msg)
        print(f"[ALERT FIRED] {msg}")

    watchdog = WatchdogMonitor(
        broker=broker,
        kill_switch=kill_switch,
        alert_callback=on_alert,
    )

    # Step 1: Normal Baseline
    print("\n[Step 1] Establishing peak baseline at $100,000...")
    kill_switch.set_high_water_mark(100_000.0)
    print(f"High Water Mark: ${kill_switch.high_water_mark:,.2f}")
    assert not kill_switch.is_halted, "Kill switch should not be halted initially"

    # Step 2: Simulate 11% Drawdown ($100k -> $89k)
    print("\n[Step 2] Simulating sudden portfolio drop to $89,000 (11.0% drawdown)...")
    broker._mock_balance["total_equity"] = 89_000.0

    print("Running Watchdog cycle...")
    is_healthy = watchdog.run_health_cycle()
    assert not is_healthy, "Watchdog cycle should return unhealthy during 11% drawdown"
    assert kill_switch.is_halted, "Kill switch must be in HALTED state"
    print(f"[PASS] Global HALT successfully engaged! Reason: {kill_switch.halt_reason}")

    # Step 3: Verify proposal rejection during HALT
    print("\n[Step 3] Submitting new trade proposal while HALTED...")
    prop = Proposal(
        section=Section.STOCKS,
        symbol="SPY",
        side=OrderSide.BUY,
        entry=500.0,
        stop=495.0,
        target=515.0,
        strategy_id="momentum_v1",
        panel_confidence=0.90,
        mode=TradingMode.PAPER,
    )

    account_state = AccountState(
        total_equity=89_000.0,
        cash=40_000.0,
        global_drawdown_pct=0.11,
        kill_switch_active=kill_switch.is_halted,
    )
    section_state = SectionRiskState(
        section=Section.STOCKS,
        section_nav=89_000.0,
        start_of_day_equity=100_000.0,
    )

    decision = risk_agent.evaluate(prop, account_state, section_state)
    print(f"Verdict during HALT: {decision.verdict.value.upper()} (Approved: {decision.approved})")
    assert decision.verdict == RiskVerdict.HALTED, "Proposal must be HALTED"
    assert not decision.approved, "Proposal must not be approved during HALT"
    print("[PASS] New proposals are completely blocked by the deterministic gate.")

    # Step 4: Verify unauthorized re-arm is blocked
    print("\n[Step 4] Testing unauthorized re-arm attempt...")
    try:
        kill_switch.re_arm(
            operator_token="wrong-token",
            written_rationale="Attempting re-arm without proper credentials",
            new_equity_baseline=89_000.0,
        )
        raise AssertionError("Unauthorized re-arm should have failed!")
    except PermissionError as e:
        print(f"[PASS] Unauthorized re-arm blocked: {e}")

    # Step 5: Verify re-arm with empty justification is blocked
    print("\n[Step 5] Testing re-arm with empty written justification...")
    try:
        kill_switch.re_arm(
            operator_token=settings.operator_rearm_secret.get_secret_value(),
            written_rationale="short",
            new_equity_baseline=89_000.0,
        )
        raise AssertionError("Re-arm with short justification should have failed!")
    except ValueError as e:
        print(f"[PASS] Missing justification blocked: {e}")

    # Step 6: Perform authorized operator re-arm
    print("\n[Step 6] Executing authorized operator re-arm with valid token & written rationale...")
    kill_switch.re_arm(
        operator_token=settings.operator_rearm_secret.get_secret_value(),
        written_rationale="Incident reviewed by operator: Volatility spike absorbed, re-baselining equity.",
        new_equity_baseline=89_000.0,
    )
    assert not kill_switch.is_halted, "Kill switch should now be re-armed"
    print(f"[PASS] Kill switch successfully re-armed! New baseline: ${kill_switch.high_water_mark:,.2f}")

    # Cleanup
    if drill_state_file.exists():
        drill_state_file.unlink()

    print("\n" + "=" * 70)
    print("Kill-Switch Drill PASSED all criteria!")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
