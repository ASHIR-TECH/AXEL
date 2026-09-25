/* ═══════════════════════════════════════════════════════════════
   AXEL TERMINAL · ui/trade.js — page 2 renderers
   [06] order kanban · [07] signal matrix · [08] decision log tape
   [09] comms & HITL approvals · [10] system ops
   ═══════════════════════════════════════════════════════════════ */

import { SECTIONS, ANALYSTS, SECH, PHASES } from "../config.js";
import { S, on } from "../sim/state.js";

const $  = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];

/* ══════════ [06] ORDER KANBAN (diffed) ══════════ */
const kcMap = new Map();
function colFor(state) { return state === "CANCELED" || state === "REJECTED" ? "DEAD" : state; }
export function renderKanban() {
  const cols = Object.fromEntries($$(".kcol").map(c => [c.dataset.st, c.querySelector(".kcards")]));
  const live = new Set();
  for (const o of S.orders.slice(-14)) {
    live.add(o.cid);
    const st = colFor(o.state);
    let card = kcMap.get(o.cid);
    if (!card) {
      card = document.createElement("div"); card.className = "kcard";
      kcMap.set(o.cid, card);
    }
    card.dataset.st = st;
    const pct = o.qty ? Math.min(100, o.filled/o.qty*100) : 0;
    card.innerHTML = `<div class="kc-top"><span>${o.sym}</span><span class="side-${o.side}">${o.side}</span></div>
      <div class="kc-sub">${o.qty} · ${o.state} · ${o.cid.slice(-8)}</div>
      <div class="kc-bar"><i style="width:${pct}%"></i></div>`;
    const target = cols[st];
    if (card.parentElement !== target) { target.prepend(card); }
  }
  for (const [cid, card] of kcMap) if (!live.has(cid)) { card.remove(); kcMap.delete(cid); }
  for (const c of $$(".kcol")) {
    const st = c.dataset.st;
    c.querySelector("i").textContent = [...kcMap.values()].filter(k => k.parentElement === c.querySelector(".kcards") && k.dataset.st === st).length;
  }
}
on("orders", renderKanban);

/* ══════════ [07] SIGNAL MATRIX ══════════ */
export function renderSignals() {
  let html = `<div class="sm-h"></div>` + Object.keys(SECTIONS).map(k => `<div class="sm-h">${SECH[k]}</div>`).join("");
  for (const a of ANALYSTS) {
    html += `<div class="sm-r">${a}</div>`;
    for (const sec in SECTIONS) {
      const c = S.sig[a+"|"+sec] || {d:"N",s:0,conf:0};
      const arrow = c.d === "L" ? "▲" : c.d === "S" ? "▼" : "—";
      html += `<div class="sm-c ${c.d} ${c.s===3?"hot":""}" title="${a} → ${sec}">
        <span class="d">${arrow}${"●".repeat(c.s)}</span>
        <span class="c">${c.d==="N" ? "—" : c.conf.toFixed(2)}</span></div>`;
    }
  }
  $("#sig-matrix").innerHTML = html;
}
on("signals", renderSignals);

/* ══════════ [08] LOGS with channel filter ══════════ */
let logFilter = "ALL";
const tape = $("#log-tape");
function logHtml(l) {
  return `<div class="ln"><span class="ts">${l.ts.slice(11,19)}</span><span class="lv ${l.level}">${l.level}</span><span class="lg">${l.logger}</span><span class="msg">${l.msg}</span></div>`;
}
function match(l) { return logFilter === "ALL" || l.ch === logFilter; }
export function renderTape() {
  tape.innerHTML = S.logs.filter(match).slice(-60).map(logHtml).join("");
}
on("log", l => {
  if (!match(l)) return;
  tape.insertAdjacentHTML("beforeend", logHtml(l));
  while (tape.children.length > 60) tape.removeChild(tape.firstChild);
});
$$(".lchip").forEach(b => b.addEventListener("click", () => {
  $$(".lchip").forEach(x => x.classList.toggle("active", x === b));
  logFilter = b.dataset.ch; renderTape();
}));
let logWin = [];
on("log", () => logWin.push(Date.now()));
setInterval(() => { logWin = logWin.filter(t => t > Date.now()-60000); $("#log-rate").textContent = logWin.length + "/min"; }, 2000);

/* ══════════ [09] COMMS ══════════ */
export function renderAlerts(a) {
  $("#alert-list").innerHTML = a.map(x =>
    `<li class="${x.level}"><span class="a-ico">${x.level==="CRITICAL"?"!!":x.level==="WARN"?"!":"✓"}</span>
     <span>${x.text}</span><span class="a-time">${x.at}</span></li>`).join("");
}
on("alerts", renderAlerts);
on("approvals", list => {
  $("#appr-tb").innerHTML = list.slice(-5).reverse().map(t =>
    `<tr><td>${t.id}</td><td>${t.section.slice(0,4)}</td><td>${t.ttl}m</td>
     <td class="${t.state==="AUTO_FLOW"?"ok":""}">${t.state==="AUTO_FLOW"?"auto · paper":t.state}</td></tr>`).join("")
    || `<tr><td colspan="4" class="dim" style="text-align:center">queue empty</td></tr>`;
});

/* ══════════ [10] OPS ══════════ */
$("#phase-row").innerHTML = PHASES.map((p,i) =>
  `<div class="ph ${i<=1?"done":i===2?"now":""}"><b>P${i}</b>${i<=1?"✓":i===2?"⬤":"·"}</div>`).join("");
export function renderOps(ops) {
  const pct = ops.llmSpend/ops.llmBudget*100;
  $("#budget-fill").style.width = pct + "%";
  $("#budget-txt").textContent = `$${ops.llmSpend.toFixed(2)} / $${ops.llmBudget} · 30d`;
  $("#ingest-tb").innerHTML = ops.ingest.map(s => {
    const cls = s.lag < 10 ? "ok" : s.lag < 90 ? "" : "bad";
    const col = s.lag < 10 ? "var(--green)" : s.lag < 90 ? "var(--yellow)" : "var(--coral)";
    return `<tr><td>${s.src}</td><td class="lag" style="color:${col}">${s.lag.toFixed(1)}s</td></tr>`;
  }).join("");
  $("#op-uptime").textContent = ((Date.now()-S.t0)/36e5).toFixed(1) + "h";
}
on("ops", renderOps);

export function boot() {
  renderSignals(); renderKanban(); renderTape();
  renderAlerts(S.alerts); renderOps(S.ops);
}
