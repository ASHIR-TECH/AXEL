/* ═══════════════════════════════════════════════════════════════
   AXEL TERMINAL · main.js — entry point (loaded as <script
   type="module">). Wires sim → UI: import order matters only in
   that UI modules must exist before start() emits events.
   dep rule: state ← market ← agents ← scheduler; ui ← sim + config.
   ═══════════════════════════════════════════════════════════════ */

import { layoutPipeline } from "./ui/pipeline.js";
import * as desk from "./ui/desk.js";
import * as trade from "./ui/trade.js";
import "./ui/shell.js";
import { start } from "./sim/scheduler.js";

layoutPipeline();
desk.boot();
trade.boot();
addEventListener("resize", () => { layoutPipeline(); desk.drawEquity(); });
start();
