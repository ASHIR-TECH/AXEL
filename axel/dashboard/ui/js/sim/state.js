/* ═══════════════════════════════════════════════════════════════
   AXEL TERMINAL · sim/state.js — shared state + event bus + logging
   The UI is driven entirely through this bus, so it can be
   repointed at a real Postgres/websocket feed later with no
   structural change. Log channels: SYS · DB · CHAT · ANALYSIS ·
   RISK · EXEC (inferred from logger name, like axel.core.logging).
   ═══════════════════════════════════════════════════════════════ */

import { SECTIONS } from "../config.js";

export const S = {
  t0: Date.now(),
  speed: 1, paused: false, halted: false,
  equity: 10000, hwm: 10000,
  sections: {}, orders: [], proposalsEvaluated: 0, approvals: [],
  equityHist: [], logs: [], quotePx: {}, killEvents: [],
  holdings: [], realized: 0, trades: 0, wins: 0, losses: 0,
  sig: {}, seq: 1000,
  counters: { drifts: 0, ghosts: 0, unmatched: 0 },
  ops: { llmSpend: 4.2, llmBudget: 50,
    ingest: [ {src:"ALPACA · bars", lag: 1.2}, {src:"FRED · macro", lag: 46}, {src:"EDGAR · filings", lag: 120}, {src:"NEWS · gdelt", lag: 8}, {src:"KALSHI · odds", lag: 2.1} ] },
  alerts: [],
};

for (const [name, cfg] of Object.entries(SECTIONS)) {
  S.sections[name] = { nav: cfg.start, start: cfg.start, dayPnl: 0, positions: 2 + Math.floor(Math.random()*5),
    corr: .3 + Math.random()*.3, hist: [], blocked: false };
}
for (let i = 0; i < 160; i++) S.equityHist.push(9990 + Math.sin(i/17)*26 + (Math.random()-.5)*14 + i*0.05);
S.equityHist[159] = 10000;
S.equity = 10000; S.hwm = Math.max(...S.equityHist);
for (const name of Object.keys(SECTIONS)) {
  const sec = S.sections[name];
  for (let i = 0; i < 40; i++) sec.hist.push(sec.nav + (Math.random()-.5)*24 + i*.35);
}

/* ---------- helpers ---------- */
export const rnd  = (a, b) => a + Math.random() * (b - a);
export const pick = arr => arr[Math.floor(Math.random() * arr.length)];
export const chance = p => Math.random() < p;
const pad = (n, w = 2) => String(n).padStart(w, "0");
export const ts = (d = new Date()) =>
  `${d.getUTCFullYear()}-${pad(d.getUTCMonth()+1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}Z`;
export const money = v => (v < 0 ? "-$" : "$") + (Math.abs(v) >= 1000 ? (Math.abs(v)/1000).toFixed(2) + "K" : Math.abs(v).toFixed(2));
export const sleep = ms => new Promise(r => setTimeout(r, ms));

/* ---------- event bus ---------- */
const listeners = {};
export function on(evt, fn) { (listeners[evt] ||= []).push(fn); }
export function emit(evt, data) { (listeners[evt] || []).forEach(fn => fn(data)); }

/* ---------- logging ---------- */
const CH = { "axel.risk":"RISK", "axel.execution":"EXEC", "axel.db":"DB", "axel.agents":"CHAT", "axel.data":"SYS", "axel.watchdog":"SYS", "axel.core":"SYS", "axel.main":"SYS", "axel.alerts":"SYS" };
function channelOf(logger) {
  for (const k in CH) if (logger.startsWith(k)) return CH[k];
  return "SYS";
}
export function log(level, logger, msg, channel) {
  const line = { ts: ts(), level, logger, msg, ch: channel || channelOf(logger) };
  S.logs.push(line);
  if (S.logs.length > 400) S.logs.shift();
  emit("log", line);
}
export function alert(level, text) {
  const a = { level, text, at: ts().slice(11, 19) };
  S.alerts.unshift(a); if (S.alerts.length > 7) S.alerts.pop();
  emit("alerts", S.alerts);
  log(level === "CRITICAL" ? "CRITICAL" : "INFO", "axel.alerts", `ALERT dispatched · ${text}`, "SYS");
}

/* ---------- pause / speed control ---------- */
let resumeWaiters = [];
export function waitResume() { return new Promise(r => resumeWaiters.push(r)); }
export function setPaused(v) {
  S.paused = v ?? !S.paused;
  if (!S.paused) resumeWaiters.splice(0).forEach(f => f());
  emit("paused", S.paused);
}
export function setSpeed(v) { S.speed = v; }
