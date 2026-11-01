/* ═══════════════════════════════════════════════════════════════
   AXEL TERMINAL · ui/pipeline.js — [01] n8n-style pipeline
   Node bubbles are HTML (absolutely positioned by % from
   config.NODES); wires are cubic beziers in an SVG overlay laid
   out on resize/page-change; packets animate along wire paths via
   getPointAtLength. This module also fires the pipeline packet
   chains in reaction to sim events, and counts every hit a node
   receives so a dead service is visible at a glance.
   ═══════════════════════════════════════════════════════════════ */

import { NODES, WIRES, PK_COLORS } from "../config.js";
import { S, on } from "../sim/state.js";
import { showTip } from "./tip.js";

const $  = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const NS = "http://www.w3.org/2000/svg";

/* build node DOM once */
const nodesEl = $("#nodes");
for (const [id, n] of Object.entries(NODES)) {
  const d = document.createElement("div");
  d.className = "nd " + (n.cls || ""); d.id = "nd-" + id;
  d.style.left = n.x + "%"; d.style.top = n.y + "%";
  d.innerHTML = `<b class="ttl"><span class="ico" style="background:${n.c}">${n.ico}</span><span class="t">${n.t}</span></b>
    <span class="sub">${n.s}</span>
    <i class="cnt">0</i>
    <i class="port in"></i><i class="port out"></i>`;
  d.addEventListener("mouseenter", () => edgesOf(id).forEach(i => wirePaths[i]?.classList.add("hi")));
  d.addEventListener("mouseleave", () => $$(".wire.hi").forEach(p => p.classList.remove("hi")));
  d.addEventListener("click", e => { e.stopPropagation(); showTip(e.clientX, e.clientY, n.t, n.info); });
  nodesEl.appendChild(d);
}

const wiresSvg = $("#wires"), pktLayer = $("#pkt-layer");
let wirePaths = {}, nodeRects = {};

/* ─────────── geometry ─────────── */
export function layoutPipeline() {
  const wrap = $(".canvas-wrap");
  const canvasBox = wrap.getBoundingClientRect();
  const W = canvasBox.width - 12, H = canvasBox.height - 12;
  if (W <= 0 || H <= 0) return;
  wiresSvg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  nodeRects = {};
  for (const [id, n] of Object.entries(NODES)) {
    const el = $("#nd-" + id);
    nodeRects[id] = { cx: n.x/100*W, cy: n.y/100*H, hw: el.offsetWidth/2, hh: el.offsetHeight/2 };
  }
  wiresSvg.innerHTML = ""; $$(".wlabel").forEach(e => e.remove());
  wirePaths = {};
  const pt = (id, side, off = 0) => {
    const r = nodeRects[id];
    return side === "R" ? [r.cx + r.hw, r.cy + off]
         : side === "L" ? [r.cx - r.hw, r.cy + off]
         : side === "T" ? [r.cx + off, r.cy - r.hh]
         :                [r.cx + off, r.cy + r.hh];
  };
  WIRES.forEach((w, i) => {
    const [x1,y1] = pt(w.a, w.sa, w.ox || 0), [x2,y2] = pt(w.b, w.tb, w.oxt || 0);
    const dx = Math.max(30, Math.abs(x2-x1)*.5), dy = Math.max(30, Math.abs(y2-y1)*.5);
    const c1 = [w.sa==="R"?x1+dx:w.sa==="L"?x1-dx:x1, w.sa==="B"?y1+dy:w.sa==="T"?y1-dy:y1];
    const c2 = [w.tb==="R"?x2+dx:w.tb==="L"?x2-dx:x2, w.tb==="B"?y2+dy:w.tb==="T"?y2-dy:y2];
    const p = document.createElementNS(NS, "path");
    p.setAttribute("d", `M${x1},${y1} C${c1[0]},${c1[1]} ${c2[0]},${c2[1]} ${x2},${y2}`);
    p.setAttribute("class", "wire" + (w.kind ? " w-" + w.kind : ""));
    p.id = "wire-" + i;
    wiresSvg.appendChild(p);
    wirePaths[i] = p;
    if (w.lab) {
      const mid = p.getPointAtLength(p.getTotalLength() * (w.lm ?? .5));
      const lab = document.createElement("span");
      lab.className = "wlabel" + (w.kind === "rec" ? " rec" : w.kind === "halt" ? " halt" : "");
      lab.dataset.w = i;
      lab.style.left = (mid.x + (w.lx || 0)) + "px";
      lab.style.top  = (mid.y + 6 + (w.ly || 0)) + "px";
      lab.textContent = w.lab;
      $(".canvas-wrap").appendChild(lab);
    }
  });
}

const edgesOf = id => WIRES.map((w, i) => (w.a === id || w.b === id ? i : -1)).filter(i => i >= 0);

/* ─────────── node lighting + hit counter ─────────── */
const hits = {};
export function litNode(id, ms = 500) {
  const el = $("#nd-" + id); if (!el) return;
  el.classList.add("lit"); setTimeout(() => el.classList.remove("lit"), ms);
  const c = hits[id] = (hits[id] || 0) + 1;
  el.querySelector(".cnt").textContent = c > 999 ? "999+" : c;
}

/* ─────────── packets ─────────── */
function fly(idx, color, { dur, pay = null, amb = false } = {}) {
  const path = wirePaths[idx];
  if (!path) return Promise.resolve();
  const len = path.getTotalLength();
  const ms = (dur || Math.max(600, len * 2.2)) / S.speed;
  return new Promise(done => {
    const dot = document.createElement("div");
    dot.className = "pkt" + (pay ? " pay" : "") + (amb ? " amb" : "");
    dot.style.color = color;
    if (pay) dot.textContent = pay; else dot.style.background = color;
    pktLayer.appendChild(dot);
    path.classList.add("flash");
    const t0 = performance.now();
    const step = now => {
      const f = Math.min(1, (now - t0) / ms);
      const p = path.getPointAtLength(f * len);
      dot.style.left = p.x + "px"; dot.style.top = p.y + "px";
      if (f < 1) requestAnimationFrame(step);
      else { dot.remove(); path.classList.remove("flash"); done(); }
    };
    requestAnimationFrame(step);
  });
}

export function sendChain(edgeIdxs, color, speed = 1) {
  edgeIdxs.forEach((idx, stage) => {
    const w = WIRES[idx]; if (!w) return;
    setTimeout(() => {
      litNode(w.a, 380);
      fly(idx, color, { pay: w.pay }).then(() => litNode(w.b));
    }, stage * 900 / S.speed * speed);
  });
}

/* one packet on one wire, e.g. a heartbeat or a read-only tap */
function pulse(idx, color, label, dur) {
  const w = WIRES[idx]; if (!w) return;
  litNode(w.a, 380);
  fly(idx, color, { dur, pay: label }).then(() => litNode(w.b));
}

/* edge index map + named chains */
const IDX = {};
WIRES.forEach((w, i) => IDX[w.a + "-" + w.b] = i);
export const CHAIN_AGENT = ["di-fe","fe-an","an-ep","ep-sm","sm-ax"].map(k => IDX[k]);
export const CHAIN_REC   = ["ax-db","db-rk"].map(k => IDX[k]);
export const CHAIN_EXEC  = ["rk-ap","ap-ex","ex-br"].map(k => IDX[k]);
export const CHAIN_FILL  = ["br-ex"].map(k => IDX[k]);
export const CHAIN_HALT  = ["ex-wd","wd-rk"].map(k => IDX[k]);
const CHAIN_HB  = IDX["ex-wd"];   // Execution → Watchdog heartbeat
const CHAIN_OBS = IDX["db-qr"];   // TimescaleDB → Quant Research tap

/* ─────────── packet triggers ─────────── */
on("risk:start", () => sendChain(CHAIN_AGENT, PK_COLORS.agent));
on("risk:done", ({ verdict }) => {
  sendChain(CHAIN_REC, PK_COLORS.rec);
  if (verdict === "APPROVED") setTimeout(() => sendChain(CHAIN_EXEC, PK_COLORS.core), 900 / S.speed);
});
on("orders", () => {
  const last = S.orders.at(-1);
  if (last && last.state === "FILLED" && !last._pkt) { last._pkt = true; sendChain(CHAIN_FILL, PK_COLORS.fill); }
});
on("signals", () => litNode("an", 400));
on("watchdog", () => pulse(CHAIN_HB, PK_COLORS.order, "♥", 1500));
on("qr:pr",    () => pulse(CHAIN_OBS, PK_COLORS.obs, "observe", 2600));

/* ambient dust: the graph is never still, even between proposals */
setInterval(() => {
  if (S.paused || !Object.keys(wirePaths).length) return;
  const idx = Math.floor(Math.random() * WIRES.length);
  const w = WIRES[idx];
  fly(idx, w.kind === "halt" ? PK_COLORS.halt : "#4b545e", { dur: 2400, amb: true })
     .then(() => litNode(w.b, 260));
}, 750);
