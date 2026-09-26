/* ═══════════════════════════════════════════════════════════════
   AXEL TERMINAL · ui/tip.js — one shared click-to-inspect popover
   showTip(x, y, title, [[label, value], …])
   ═══════════════════════════════════════════════════════════════ */

const el = document.createElement("div");
el.id = "tip"; el.hidden = true;
document.body.appendChild(el);

export function showTip(x, y, title, rows) {
  el.innerHTML = `<b>${title}</b>` + rows
    .map(([k, v]) => `<div class="tr"><span>${k}</span><em>${v}</em></div>`).join("");
  el.hidden = false;
  const w = el.offsetWidth, h = el.offsetHeight;
  el.style.left = Math.min(Math.max(8, x + 14), innerWidth  - w - 8) + "px";
  el.style.top  = Math.min(Math.max(8, y + 14), innerHeight - h - 8) + "px";
  el.classList.remove("in"); void el.offsetWidth; el.classList.add("in");
}
export function hideTip() { el.hidden = true; el.classList.remove("in"); }

addEventListener("keydown", e => { if (e.key === "Escape") hideTip(); });
addEventListener("click", e => { if (!el.contains(e.target)) hideTip(); });
