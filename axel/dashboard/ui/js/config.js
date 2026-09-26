/* ═══════════════════════════════════════════════════════════════
   AXEL TERMINAL · config.js — single place to edit all static data
   Mirrors axel/core/types.py · axel/risk/limits.py · README §5-7.
   Pipeline geometry (NODES/WIRES) lives here too: positions are %
   of the canvas, wires attach to sides L/R/T/B with optional px
   offsets (ox = source, oxt = target).
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

/* section → chart colors / short labels */
export const SECCOLOR = { STOCKS:"#a259ff", OPTIONS:"#1abcfe", CRYPTO:"#0acf83", FOREX:"#ffc857", PREDICTIONS:"#f24e1e" };
export const SECLABEL = { STOCKS:"Stocks", OPTIONS:"Options", CRYPTO:"Crypto", FOREX:"Forex", PREDICTIONS:"Bets" };
export const SECH     = { STOCKS:"STOCKS", OPTIONS:"OPTIONS", CRYPTO:"CRYPTO", FOREX:"FOREX", PREDICTIONS:"BETS" };

export const PK_COLORS = { agent:"#a259ff", rec:"#1abcfe", core:"#0acf83", order:"#ffc857", fill:"#f24e1e", halt:"#ff7262" };

export const NODES = {
  di: { t:"Data Ingest",   s:"bars · news · odds",  ico:"DI", c:"#1abcfe", x:5.5,  y:24 },
  fe: { t:"Feature Engine",s:"point-in-time",       ico:"FE", c:"#1abcfe", x:15,   y:24 },
  an: { t:"Analysts",      s:"LLM + ML",            ico:"AN", c:"#a259ff", x:25.5, y:24 },
  ep: { t:"Expert Panel",  s:"debate · weigh",      ico:"EP", c:"#a259ff", x:36,   y:24 },
  sm: { t:"Section Mgr",   s:"playbook",            ico:"SM", c:"#a259ff", x:46,   y:24 },
  ax: { t:"◆ AXEL",        s:"overseer LLM",        ico:"AX", c:"#a259ff", x:55.5, y:24, cls:"axel" },
  db: { t:"TimescaleDB",   s:"typed records",       ico:"DB", c:"#ffc857", x:69.5, y:52, w:126 },
  rk: { t:"Risk Package",  s:"validate→size→veto",  ico:"RK", c:"#0acf83", x:81.5, y:22 },
  ap: { t:"Approval",      s:"HITL · live only",    ico:"AP", c:"#0acf83", x:81.5, y:52 },
  ex: { t:"Execution",     s:"order FSM",           ico:"EX", c:"#0acf83", x:93,   y:22 },
  br: { t:"Broker",        s:"alpaca paper",        ico:"BR", c:"#f24e1e", x:93,   y:52 },
  wd: { t:"Watchdog",      s:"independent",         ico:"WD", c:"#ff7262", x:93,   y:81 },
  qr: { t:"Quant Research",s:"observe → PR only",   ico:"QR", c:"#8f9bab", x:46,   y:81 },
};

export const WIRES = [
  { a:"di", b:"fe",        sa:"R", tb:"L", lab:"bars/news" },
  { a:"fe", b:"an",        sa:"R", tb:"L", lab:"features" },
  { a:"an", b:"ep",        sa:"R", tb:"L", lab:"signals", kind:"rec" },
  { a:"ep", b:"sm",        sa:"R", tb:"L", lab:"vs playbook" },
  { a:"sm", b:"ax",        sa:"R", tb:"L", lab:"draft" },
  { a:"ax", b:"db",        sa:"R", tb:"L", lab:"Proposal · typed record", kind:"rec" },
  { a:"db", b:"rk",        sa:"R", tb:"L", lab:"read", kind:"rec" },
  { a:"rk", b:"ap",        sa:"B", tb:"T", lab:"RiskDecision" },
  { a:"ap", b:"ex",        sa:"R", tb:"B", lab:"approved" },
  { a:"rk", b:"ex",        sa:"R", tb:"L", lab:"guarded submit" },
  { a:"ex", b:"br",        sa:"B", tb:"T", lab:"order", ox:16, oxt:16 },
  { a:"br", b:"ex",        sa:"T", tb:"B", lab:"fills", ox:-16, oxt:-16 },
  { a:"ex", b:"wd",        sa:"R", tb:"R", lab:"♥", kind:"hb" },
  { a:"wd", b:"rk",        sa:"T", tb:"B", lab:"HALT", kind:"halt" },
  { a:"db", b:"qr",        sa:"L", tb:"R", lab:"observe", kind:"obs" },
];

export const PHASES = ["0 Foundations","1 Core","2 Data","3 Agents","4 Dash","5 Soak","6 Live","7 Crypto","8 Options","9 Bets","10 Research"];
