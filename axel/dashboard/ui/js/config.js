/* ═══════════════════════════════════════════════════════════════
   AXEL TERMINAL · config.js — single place to edit all static data
   Mirrors axel/core/types.py · axel/risk/limits.py · README §5-7.
   Pipeline geometry (NODES/WIRES) lives here too: positions are %
   of the canvas, wires attach to sides L/R/T/B with optional px
   offsets (ox = source, oxt = target). Wire labels sit at the path
   midpoint by default; lm (0..1 along path) plus lx/ly px nudges
   move them off a crossing.
   ═══════════════════════════════════════════════════════════════ */

export const LIMITS = {
  GLOBAL_MAX_DRAWDOWN: 0.10, SECTION_DAILY_STOP: 0.03, MAX_POSITION_PCT: 0.05,
  KELLY_CAP: 0.25, MAX_PER_TRADE_RISK_PCT: 0.01, MIN_RR_RATIO: 1.5,
  MAX_EXPECTED_R: 4.0, MIN_PANEL_CONFIDENCE: 0.30, MAX_OPEN_POSITIONS: 10,
  CORR_BLOCK: 0.85, MAX_REALLOC: 0.05,
};

export const SECTIONS = {
  STOCKS:      { alloc: .45, start: 4500, hitlTtl: 5,  venue: "ALPACA",   flatten: "HOLD",
                 syms: ["AAPL","NVDA","MSFT","TSLA","XOM","JPM","META","AVGO","LLY","CAT"] },
  OPTIONS:     { alloc: .25, start: 2500, hitlTtl: 10, venue: "ALPACA",   flatten: "HOLD",
                 syms: ["SPY 26SEP550C","QQQ 02OCT480P","SPY 26SEP600C","IWM 2200C","NVDA 1200P"] },
  CRYPTO:      { alloc: .15, start: 1500, hitlTtl: 2,  venue: "EXCHANGE", flatten: "FLATTEN",
                 syms: ["BTC-USD","ETH-USD","SOL-USD","AVAX-USD","LINK-USD"] },
  FOREX:       { alloc: .10, start: 1000, hitlTtl: 5,  venue: "OANDA",    flatten: "HOLD",
                 syms: ["EURUSD","USDJPY","GBPUSD","AUDUSD","USDCAD"] },
  PREDICTIONS: { alloc: .05, start: 500,  hitlTtl: 30, venue: "KALSHI",   flatten: "HOLD",
                 syms: ["KAL:FED-CUT","KAL:NBA-BOS","KAL:BTC>150K","KAL:CPI-OCT","KAL:SFR-DEC"] },
};

export const ANALYSTS = ["TECHNICAL","FUNDAMENTAL","SENTIMENT","MACRO/CARRY","VOL/GREEKS","ODDS/CALIB"];

/* section → chart colors / short labels. One hue per idea: gold = the
   brand/agent side, green = deterministic pass, amber = money in motion,
   slate = observe-only. */
export const SECCOLOR = { STOCKS:"#ecbf3a", OPTIONS:"#ffd75e", CRYPTO:"#3ddc97", FOREX:"#f0a83c", PREDICTIONS:"#8b9199" };
export const SECLABEL = { STOCKS:"Stocks", OPTIONS:"Options", CRYPTO:"Crypto", FOREX:"Forex", PREDICTIONS:"Bets" };
export const SECH     = { STOCKS:"STOCKS", OPTIONS:"OPTIONS", CRYPTO:"CRYPTO", FOREX:"FOREX", PREDICTIONS:"BETS" };

export const PK_COLORS = { agent:"#ecbf3a", rec:"#ffd75e", core:"#3ddc97", order:"#ecbf3a", fill:"#f0a83c", halt:"#ff5e4b", obs:"#8b9199" };

export const NODES = {
  di: { t:"Ingest",       s:"bars · odds", ico:"DI", c:"#9b7f2a", x:6.5,  y:18,
        info:[["sources","alpaca · polygon · fred · edgar · gdelt · kalshi"],["rule","point-in-time only — no lookahead"],["freshness","see [10] System Ops"]] },
  fe: { t:"Features",     s:"point-in-time",      ico:"FE", c:"#c8a13c", x:21.5, y:18,
        info:[["store","features (versioned)"],["keys","symbol · asof · feature"],["gate","PIT + quality gates before any fit"]] },
  an: { t:"Analysts",     s:"LLM + ML",           ico:"AN", c:"#ecbf3a", x:36.5, y:18,
        info:[["agents","6 analysts — see [07] Signals"],["emits","Signal(direction, strength, conf)"],["llm","allowed here, agent side only"]] },
  ep: { t:"Expert Panel", s:"debate · weigh",     ico:"EP", c:"#f0a83c", x:51.5, y:18,
        info:[["method","weighted debate, dissent kept"],["floor","MIN_PANEL_CONFIDENCE = 0.30"],["emits","Proposal"]] },
  sm: { t:"Section Mgr",  s:"playbook",           ico:"SM", c:"#ffd75e", x:45.5, y:52,
        info:[["input","signals + playbook"],["caps","5% per position · 5% realloc/cycle"],["cannot","hold broker keys"]] },
  ax: { t:"◆ AXEL",       s:"overseer LLM",       ico:"AX", c:"#0d0b04", x:45.5, y:84, cls:"axel",
        info:[["role","allocate, escalate, explain"],["authority","propose only — never execute"],["hitl ttl","5 / 10 / 2 / 5 / 30 min per section"]] },
  db: { t:"TimescaleDB",  s:"typed records",      ico:"DB", c:"#8b9199", x:59,   y:52,
        info:[["engine","Postgres + TimescaleDB"],["role","the ONLY thing crossing the boundary"],["tables","bars · features · signals · proposals · risk_decisions · orders · fills"]] },
  rk: { t:"Risk Engine",  s:"7 checks · veto", ico:"RK", c:"#3ddc97", x:74,   y:22,
        info:[["order","7 hard checks, fail-fast"],["checks","kill · stop · conf · pos · rr · corr · kelly"],["ci","LLM imports banned in axel/core"]] },
  ap: { t:"Approval",     s:"HITL · live",   ico:"AP", c:"#3ddc97", x:74,   y:52,
        info:[["mode","paper → AUTO_FLOW · live → human ticket"],["ttl","per section (OPTIONS 10m · BETS 30m)"],["emits","Approval + order release"]] },
  ex: { t:"Execution",    s:"order FSM",          ico:"EX", c:"#3ddc97", x:89,   y:22,
        info:[["fsm","NEW→SUBMITTED→PARTIAL→FILLED / CANCELED / REJECTED"],["realism","slippage · funding · partial fills · rejections"],["guard","only APPROVED decisions may submit"]] },
  br: { t:"Broker",       s:"alpaca paper",       ico:"BR", c:"#f0a83c", x:89,   y:52,
        info:[["venue","Alpaca paper (ALPACA_PAPER=true)"],["reconcile","broker⇄DB drift must be 0"],["halt policy","cancel_all + flatten per section"]] },
  wd: { t:"Watchdog",     s:"independent",        ico:"WD", c:"#ff5e4b", x:89,   y:81,
        info:[["scope","separate process, own DB user"],["trip","−10% peak-to-trough vs HWM"],["resume","manual only — F4"]] },
  qr: { t:"Research",     s:"read-only · PR",  ico:"QR", c:"#8b9199", x:16,   y:84,
        info:[["rights","read-only + PR drafts"],["never","no writes, nothing on the order path"],["observes","slippage · calibration · signal decay"]] },
};

/* Wire labels are terse on purpose: the right cluster has ~35px of
   clear space between columns, so the payload name rides the packet
   (pay) instead of the static chip. lm = fraction along the path,
   lx/ly = px nudges off it. ly 38 on the top row is what clears a 50px
   card sitting on the same line; nudges are measured at 1280px, the
   narrowest layout, which is also the worst case. */
export const WIRES = [
  { a:"di", b:"fe",  sa:"R", tb:"L", lab:"bars/news",           ly:38 },
  { a:"fe", b:"an",  sa:"R", tb:"L", lab:"features",            ly:38 },
  { a:"an", b:"ep",  sa:"R", tb:"L", lab:"signals", kind:"rec", pay:"Signal", ly:38 },
  { a:"ep", b:"sm",  sa:"B", tb:"T", lab:"playbook", ly:12 },
  { a:"sm", b:"ax",  sa:"B", tb:"T", lab:"draft" },
  { a:"ax", b:"db",  sa:"R", tb:"L", lab:"Proposal",  kind:"rec", pay:"Proposal" },
  { a:"db", b:"rk",  sa:"R", tb:"L", lab:"read",      kind:"rec", pay:"row" },
  { a:"rk", b:"ap",  sa:"B", tb:"T", lab:"verdict",   pay:"RiskDecision" },
  { a:"ap", b:"ex",  sa:"T", tb:"B", lab:"OK",        ox:-30, pay:"order ticket" },
  { a:"rk", b:"ex",  sa:"R", tb:"L", lab:"submit",    pay:"guarded", ly:38, lx:28 },
  { a:"ex", b:"br",  sa:"B", tb:"T",                 ox:16, oxt:16, pay:"ORDER" },
  { a:"br", b:"ex",  sa:"T", tb:"B",                 ox:-16, oxt:-16, pay:"FILL" },
  { a:"ex", b:"wd",  sa:"R", tb:"R", lab:"♥",         kind:"hb" },
  { a:"wd", b:"rk",  sa:"T", tb:"B", lab:"HALT",      kind:"halt", pay:"HALT", ly:40 },
  { a:"db", b:"qr",  sa:"L", tb:"T", lab:"observe",   kind:"obs", ox:38 },
];

export const PHASES = ["0 Foundations","1 Core","2 Data","3 Agents","4 Dash","5 Soak","6 Live","7 Crypto","8 Options","9 Bets","10 Research"];
