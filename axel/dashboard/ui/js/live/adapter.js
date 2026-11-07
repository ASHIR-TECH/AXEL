/* ═══════════════════════════════════════════════════════════════
   AXEL TERMINAL · live/adapter.js — server feed → existing event bus
   The terminal components are unchanged: this module only writes the
   durable snapshot into `S` and re-emits the same events that
   `sim/scheduler.js` used to emit. If the server is unreachable the
   caller falls back to the simulation explicitly (never silently).
   ═══════════════════════════════════════════════════════════════ */

import { S, emit } from "../sim/state.js";

const params = new URLSearchParams(location.search);
const TOKEN = params.get("token") || "";

const EVENT_NAMES = [
  "equity", "sections", "portfolio", "orders", "signals", "quotes",
  "ops", "alerts", "killEvents", "halt", "log",
  "risk:start", "risk:done", "watchdog",
];

const emittedLogIds = new Set();
let stream = null;
let lastRiskKey = null;

/* ---------- endpoint + badge ---------- */
function defaultApiBase() {
  const { protocol, hostname, port } = location;
  if (protocol === "file:") return "http://127.0.0.1:8000/api/v1";
  // The static dev server is not the API server.
  if (port === "8080" || port === "3000" || port === "5173")
    return `${protocol}//${hostname}:8000/api/v1`;
  return "/api/v1";
}

const API_BASE = params.get("api") || defaultApiBase();

function withToken(url) {
  if (!TOKEN) return url;
  return url + (url.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(TOKEN);
}

function setBadge(kind, detail) {
  const chip = document.getElementById("source-chip");
  if (chip) {
    chip.className = "source-chip " + kind;
    chip.querySelector("b").textContent =
      kind === "live" ? "LIVE" : kind === "retry" ? "LIVE · RETRY" : "SIM";
    chip.title = detail || "";
  }
  const note = document.getElementById("feed-note");
  if (note) {
    note.textContent = kind === "live"
      ? "agent feed live · durable records"
      : kind === "retry"
        ? "agent feed live · reconnecting to server"
        : "agent feed simulated in this draft";
  }
}

/* ---------- state application ---------- */
function pushLog(line) {
  if (!line || !line.id || emittedLogIds.has(line.id)) return;
  emittedLogIds.add(line.id);
  if (emittedLogIds.size > 2000) emittedLogIds.clear();
  S.logs.push(line);
  if (S.logs.length > 400) S.logs.shift();
  emit("log", line);
}

function renderRisk(risk, initial = false) {
  if (!risk || !risk.proposal) return;
  const key = `${risk.proposal.id}:${risk.verdict}`;
  if (key === lastRiskKey) return;
  lastRiskKey = key;
  emit("risk:start", risk.proposal);
  if (!initial) S.proposalsEvaluated += 1;
  if (risk.verdict === "PENDING") return;
  const checks = risk.checks || [];
  checks.forEach((c, i) =>
    setTimeout(() => emit("risk:check", { p: risk.proposal, c }), i * 140));
  setTimeout(() => emit("risk:done", {
    p: risk.proposal, verdict: risk.verdict, failed: risk.failed, checks,
  }), checks.length * 140 + 80);
}

function renderRiskDone(payload) {
  if (!payload || !payload.p) return;
  lastRiskKey = `${payload.p.id}:${payload.verdict}`;
  const checks = payload.checks || [];
  checks.forEach((c, i) =>
    setTimeout(() => emit("risk:check", { p: payload.p, c }), i * 140));
  setTimeout(() => emit("risk:done", payload), checks.length * 140 + 80);
}

function applySnapshot(snapshot) {
  S.t0 = snapshot.started_at ? Date.parse(snapshot.started_at) : Date.now();
  S.equity = snapshot.equity.equity;
  S.hwm = snapshot.equity.hwm;
  S.equityHist = (snapshot.equity.series || []).slice(-180);
  if (S.equityHist.length < 2) S.equityHist = [S.equity, S.equity];
  S.sections = snapshot.sections;
  S.realized = snapshot.portfolio.realized;
  S.trades = snapshot.portfolio.trades;
  S.wins = snapshot.portfolio.wins;
  S.holdings = snapshot.portfolio.holdings;
  S.orders = snapshot.orders;
  S.sig = snapshot.signals;
  S.ops = snapshot.ops;
  S.alerts = snapshot.alerts;
  S.quotePx = snapshot.quotes;
  S.halted = snapshot.halted;
  S.killEvents = snapshot.kill_events;
  S.proposalsEvaluated = snapshot.counters.proposals_evaluated;
  const knownLogIds = new Set(emittedLogIds);
  S.logs = (snapshot.logs || []).slice(-400);
  emittedLogIds.clear();
  const freshLogs = [];
  for (const line of S.logs) {
    emittedLogIds.add(line.id);
    if (!knownLogIds.has(line.id)) freshLogs.push(line);
  }

  emit("quotes", S.quotePx);
  emit("signals", S.sig);
  emit("equity", {
    equity: S.equity, hwm: S.hwm, dd: snapshot.equity.drawdown_pct,
  });
  emit("sections", S.sections);
  emit("portfolio", snapshot.portfolio);
  emit("orders", S.orders);
  emit("ops", S.ops);
  emit("alerts", S.alerts);
  emit("killEvents", S.killEvents);
  emit("halt", S.halted);
  for (const line of freshLogs) emit("log", line);
  renderRisk(snapshot.latest_risk, true);
  emit("watchdog");
}

function applyEvent(name, payload) {
  switch (name) {
    case "equity":
      S.equity = payload.equity; S.hwm = payload.hwm;
      S.equityHist.push(payload.equity);
      if (S.equityHist.length > 180) S.equityHist.shift();
      emit("equity", payload);
      break;
    case "sections": S.sections = payload; emit("sections", payload); break;
    case "portfolio":
      S.realized = payload.realized; S.trades = payload.trades;
      S.wins = payload.wins; S.holdings = payload.holdings;
      S.equity = payload.equity;
      emit("portfolio", payload);
      break;
    case "orders": S.orders = payload; emit("orders", payload); break;
    case "signals": S.sig = payload; emit("signals", payload); break;
    case "quotes": S.quotePx = payload; emit("quotes", payload); break;
    case "ops": S.ops = payload; emit("ops", payload); break;
    case "alerts": S.alerts = payload; emit("alerts", payload); break;
    case "killEvents": S.killEvents = payload; emit("killEvents", payload); break;
    case "halt": S.halted = payload; emit("halt", payload); break;
    case "log": pushLog(payload); break;
    case "risk:start":
      S.proposalsEvaluated += 1;
      emit("risk:start", payload);
      break;
    case "risk:done": renderRiskDone(payload); break;
    case "watchdog": emit("watchdog"); break;
    default: break;
  }
}

/* ---------- stream ---------- */
function openStream() {
  if (!("EventSource" in window)) {
    setBadge("sim", "this browser does not support EventSource");
    return;
  }
  stream = new EventSource(withToken(`${API_BASE}/dashboard/stream`));
  stream.addEventListener("open", () => setBadge("live", `server feed · ${API_BASE}`));
  stream.addEventListener("error", () =>
    setBadge("retry", "server stream disconnected — reconnecting"));
  stream.addEventListener("snapshot", event => {
    try { applySnapshot(JSON.parse(event.data)); }
    catch (error) { console.error("[axel] invalid snapshot", error); }
  });
  for (const name of EVENT_NAMES) {
    stream.addEventListener(name, event => {
      try { applyEvent(name, JSON.parse(event.data)); }
      catch (error) { console.error(`[axel] invalid ${name} event`, error); }
    });
  }
}

/* ---------- public entry point ---------- */
export async function bootLive() {
  setBadge("retry", `connecting to ${API_BASE}…`);
  let snapshot;
  try {
    const response = await fetch(withToken(`${API_BASE}/dashboard/snapshot`), {
      cache: "no-store",
      headers: TOKEN ? { Authorization: `Bearer ${TOKEN}` } : undefined,
    });
    if (!response.ok) throw new Error(`snapshot HTTP ${response.status}`);
    snapshot = await response.json();
  } catch (error) {
    window.AXEL_LIVE = false;
    setBadge("sim", `live feed unavailable — ${error.message}`);
    throw error;
  }
  window.AXEL_LIVE = true;
  applySnapshot(snapshot);
  setBadge("live", `server feed · ${snapshot.mode} · ${API_BASE}`);
  openStream();
  return true;
}

export { API_BASE };
