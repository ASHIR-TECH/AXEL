/* ═══════════════════════════════════════════════════════════════
   AXEL TERMINAL · sim/scheduler.js — tick loop + single-flight
   proposal pipeline. start() is the only entry point.
   ═══════════════════════════════════════════════════════════════ */

import { S, log, alert, rnd, sleep } from "./state.js";
import { initQuotes, tickQuotes, tickEquity, tickWatchdog } from "./market.js";
import { tickSignals, makeProposal, evaluate, tickChatter } from "./agents.js";

async function proposalLoop() {
  while (true) {
    if (!S.paused && !S.halted) {
      const p = makeProposal();
      if (p) { await evaluate(p); await sleep(rnd(400, 900) / S.speed); }
      else await sleep(1500);
    } else await sleep(400);
  }
}

export function start() {
  initQuotes(); tickSignals(); tickQuotes();
  setInterval(() => !S.paused && tickQuotes(), 1500);
  setInterval(() => !S.paused && tickSignals(), 2400);
  setInterval(() => !S.paused && tickEquity(), 1000);
  setInterval(() => !S.paused && tickWatchdog(), 5200);
  setInterval(() => !S.paused && tickChatter(), 4300);
  proposalLoop();
  log("INFO", "axel.core.config", "AxelSettings validated · ENVIRONMENT=paper · ALPACA_PAPER=true · double safety lock OK", "SYS");
  log("INFO", "axel.main", "trading core started · boundary lint PASS · watchdog detached · mode=PAPER", "SYS");
  alert("OK", "Terminal session started in PAPER mode");
}
