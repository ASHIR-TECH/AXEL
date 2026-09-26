/* ═══════════════════════════════════════════════════════════════
   AXEL TERMINAL · sim/market.js — quotes, equity walk, kill-switch,
   watchdog heartbeat, ingest freshness. Deterministic-core mirror:
   tickEquity enforces the same 3% section stop / 10% peak-to-trough
   rules as axel/risk (loss_manager.py, killswitch.py).
   ═══════════════════════════════════════════════════════════════ */

import { SECTIONS, LIMITS } from "../config.js";
import { S, emit, log, alert, rnd, pick, ts } from "./state.js";

/* ---------- quotes ---------- */
export function initQuotes() {
  for (const [sec, cfg] of Object.entries(SECTIONS))
    for (const sym of cfg.syms.slice(0, sec === "STOCKS" ? 6 : 3))
      S.quotePx[sym] = { price: sec === "STOCKS" ? rnd(60, 900) : sec === "CRYPTO" ? rnd(40, 96000)
                       : sec === "FOREX" ? rnd(.7, 1.55) : rnd(40, 90), chg: rnd(-2.2, 2.6), sec };
}
export function tickQuotes() {
  for (const k in S.quotePx) {
    const q = S.quotePx[k];
    const drift = (Math.random() - .492) * (q.sec === "PREDICTIONS" ? .9 : .55);
    q.price *= 1 + drift / 100; q.chg = Math.max(-9, Math.min(9, q.chg + drift));
  }
  for (const h of S.holdings) { const q = S.quotePx[h.sym]; if (q) h.last = q.price; }
  emit("quotes", S.quotePx);
}

/* ---------- equity walk / kill switch ---------- */
export function tickEquity() {
  for (const [, st] of Object.entries(S.sections)) {
    const shock = S.halted ? 0 : (Math.random() - .485) * .0016;
    const d = st.nav * shock;
    st.nav += d; st.dayPnl += d / st.start;
    st.hist.push(st.nav); if (st.hist.length > 40) st.hist.shift();
    st.corr = Math.max(.15, Math.min(.95, st.corr + (Math.random() - .5) * .06));
  }
  S.equity = Object.values(S.sections).reduce((a, s) => a + s.nav, 0);
  S.hwm = Math.max(S.hwm, S.equity);
  const dd = (S.hwm - S.equity) / S.hwm;
  S.equityHist.push(S.equity); if (S.equityHist.length > 180) S.equityHist.shift();
  emit("equity", { equity: S.equity, hwm: S.hwm, dd });

  for (const [name, st] of Object.entries(S.sections)) {
    const was = st.blocked;
    st.blocked = st.dayPnl <= -LIMITS.SECTION_DAILY_STOP;
    if (st.blocked && !was) {
      log("REJECTED", "axel.risk.loss_manager", `${name} SECTION_DAILY_STOP breach pnl=${(st.dayPnl*100).toFixed(2)}% → BLOCK_NEW_ENTRIES`, "RISK");
      alert("WARN", `${name} daily stop breach — new entries blocked`);
    }
    if (!st.blocked && was) st.dayPnl = Math.max(st.dayPnl, -LIMITS.SECTION_DAILY_STOP * .98);
  }
  if (!S.halted && dd >= LIMITS.GLOBAL_MAX_DRAWDOWN) triggerHalt("peak-to-trough drawdown breach");
  emit("sections", S.sections);
  emit("portfolio", portfolioSnapshot());
}

export function portfolioSnapshot() {
  return { equity: S.equity, realized: S.realized, trades: S.trades, wins: S.wins,
    holdings: S.holdings.map(h => ({ ...h, pnl: (h.last - h.entry) * h.qty * (h.side === "L" ? 1 : -1) })) };
}

export function triggerHalt(reason) {
  if (S.halted) return;
  S.halted = true;
  S.killEvents.unshift({ at: ts(), reason }); if (S.killEvents.length > 4) S.killEvents.pop();
  emit("killEvents", S.killEvents);
  emit("halt", true);
  log("CRITICAL", "axel.watchdog.monitor", "★ GLOBAL HALT ★ kill-switch TRIPPED — state persisted to .axel_killswitch.json", "SYS");
  for (const [name, cfg] of Object.entries(SECTIONS))
    log("HALT", "axel.execution.broker", `HALT policy ${name}: cancel_all_orders + ${cfg.flatten}`, "EXEC");
  // agents.js listens for halt:live-orders and cancels via the FSM (keeps dep direction market ← agents)
  emit("halt:live-orders");
  alert("CRITICAL", "KILL-SWITCH TRIPPED — email + dashboard HALT sent to operator");
}

export function resume() {
  if (!S.halted) return;
  S.halted = false; S.hwm = S.equity;
  log("INFO", "axel.risk.killswitch", `KILL-SWITCH MANUAL RESUME reason="drill review complete" → appended to kill_switch_events (append-only)`, "RISK");
  alert("OK", "System resumed manually — kill-switch event appended");
  emit("halt", false);
}

export function runDrill() {
  if (S.halted) return;
  log("CRITICAL", "axel.scripts.killswitch_drill", "PHASE-1 DRILL — injecting drawdown: equity → −10.4% vs HWM", "SYS");
  const scale = (S.hwm * .896) / S.equity;
  for (const st of Object.values(S.sections)) st.nav *= scale;
  tickEquity();
}

/* ---------- watchdog / ops ---------- */
export function tickWatchdog() {
  log("INFO", "axel.watchdog.monitor", pick([
    "heartbeat OK core=" + (rnd(20,90)|0) + "ms ingest=" + (rnd(20,140)|0) + "ms agent=" + (rnd(60,300)|0) + "ms · equity snapshot INSERT",
    "reconcile_positions: broker⇄DB drift=0 · ghost_orders=0",
    "reconcile_orders: FSM ledger matches broker (" + S.orders.length + " tracked)",
  ]), "SYS");
  for (const src of S.ops.ingest) src.lag = Math.max(.4, src.lag + (Math.random()-.5) * (src.lag * .3 + 1));
  emit("ops", S.ops);
}
