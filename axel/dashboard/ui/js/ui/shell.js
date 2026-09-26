/* ═══════════════════════════════════════════════════════════════
   AXEL TERMINAL · ui/shell.js — chrome shared by both pages
   tab switching (1/2) · clock · lamps · quote tape · fkey actions
   (F1 drill / F2 freeze / F3 speed / F4 resume) · halt & pause
   banners/status.
   ═══════════════════════════════════════════════════════════════ */

import { PK_COLORS } from "../config.js";
import { S, on, setPaused, setSpeed } from "../sim/state.js";
import { runDrill, resume } from "../sim/market.js";
import { layoutPipeline, sendChain, CHAIN_HALT } from "./pipeline.js";
import { drawEquity } from "./desk.js";

const $  = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];

/* ══════════ TABS / PAGES ══════════ */
function showPage(id) {
  $$(".page").forEach(p => p.classList.toggle("active", p.id === id));
  $$(".tab").forEach(t => t.classList.toggle("active", t.dataset.page === id));
  layoutPipeline(); drawEquity();
}
$$(".tab").forEach(t => t.addEventListener("click", () => showPage(t.dataset.page)));

/* ══════════ HALT / PAUSE ══════════ */
on("halt", halted => {
  $("#halt-banner").hidden = !halted;
  $("#lamp-core").className = "dot " + (halted ? "bad" : "ok");
  const qn = $("#pipeline-qn");
  qn.textContent = halted ? "GLOBAL HALT" : "running";
  qn.className = "p-tag" + (halted ? " halt" : " green");
  qn.id = "pipeline-qn";
  $$(".nd").forEach(n => halted ? n.classList.add("halted") : n.classList.remove("halted"));
  if (halted) sendChain(CHAIN_HALT, PK_COLORS.halt);
});
on("paused", p => {
  if (S.halted) return;
  const qn = $("#pipeline-qn");
  qn.textContent = p ? "frozen" : "running";
  qn.className = "p-tag " + (p ? "pause" : "green"); qn.id = "pipeline-qn";
});

/* ══════════ TAPE / CLOCK / LAMPS ══════════ */
on("quotes", q => {
  const items = Object.entries(q).map(([sym,x]) => {
    const p = x.price > 50 ? x.price.toFixed(0) : x.price.toFixed(4);
    return `<span class="tk"><span class="sec-tag">${x.sec.slice(0,3)}</span><b>${sym}</b> ${p} <span class="chg ${x.chg>=0?"up":"down"}">${x.chg>=0?"▲":"▼"}${Math.abs(x.chg).toFixed(2)}%</span></span>`;
  }).join("");
  $("#tape-track").innerHTML = items + items;
});
setInterval(() => {
  const d = new Date();
  $("#clock").childNodes[0].textContent = d.toISOString().slice(11,19) + " ";
  $("#clock-date").textContent = d.toISOString().slice(0,10);
}, 1000);
setInterval(() => { $("#lamp-agent").className = "dot " + (Math.random()<.3 ? "warn" : "ok"); }, 1100);

/* ══════════ FKEYS / SHORTCUTS ══════════ */
$$(".fkey").forEach(k => k.addEventListener("click", () => {
  const a = k.dataset.action;
  if (a === "halt-drill") runDrill();
  if (a === "pause") setPaused();
  if (a === "resume") { resume(); setPaused(false); }
  if (a === "speed") { S.speed = S.speed >= 4 ? 1 : S.speed*2; setSpeed(S.speed); $("#speed-val").textContent = S.speed; }
}));
addEventListener("keydown", e => {
  if (e.key === "1") showPage("p1");
  if (e.key === "2") showPage("p2");
  const map = { F1:"halt-drill", F2:"pause", F3:"speed", F4:"resume" };
  if (map[e.key]) { e.preventDefault(); document.querySelector(`.fkey[data-action="${map[e.key]}"]`)?.click(); }
});
