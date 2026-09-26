/* ═══════════════════════════════════════════════════════════════
   AXEL TERMINAL · sim/agents.js — analyst signals, proposals,
   the 7-check deterministic risk gate (axel/risk/engine.py order),
   order FSM lifecycle and holdings/P&L.
   ═══════════════════════════════════════════════════════════════ */

import { SECTIONS, LIMITS, ANALYSTS } from "../config.js";
import { S, on, emit, log, rnd, pick, chance, ts, sleep, waitResume } from "./state.js";
import { portfolioSnapshot } from "./market.js";

/* ---------- signals (with confidence) ---------- */
export function tickSignals() {
  for (const a of ANALYSTS) for (const sec in SECTIONS) {
    const key = a + "|" + sec;
    let cur = S.sig[key] || { d: "N", s: 0, conf: 0 };
    if (cur.d !== "N" && chance(.35)) cur = { d: "N", s: 0, conf: 0 };
    else if (chance(.10)) {
      cur = { d: pick(["L","S"]), s: Math.round(rnd(1,3)), conf: rnd(.31,.97) };
      if (cur.s === 3) log("INFO", "axel.agents.analyst", `${a.toLowerCase()} → SIGNAL ${sec}/${cur.d} strength=${cur.s} conf=${cur.conf.toFixed(2)}`, "ANALYSIS");
      else if (chance(.25)) log("CHAT", "axel.agents.analyst", `[${a.toLowerCase()}] ${cur.d} ${sec}: ${pick(["momentum accelerating","sentiment skew positive","funding dislocated","IV > RV by 6pts","odds lagging news","carry still paid","breakout on volume","macro regime supportive"])}`, "CHAT");
    }
    S.sig[key] = cur;
  }
  emit("signals", S.sig);
}

/* ---------- proposal + risk ---------- */
export function makeProposal() {
  if (S.halted) return null;
  const sec = pick(Object.keys(SECTIONS));
  const cfg = SECTIONS[sec];
  const price = S.quotePx[cfg.syms[0]]?.price ?? rnd(20, 400);
  const entry = price * rnd(.98, 1.02);
  const dir = pick(["LONG", "SHORT"]);
  const stopDist = rnd(.004, .035);
  const stop  = dir === "LONG" ? entry * (1 - stopDist) : entry * (1 + stopDist);
  const rr    = rnd(.6, 4.6);
  const target = dir === "LONG" ? entry + (entry - stop) * rr : entry - (stop - entry) * rr;
  return { id: "P-" + (++S.seq), section: sec, symbol: pick(cfg.syms), side: dir, entry, stop, target, rr,
    conf: Math.round(rnd(.12, .93) * 100) / 100, horizon: pick(["4H","1D","3D","1W"]),
    strategy: pick(["MOM_12-1","LOW_BETA","PAIRS_MR","VRP_CC","FUND_ARB","CARRY_ADJ","ODDS_ARB","ONCHAIN_FLOW"]),
    corr: Math.min(.97, S.sections[sec].corr + rnd(-.15,.2)), at: ts() };
}

export async function evaluate(p) {
  const st = S.sections[p.section];
  const dd = (S.hwm - S.equity) / S.hwm;
  const checks = [
    { k: "kill", pass: !(dd >= LIMITS.GLOBAL_MAX_DRAWDOWN || S.halted), why: `drawdown_vs_hwm=${(dd*100).toFixed(2)}% limit=10%` },
    { k: "stop", pass: st.dayPnl > -LIMITS.SECTION_DAILY_STOP, why: `${p.section}_pnl_day=${(st.dayPnl*100).toFixed(2)}% stop=-3%` },
    { k: "conf", pass: p.conf >= LIMITS.MIN_PANEL_CONFIDENCE, why: `panel_confidence=${p.conf.toFixed(2)} min=0.30` },
    { k: "pos",  pass: st.positions < LIMITS.MAX_OPEN_POSITIONS, why: `open_positions=${st.positions} max=10` },
    { k: "rr",   pass: p.rr >= LIMITS.MIN_RR_RATIO && p.rr <= LIMITS.MAX_EXPECTED_R, why: `reward_risk=${p.rr.toFixed(2)} min=1.5 max=4.0` },
    { k: "corr", pass: p.corr < LIMITS.CORR_BLOCK, why: `max_correlation=${p.corr.toFixed(2)} block≥0.85` },
    { k: "kelly", pass: chance(.92), why: `kelly_f*=${rnd(.01,.22).toFixed(3)} cap_binding=${pick(["NOTIONAL_CAP_5PCT","RISK_AT_STOP_CAP","KELLY_FRACTION"])}` },
  ];
  S.proposalsEvaluated++;
  emit("risk:start", p);
  log("INFO", "axel.db.proposals", `INSERT proposal ${p.id} · ${p.section} ${p.side} ${p.symbol} conf=${p.conf.toFixed(2)} → row written (typed record crosses boundary)`, "DB");
  if (chance(.5)) log("CHAT", "axel.agents.panel", `[expert panel] debated ${p.symbol}: ${pick(["bull case carries the room","dissent noted — size reduced","crowding check passed","risk/reward survives haircut","weakest leg is the macro analyst"])} → conf ${p.conf.toFixed(2)}`, "CHAT");

  let verdict = "APPROVED", failed = [];
  for (const c of checks) {
    await sleep(S.halted ? 400 : 320 / S.speed);
    if (S.paused) await waitResume();
    emit("risk:check", { p, c });
    if (!c.pass) {
      verdict = S.halted ? "HALTED" : "REJECTED";
      failed.push(c.k);
      log(verdict === "HALTED" ? "CRITICAL" : "VETO", "axel.risk.engine",
          `${verdict} ${p.id} ${p.section}/${p.symbol} check=${c.k} :: ${c.why}`, "RISK");
      break;
    }
  }
  emit("risk:done", { p, verdict, failed, checks });
  S.ops.llmSpend = Math.min(S.ops.llmBudget, S.ops.llmSpend + rnd(.02, .18));

  if (verdict === "APPROVED") {
    const qty = Math.max(1, Math.floor(rnd(.02, .05) * st.nav / p.entry));
    log("APPROVED", "axel.risk.engine",
        `RiskDecision APPROVED ${p.id} qty=${qty} notional=${(qty*p.entry).toFixed(0)} (5% cap=${(st.nav*.05).toFixed(0)}) → execution unlocked`, "RISK");
    S.approvals.push({ id: "T-" + S.seq, section: p.section, ttl: SECTIONS[p.section].hitlTtl, state: "AUTO_FLOW" });
    if (S.approvals.length > 6) S.approvals.shift();
    emit("approvals", S.approvals);
    openOrder(p, qty);
  }
  return verdict;
}

/* ---------- orders ---------- */
export function openOrder(p, qty) {
  const o = { cid: `axel-${S.seq}-cid`.toLowerCase(), sym: p.symbol, side: p.side === "LONG" ? "BUY" : "SELL",
              sec: p.section, qty, filled: 0, entry: p.entry, state: "NEW", at: Date.now() };
  S.orders.push(o); emit("orders", S.orders);
  setTimeout(() => advance(o), rnd(350, 800) / S.speed);
}

export function advance(o) {
  if (S.halted) { settle(o, "CANCELED"); return; }
  switch (o.state) {
    case "NEW": o.state = "SUBMITTED"; emit("orders", S.orders); setTimeout(() => advance(o), rnd(400, 900) / S.speed); break;
    case "SUBMITTED":
      if (chance(.05)) { log("REJECTED", "axel.execution.alpaca", `${o.cid} broker rejected ${o.sym} — FSM terminal`, "EXEC"); settle(o, "REJECTED"); }
      else if (chance(.3)) { o.state = "PARTIAL"; o.filled = Math.max(1, Math.floor(o.qty * rnd(.2, .8))); emit("orders", S.orders); setTimeout(() => advance(o), rnd(500, 1100) / S.speed); }
      else { o.filled = o.qty; settle(o, "FILLED"); }
      break;
    case "PARTIAL": o.filled = o.qty; settle(o, "FILLED"); break;
  }
}

export function settle(o, state) {
  o.state = state;
  if (state === "FILLED") {
    const px = S.quotePx[o.sym]?.price ?? o.entry;
    log("FILLED", "axel.execution.reconcile", `fill ${o.cid} ${o.side} ${o.qty}@${px.toFixed(2)} slip=${rnd(-.04,.11).toFixed(3)}% · broker⇄DB reconciled drift=0`, "EXEC");
    addHolding(o, px);
    S.sections[o.sec].positions = Math.min(12, S.sections[o.sec].positions + 1);
  } else if (state === "CANCELED") {
    log("HALT", "axel.execution.broker", `${o.cid} ${o.sym} CANCELED by kill-switch policy`, "EXEC");
  }
  emit("orders", S.orders);
  setTimeout(() => { const i = S.orders.indexOf(o); if (i >= 0) { S.orders.splice(i, 1); emit("orders", S.orders); } }, 14000);
}

// kill-switch policy: on HALT, cancel every live order through the FSM
on("halt:live-orders", () => {
  for (const o of S.orders) if (["NEW","SUBMITTED","PARTIAL"].includes(o.state)) settle(o, "CANCELED");
});

/* ---------- holdings / portfolio ---------- */
export function addHolding(o, px) {
  const h = { sym: o.sym, sec: o.sec, side: o.side === "BUY" ? "L" : "S", qty: o.qty, entry: px, last: px, id: ++S.seq };
  S.holdings.push(h);
  setTimeout(() => closeHolding(h), rnd(25000, 70000));
  emitPortfolio();
}
export function closeHolding(h) {
  const i = S.holdings.indexOf(h); if (i < 0) return;
  S.holdings.splice(i, 1);
  const move = (h.last - h.entry) / h.entry * (h.side === "L" ? 1 : -1);
  const pnl = h.qty * h.entry * move;
  S.realized += pnl; S.trades++;
  if (pnl >= 0) S.wins++; else S.losses++;
  const st = S.sections[h.sec];
  st.nav += pnl; st.dayPnl += pnl / st.start;
  log(pnl >= 0 ? "APPROVED" : "VETO", "axel.db.fills", `EXIT ${h.sec}/${h.sym} ${h.side} ${h.qty} @${h.last.toFixed(2)} realized_pnl=${pnl >= 0 ? "+" : ""}${pnl.toFixed(2)} (${(move*100).toFixed(2)}%)`, "DB");
  emitPortfolio();
}
export function emitPortfolio() { emit("portfolio", portfolioSnapshot()); }

/* ---------- overseer / allocator / research chatter ---------- */
export function tickChatter() {
  if (chance(.25) && !S.halted) {
    const from = pick(Object.keys(SECTIONS)), to = pick(Object.keys(SECTIONS));
    if (from !== to) {
      const want = rnd(.01, .09), capped = Math.min(want, LIMITS.MAX_REALLOC);
      log(want > capped ? "VETO" : "INFO", "axel.risk.allocator",
          `AllocationProposal ${from}→${to} requested=${(want*100).toFixed(1)}% ${want > capped ? "CLAMPED to 5%/cycle" : "within cap"}`, "RISK");
    }
  }
  if (chance(.18)) log("CHAT", "axel.agents.axel", `[axel] overseer cycle · section scores ${Object.keys(SECTIONS).map(s => `${s.toLowerCase()}:${rnd(.3,.9).toFixed(2)}`).join(" ")}`, "CHAT");
  if (chance(.15)) log("INFO", "axel.data.ingest", `bars INSERT source=${pick(["alpaca","polygon","fred","edgar","gdelt","kalshi"])} point_in_time=true`, "DB");
  if (chance(.12)) log("INFO", "axel.agents.qr", `[quant-research] observed ${pick(["slippage drift on CRYPTO","panel calibration +0.02","momentum decay in low-beta","funding spread widening"])} → drafted PR #${10+(S.seq%40)}`, "ANALYSIS");
  S.ops.llmSpend = Math.min(S.ops.llmBudget, S.ops.llmSpend + rnd(.01, .09));
}
