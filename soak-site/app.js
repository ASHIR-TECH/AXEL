const DATA_URL =
  "https://raw.githubusercontent.com/ASHIR-TECH/AXEL/soak-data/snapshot.json";
const REFRESH_MS = 60_000;
const STALE_MS = 15 * 60_000;

const $ = (id) => document.getElementById(id);

const money = (n, digits = 2) =>
  n === null || n === undefined || Number.isNaN(Number(n))
    ? "—"
    : Number(n).toLocaleString("en-US", {
        style: "currency",
        currency: "USD",
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });

const signed = (n) => {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
  const v = Number(n);
  return `${v >= 0 ? "+" : "−"}${money(Math.abs(v))}`;
};

const pct = (n, digits = 2) =>
  n === null || n === undefined || Number.isNaN(Number(n))
    ? "—"
    : `${Number(n).toFixed(digits)}%`;

const num = (n) =>
  n === null || n === undefined
    ? "—"
    : Number(n).toLocaleString("en-US", { maximumFractionDigits: 2 });

const stamp = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("en-GB", {
    timeZone: "America/New_York",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
  });
};

const shortTime = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("en-GB", {
    timeZone: "America/New_York",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
};

function tone(node, value) {
  node.classList.remove("up", "down", "pos", "neg");
  if (value > 0) node.classList.add(node.tagName === "TD" ? "pos" : "up");
  if (value < 0) node.classList.add(node.tagName === "TD" ? "neg" : "down");
}

function setStatus(kind, text) {
  $("status-dot").className = `dot ${kind}`;
  $("status-text").textContent = text;
}

function renderAccount(d) {
  const a = d.account || {};
  const start = d.meta?.starting_equity ?? a.high_water_mark ?? null;
  $("nav").textContent = money(a.nav);
  $("cash").textContent = money(a.cash);
  $("hwm").textContent = money(a.high_water_mark);
  $("drawdown").textContent = pct(a.drawdown_pct);
  $("realized").textContent = signed(a.realized_pnl);
  $("unrealized").textContent = signed(a.unrealized_pnl);
  tone($("realized"), a.realized_pnl);
  tone($("unrealized"), a.unrealized_pnl);

  const deltaNode = $("nav-delta");
  if (start && a.nav) {
    const change = a.nav - start;
    const changePct = (change / start) * 100;
    deltaNode.textContent = `${signed(change)} (${changePct >= 0 ? "+" : "−"}${Math.abs(changePct).toFixed(3)}%) since ${money(start, 0)} start`;
    tone(deltaNode, change);
  } else {
    deltaNode.textContent = "\u00a0";
  }
  $("curve-range").textContent = a.as_of ? `as of ${stamp(a.as_of)}` : "—";
}

function renderWindow(d) {
  const m = d.meta || {};
  const elapsed = Number(m.days_elapsed || 0);
  const remaining = Number(m.days_remaining || 0);
  const total = elapsed + remaining || 56;
  $("window").textContent = `${elapsed.toFixed(2)} / ${total.toFixed(0)} days`;
  $("progress-bar").style.width = `${Math.min(100, (elapsed / total) * 100)}%`;
  $("window-sub").textContent = m.started_at
    ? `started ${stamp(m.started_at)} · ends ${stamp(m.window_end)} · ${remaining.toFixed(2)} days left`
    : "awaiting soak marker";
  $("run-line").textContent = `${m.feed || "—"} feed · ${m.execution || "—"} execution · ${(m.sections || []).join(", ") || "—"}`;
}

function renderStats(d) {
  const s = d.stats || {};
  $("s-proposals").textContent = num(s.proposals);
  $("s-orders").textContent = num(s.orders);
  $("s-fills").textContent = num(s.broker_orders ?? s.fills);
  $("s-closed").textContent = num(s.closed_trades);
  $("s-winrate").textContent =
    s.win_rate === null || s.win_rate === undefined ? "—" : pct(s.win_rate * 100, 1);
  $("s-slip").textContent =
    s.mean_slippage_bps === null || s.mean_slippage_bps === undefined
      ? "—"
      : `${Number(s.mean_slippage_bps).toFixed(2)} bps`;
  $("s-coverage").textContent = `${Number(d.coverage_days || 0).toFixed(2)} d`;
}

function renderPositions(d) {
  const rows = d.positions || [];
  const body = $("positions");
  body.innerHTML = "";
  rows.forEach((p) => {
    const tr = document.createElement("tr");
    const side = String(p.side || "").toLowerCase();
    tr.innerHTML = `
      <td><strong>${p.symbol ?? "—"}</strong></td>
      <td><span class="pill">${p.section ?? "—"}</span></td>
      <td><span class="pill ${side}">${p.side ?? "—"}</span></td>
      <td class="num">${num(p.qty)}</td>
      <td class="num">${money(p.entry, 4)}</td>
      <td class="num">${money(p.price, 4)}</td>
      <td class="num ${p.unrealized >= 0 ? "pos" : "neg"}">${signed(p.unrealized)}</td>
      <td>${shortTime(p.opened_at)}</td>`;
    body.appendChild(tr);
  });
  $("pos-count").textContent = rows.length ? `${rows.length} open` : "flat";
  $("pos-empty").classList.toggle("show", rows.length === 0);
}

function renderTrades(d) {
  const rows = d.trades || [];
  const body = $("trades");
  body.innerHTML = "";
  rows.forEach((t) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${shortTime(t.at)}</td>
      <td><strong>${t.symbol ?? "—"}</strong></td>
      <td><span class="pill">${t.section ?? "—"}</span></td>
      <td class="num">${num(t.qty)}</td>
      <td class="num">${money(t.entry, 4)}</td>
      <td class="num">${money(t.exit, 4)}</td>
      <td class="num ${t.pnl >= 0 ? "pos" : "neg"}">${signed(t.pnl)}</td>
      <td>${t.reason ?? "—"}</td>`;
    body.appendChild(tr);
  });
  $("trade-count").textContent = rows.length ? `${rows.length} recent` : "none";
  $("trade-empty").classList.toggle("show", rows.length === 0);
}

function renderWorkers(d) {
  const workers = d.workers || {};
  const host = $("workers");
  host.innerHTML = "";
  const names = Object.keys(workers);
  names.forEach((name) => {
    const w = workers[name];
    const ratio = Number(w.ratio || 0);
    const toneClass = ratio >= 0.99 ? "pos" : ratio >= 0.9 ? "" : "neg";
    const el = document.createElement("div");
    el.className = "worker";
    el.innerHTML = `
      <div class="worker-top">
        <span class="worker-name">${name}</span>
        <span class="worker-ratio ${toneClass}">${(ratio * 100).toFixed(2)}%</span>
      </div>
      <div class="worker-meta">${num(w.cycles)} cycles · ${w.gaps} gaps · ${Number(w.downtime_hours || 0).toFixed(2)} h down</div>
      <div class="worker-meta">last seen ${stamp(w.last_seen)}</div>`;
    host.appendChild(el);
  });
  $("coverage-note").textContent = `min covered ${Number(d.coverage_days || 0).toFixed(2)} days of 56`;
}

function drawChart(d) {
  const points = d.equity_curve || [];
  const canvas = $("chart");
  const empty = $("chart-empty");
  if (points.length < 2) {
    canvas.style.visibility = "hidden";
    empty.classList.add("show");
    return;
  }
  canvas.style.visibility = "visible";
  empty.classList.remove("show");

  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.round(rect.width * dpr));
  canvas.height = Math.max(1, Math.round(rect.height * dpr));
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);

  const W = rect.width;
  const H = rect.height;
  const pad = { top: 16, right: 12, bottom: 24, left: 54 };
  const values = points.map((p) => Number(p.nav));
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const span = max - min;
  min -= span * 0.08;
  max += span * 0.08;

  const x = (i) => pad.left + (i / (points.length - 1)) * (W - pad.left - pad.right);
  const y = (v) => pad.top + ((max - v) / (max - min)) * (H - pad.top - pad.bottom);

  ctx.clearRect(0, 0, W, H);
  ctx.strokeStyle = "rgba(30, 42, 54, 0.9)";
  ctx.lineWidth = 1;
  ctx.font = "10px ui-monospace, monospace";
  ctx.fillStyle = "#7f93a6";

  for (let i = 0; i <= 4; i++) {
    const value = min + ((max - min) * i) / 4;
    const yy = Math.round(y(value)) + 0.5;
    ctx.beginPath();
    ctx.moveTo(pad.left, yy);
    ctx.lineTo(W - pad.right, yy);
    ctx.stroke();
    ctx.fillText(money(value, 0), 4, yy + 3);
  }

  const first = new Date(points[0].t);
  const last = new Date(points[points.length - 1].t);
  const fmt = (dt) =>
    dt.toLocaleString("en-GB", {
      timeZone: "America/New_York",
      month: "short",
      day: "2-digit",
    });
  if (!Number.isNaN(first.getTime())) {
    ctx.fillText(fmt(first), pad.left, H - 6);
  }
  if (!Number.isNaN(last.getTime())) {
    const label = fmt(last);
    ctx.fillText(label, W - pad.right - ctx.measureText(label).width, H - 6);
  }

  const gradient = ctx.createLinearGradient(0, pad.top, 0, H - pad.bottom);
  gradient.addColorStop(0, "rgba(53, 224, 138, 0.22)");
  gradient.addColorStop(1, "rgba(53, 224, 138, 0)");

  ctx.beginPath();
  points.forEach((p, i) => {
    const px = x(i);
    const py = y(Number(p.nav));
    if (i === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  });
  ctx.lineTo(x(points.length - 1), H - pad.bottom);
  ctx.lineTo(x(0), H - pad.bottom);
  ctx.closePath();
  ctx.fillStyle = gradient;
  ctx.fill();

  ctx.beginPath();
  points.forEach((p, i) => {
    const px = x(i);
    const py = y(Number(p.nav));
    if (i === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  });
  ctx.strokeStyle = "#35e08a";
  ctx.lineWidth = 2;
  ctx.lineJoin = "round";
  ctx.stroke();

  const lastValue = Number(points[points.length - 1].nav);
  ctx.fillStyle = lastValue >= values[0] ? "#35e08a" : "#ff5f6d";
  ctx.beginPath();
  ctx.arc(x(points.length - 1), y(lastValue), 3.5, 0, Math.PI * 2);
  ctx.fill();
}

let lastPayload = null;

function render(d) {
  lastPayload = d;
  renderAccount(d);
  renderWindow(d);
  renderStats(d);
  renderPositions(d);
  renderTrades(d);
  renderWorkers(d);
  drawChart(d);
  $("generated").textContent = `snapshot ${d.meta?.generated_at ? stamp(d.meta.generated_at) : "—"}`;
  $("feed").textContent = `${d.meta?.feed || "—"} · ${d.meta?.execution || "—"}`;

  const age = d.meta?.generated_at ? Date.now() - new Date(d.meta.generated_at).getTime() : Infinity;
  if (age <= STALE_MS) setStatus("live", `live · updated ${Math.max(0, Math.round(age / 60000))}m ago`);
  else setStatus("stale", `stale · ${Math.round(age / 3600000)}h old`);
}

async function refresh() {
  try {
    const res = await fetch(`${DATA_URL}?t=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    render(await res.json());
  } catch (err) {
    if (!lastPayload) {
      setStatus("down", "snapshot unavailable");
    } else {
      setStatus("stale", "refresh failed · showing last snapshot");
    }
    console.warn("snapshot refresh failed", err);
  }
}

window.addEventListener("resize", () => lastPayload && drawChart(lastPayload));
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refresh();
});

setStatus("live", "connecting…");
refresh();
setInterval(refresh, REFRESH_MS);
