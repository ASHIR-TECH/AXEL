/* ═══════════════════════════════════════════════════════════════
   AXEL TERMINAL · main.js — entry point (loaded as <script
   type="module">). Wires live feed → UI; falls back to the browser
   simulation only when the server feed is unreachable, or when the
   page is opened with ?sim=1. The visual components are unchanged.
   dep rule: state ← market ← agents ← scheduler; ui ← sim + config.
   ═══════════════════════════════════════════════════════════════ */

import { layoutPipeline } from "./ui/pipeline.js";
import * as desk from "./ui/desk.js";
import * as trade from "./ui/trade.js";
import "./ui/shell.js";
import { bootLive } from "./live/adapter.js";

async function boot() {
  layoutPipeline();
  const params = new URLSearchParams(location.search);
  let live = false;

  if (params.get("sim") !== "1") {
    try {
      live = await bootLive();
    } catch (error) {
      console.warn("[axel] live feed unavailable — using the browser simulation", error);
    }
  }

  if (!live) {
    window.AXEL_LIVE = false;
    const { start } = await import("./sim/scheduler.js");
    start();
  }

  desk.boot();
  trade.boot();
  addEventListener("resize", () => { layoutPipeline(); desk.drawEquity(); });
}

boot();
