/* ═══════════════════════════════════════════════════════════════
   AXEL TERMINAL · ui/pipeline.js — [01] n8n-style pipeline
   Node bubbles are HTML (absolutely positioned by % from
   config.NODES); wires are cubic beziers in an SVG overlay laid
   out on resize/page-change; packets animate along wire paths via
   getPointAtLength. This module also fires the pipeline packet
   chains in reaction to sim events.
   ═══════════════════════════════════════════════════════════════ */

import { NODES, WIRES, PK_COLORS } from "../config.js";
import { S, on } from "../sim/state.js";

const $  = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const NS = "http://www.w3.org/2000/svg";

/* build node DOM once */
const nodesEl = $("#nodes");
for (const [id, n] of Object.entries(NODES)) {
  const d = document.createElement("div");
  d.className = "nd " + (n.cls || ""); d.id = "nd-" + id;
  d.style.left = n.x + "%"; d.style.top = n.y + "%";
  if (n.w) d.style.width = n.w + "px";
  d.innerHTML = `<span class="ico" style="background:${n.c}">${n.ico}</span>
    <span class="lab"><b>${n.t}</b><span>${n.s}</span></span><i class="port in"></i><i class="port out"></i>`;
  nodesEl.appendChild(d);
}

const wiresSvg = $("#wires"), pktLayer = $("#pkt-layer");
let wirePaths = {}, nodeRects = {};

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
      const mid = p.getPointAtLength(p.getTotalLength() * .5);
      const lab = document.createElement("span");
      lab.className = "wlabel" + (w.kind === "rec" ? " rec" : w.kind === "halt" ? " halt" : "");
      lab.style.left = (mid.x + 6) + "px"; lab.style.top = (mid.y + 6) + "px";
      lab.textContent = w.lab;
      $(".canvas-wrap").appendChild(lab);
    }
  });
}

export function litNode(id, ms = 500) {
  const el = $("#nd-" + id); if (!el) return;
  el.classList.add("lit"); setTimeout(() => el.classList.remove("lit"), ms);
}

export function sendChain(edgeIdxs, color, speed = 1) {
  edgeIdxs.forEach((idx, stage) => {
    const path = wirePaths[idx]; if (!path) return;
    const len = path.getTotalLength();
    const dur = Math.max(600, len * 2.2) / (S.speed * speed);
    setTimeout(() => {
      const w = WIRES[idx];
      litNode(w.a, 380);
      const dot = document.createElement("div");
      dot.className = "pkt"; dot.style.color = color; dot.style.background = color;
      pktLayer.appendChild(dot);
      const t0 = performance.now();
      const step = now => {
        const f = Math.min(1, (now - t0) / dur);
        const p = path.getPointAtLength(f * len);
        dot.style.left = p.x + "px"; dot.style.top = p.y + "px";
        if (f < 1) requestAnimationFrame(step);
        else { dot.remove(); litNode(w.b); }
      };
      requestAnimationFrame(step);
    }, stage * dur * .5);
  });
}

/* edge index map + named chains */
const IDX = {};
WIRES.forEach((w, i) => IDX[w.a + "-" + w.b] = i);
export const CHAIN_AGENT = ["di-fe","fe-an","an-ep","ep-sm","sm-ax"].map(k => IDX[k]);
export const CHAIN_REC   = ["ax-db","db-rk"].map(k => IDX[k]);
export const CHAIN_EXEC  = ["rk-ex","ex-br"].map(k => IDX[k]);
export const CHAIN_FILL  = ["br-ex"].map(k => IDX[k]);
export const CHAIN_HALT  = ["ex-wd","wd-rk"].map(k => IDX[k]);

/* ---------- packet triggers ---------- */
on("risk:start", () => sendChain(CHAIN_AGENT, PK_COLORS.agent));
on("risk:done", ({ verdict }) => {
  sendChain(CHAIN_REC, PK_COLORS.rec);
  if (verdict === "APPROVED") setTimeout(() => sendChain(CHAIN_EXEC, PK_COLORS.core), 700 / S.speed);
});
on("orders", () => {
  const last = S.orders.at(-1);
  if (last && last.state === "FILLED" && !last._pkt) { last._pkt = true; sendChain(CHAIN_FILL, PK_COLORS.fill); }
});
on("signals", () => litNode("an", 400));
