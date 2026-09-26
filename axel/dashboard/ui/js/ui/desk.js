/* ═══════════════════════════════════════════════════════════════
   AXEL TERMINAL · ui/desk.js — page 1 renderers
   [02] portfolio · [03] risk gate · [04] sections · [05] equity
   ═══════════════════════════════════════════════════════════════ */

import { SECTIONS, SECCOLOR, SECLABEL } from "../config.js";
import { S, on, money } from "../sim/state.js";
import { portfolioSnapshot } from "../sim/market.js";

const $ = s => document.querySelector(s);

/* ══════════ [03] RISK GATE ══════════ */
const rgEls = Object.fromEntries(
  [...document.querySelectorAll("#rg-checks li")].map(li => [li.dataset.k, li]));
let lastRun = null;
on("risk:start", p => {
  $("#rg-proposal").innerHTML =
    `<div class="sym">${p.section} · ${p.symbol} <span class="side-${p.side[0]}">${p.side}</span></div>
     <div class="meta">entry <b>${p.entry.toFixed(2)}</b> · stop <b>${p.stop.toFixed(2)}</b> · target <b>${p.target.toFixed(2)}</b> · R/R <b>${p.rr.toFixed(2)}</b></div>
     <div class="meta">conf <b>${p.conf.toFixed(2)}</b> · ${p.horizon} · playbook <b>${p.strategy}</b> · ${p.id}</div>`;
  Object.values(rgEls).forEach(li => li.className = "");
  $("#rg-verdict").className = "rg-verdict";
  $("#rg-verdict").innerHTML = "<span>evaluating…</span>";
  $("#rg-count").textContent = S.proposalsEvaluated + " proposals";
});
on("risk:check", ({ p, c }) => {
  if (lastRun) rgEls[lastRun]?.classList.remove("run");
  const li = rgEls[c.k];
  if (li) { li.classList.add("run"); lastRun = c.k;
    setTimeout(() => { li.classList.remove("run"); li.classList.add(c.pass ? "pass" : "fail"); }, 130 / S.speed); }
});
on("risk:done", ({ verdict }) => {
  if (lastRun) rgEls[lastRun]?.classList.remove("run"); lastRun = null;
  const v = $("#rg-verdict");
  v.className = "rg-verdict " + (verdict === "APPROVED" ? "approved" : "rejected") + " bump";
  v.innerHTML = verdict === "APPROVED"
    ? "<span>✓ approved</span><small>deterministic gate passed — execution unlocked</small>"
    : `<span>✕ ${verdict}</span><small>veto is final · the LLM cannot appeal a hard-coded limit</small>`;
});

/* ══════════ [04] SECTIONS ══════════ */
export function renderSections() {
  const tb = $("#sec-table tbody");
  if (!tb.children.length) tb.innerHTML = Object.keys(SECTIONS).map(n =>
    `<tr id="row-${n}"><td>${n}</td>
     <td class="nav"></td><td class="dpnl"></td><td class="pos"></td><td class="expo"></td>
     <td><canvas class="spark" width="168" height="44"></canvas></td><td class="st"></td></tr>`).join("");
  for (const [name, st] of Object.entries(S.sections)) {
    const tr = $("#row-" + name), dp = st.dayPnl * 100;
    tr.querySelector(".nav").textContent = money(st.nav).replace("$","$ ");
    const d = tr.querySelector(".dpnl");
    d.textContent = (dp >= 0 ? "+" : "") + dp.toFixed(2) + "%";
    d.className = "dpnl " + (dp >= 0 ? "up" : "down");
    tr.querySelector(".pos").textContent = st.positions;
    tr.querySelector(".expo").textContent = (st.nav / S.equity * 100).toFixed(1) + "%";
    const chip = tr.querySelector(".st"), state = S.halted ? "HALTED" : st.blocked ? "BLOCKED" : "TRADING";
    if (chip.dataset.s !== state) { chip.dataset.s = state; chip.innerHTML = `<span class="state-chip ${state}">${state}</span>`; }
    drawSpark(tr.querySelector(".spark"), st.hist, dp >= 0 ? "#0acf83" : "#ff7262");
  }
}
function drawSpark(cv, data, color) {
  const ctx = cv.getContext("2d"), W = cv.width, H = cv.height;
  ctx.clearRect(0,0,W,H); if (data.length < 2) return;
  const min = Math.min(...data), max = Math.max(...data), sp = (max-min)||1;
  ctx.beginPath();
  data.forEach((v,i) => { const x = i/(data.length-1)*(W-4)+2, y = H-4-(v-min)/sp*(H-10); i?ctx.lineTo(x,y):ctx.moveTo(x,y); });
  ctx.strokeStyle = color; ctx.lineWidth = 2.4; ctx.lineCap = "round"; ctx.stroke();
}
on("sections", renderSections);

/* ══════════ [05] EQUITY ══════════ */
export function drawEquity() {
  const cv = $("#equity-chart"), dpr = devicePixelRatio || 1;
  const W = cv.clientWidth, H = cv.clientHeight; if (!W || !H) return;
  cv.width = W*dpr; cv.height = H*dpr;
  const ctx = cv.getContext("2d"); ctx.scale(dpr,dpr);
  const d = S.equityHist, min = Math.min(...d)*.997, max = S.hwm*1.005, sp = max-min;
  const Y = v => H-6 - (v-min)/sp*(H-14);
  ctx.strokeStyle = "#1c242f"; ctx.lineWidth = 1;
  for (let i=0;i<5;i++){ const y=6+i*(H-14)/4; ctx.beginPath(); ctx.moveTo(0,y); ctx.lineTo(W,y); ctx.stroke(); }
  ctx.setLineDash([5,5]); ctx.strokeStyle = "rgba(255,200,87,.5)";
  ctx.beginPath(); ctx.moveTo(0,Y(S.hwm)); ctx.lineTo(W,Y(S.hwm)); ctx.stroke();
  const thr = S.hwm*.9;
  ctx.strokeStyle = "rgba(255,114,98,.55)";
  ctx.beginPath(); ctx.moveTo(0,Y(thr)); ctx.lineTo(W,Y(thr)); ctx.stroke(); ctx.setLineDash([]);
  ctx.font = "9px JetBrains Mono"; ctx.fillStyle = "rgba(255,114,98,.8)"; ctx.fillText("−10% TRIP", 6, Y(thr)-4);
  ctx.fillStyle = "rgba(255,200,87,.8)"; ctx.fillText("HWM", W-32, Y(S.hwm)-4);
  ctx.beginPath();
  d.forEach((v,i)=>{ const x=i/(d.length-1)*W, y=Y(v); i?ctx.lineTo(x,y):ctx.moveTo(x,y); });
  ctx.strokeStyle = S.halted ? "#ff7262" : "#a259ff"; ctx.lineWidth = 2; ctx.stroke();
  ctx.lineTo(W,H); ctx.lineTo(0,H); ctx.closePath();
  const g = ctx.createLinearGradient(0,0,0,H);
  g.addColorStop(0, S.halted ? "rgba(255,114,98,.22)" : "rgba(162,89,255,.22)");
  g.addColorStop(1, "transparent");
  ctx.fillStyle = g; ctx.fill();
  $("#eq-last").textContent = "equity " + money(S.equity);
  $("#eq-hwm").textContent  = "hwm " + money(S.hwm);
  $("#eq-lo").textContent   = "dd " + ((S.hwm-S.equity)/S.hwm*100).toFixed(2) + "%";
}
on("equity", () => { drawEquity(); });

/* ══════════ [02] PORTFOLIO ══════════ */
export function renderPortfolio(P) {
  const dayPct = (S.sections.STOCKS.dayPnl*4500 + S.sections.OPTIONS.dayPnl*2500 + S.sections.CRYPTO.dayPnl*1500 + S.sections.FOREX.dayPnl*1000 + S.sections.PREDICTIONS.dayPnl*500);
  $("#pf-aum").textContent = money(P.equity);
  $("#head-aum").textContent = money(P.equity);
  const d = $("#pf-day"), dp = $("#pf-day-pct");
  d.textContent = (dayPct>=0?"+":"") + money(dayPct); d.className = dayPct>=0?"up":"down";
  dp.textContent = (dayPct>=0?"+":"") + (dayPct/100).toFixed(2) + "%";
  dp.style.color = dayPct>=0 ? "var(--green)" : "var(--coral)";
  dp.style.background = dayPct>=0 ? "rgba(10,207,131,.1)" : "rgba(255,114,98,.1)";
  $("#head-day").textContent = (dayPct>=0?"+":"") + (dayPct/P.equity*100).toFixed(2) + "%";
  $("#head-day").style.color = dayPct>=0 ? "var(--green)" : "var(--coral)";
  $("#pf-real").textContent = (P.realized>=0?"+":"") + money(P.realized);
  $("#pf-real").className = P.realized>=0?"up":"down";
  const unreal = P.holdings.reduce((a,h)=>a+h.pnl,0);
  $("#pf-unreal").textContent = (unreal>=0?"+":"") + money(unreal);
  $("#pf-unreal").className = unreal>=0?"up":"down";
  $("#pf-trades").textContent = P.trades;
  $("#pf-win").textContent = P.trades ? (P.wins/P.trades*100).toFixed(0)+"%" : "—";
  $("#alloc-bars").innerHTML = Object.entries(S.sections).map(([n,st]) => {
    const pct = st.nav/P.equity*100;
    return `<div class="ab"><span>${SECLABEL[n]}</span>
      <span class="bar"><i style="width:${pct}%;background:${SECCOLOR[n]}"></i></span>
      <span class="v">${pct.toFixed(1)}%</span></div>`;
  }).join("");
  $("#hold-tb").innerHTML = P.holdings.slice(-7).reverse().map(h =>
    `<tr><td>${h.sym}</td><td>${h.side}${h.qty}</td><td>${money(h.qty*h.last)}</td>
     <td class="${h.pnl>=0?"up":"down"}">${h.pnl>=0?"+":""}${h.pnl.toFixed(2)}</td></tr>`).join("")
    || `<tr><td colspan="4" class="dim" style="text-align:center;padding:8px">flat — no open positions</td></tr>`;
}
on("portfolio", renderPortfolio);

export function boot() {
  renderSections();
  drawEquity();
  renderPortfolio(portfolioSnapshot());
}
