# Vaelen — Delta Exchange High-Frequency Trading Engine

> **Rust-native, fault-tolerant trading engine for Delta Exchange (India) perpetuals** — real-time WebSocket ingestion, deterministic strategy engine with paper/live dual mode, Rhai-scriptable signals, ATR-aware risk management, and a Python + Rust backtesting harness with walk-forward optimization.

---

## At a glance

```
Delta WS trades ─► ingestion ─► strategy engine ─► OrderManager ─► Delta REST (live) / fill model (paper)
 (reconnect +      bounded       Rhai or built-in    HMAC-signed,     │
  watchdog)        channel       signals + risk      idempotent       └─► SessionTracker ─► CSV / Telegram
```

**Quick start (paper mode, no keys needed)**

```bash
cp config.example.toml config.toml
cargo run --release            # paper trading is the default
cargo test                     # 31 unit tests
```

**Engineering guarantees**

- **Paper by default.** Live mode refuses to start without `DELTA_API_KEY` / `DELTA_API_SECRET` in the environment. Secrets never live in files.
- **Same code, paper or live.** Backtests replay ticks through the real `run_strategy_engine`.
- **No double orders.** Every order has a UUID `client_order_id`; ambiguous responses are reconciled by lookup, not re-sent. A circuit breaker stops trading after repeated failures.
- **Realistic costs.** One model (`src/costs.rs`): maker 0.02% / taker 0.05%, +18% GST, slippage, funding. Paper fills only occur when the market trades through the order.
- **Sandboxed scripting.** Strategies are Rhai files with an operation cap, no `eval`/`import`, restricted to `strategies/`.
- **CI-gated.** fmt, clippy `-D warnings`, tests and `cargo audit` on Windows and Linux.

**Honest research results.** The candle-based Jurik/smart-money-flow strategies lose money on 5-minute Delta data across BTC, ETH, BNB, XRP, DOGE and LINK, even before fees (18 runs, 6 months of data). They are not enabled live. The harness exists to show that before capital is at risk; see [docs/BACKTESTING.md](docs/BACKTESTING.md).

**Developer docs:** [docs/](docs/README.md) — [Architecture](docs/ARCHITECTURE.md) · [Development](docs/DEVELOPMENT.md) · [Strategies](docs/STRATEGIES.md) · [Backtesting](docs/BACKTESTING.md) · [Runbook](docs/RUNBOOK.md)

---

## Table of Contents

- [1. Overview](#1-overview)
- [2. Key Features](#2-key-features)
- [3. Architecture & Data Flow](#3-architecture--data-flow)
- [4. Project Structure](#4-project-structure)
- [5. Technology Stack](#5-technology-stack)
- [6. Configuration (`config.toml`)](#6-configuration-configtoml)
  - [6.1 General](#61-general)
  - [6.2 WebSocket](#62-websocket)
  - [6.3 Order Manager](#63-order-manager)
  - [6.4 Fees](#64-fees)
  - [6.5 Strategy (per-symbol)](#65-strategy-per-symbol)
  - [6.6 Session Tracking](#66-session-tracking)
  - [6.7 Notifications](#67-notifications)
  - [6.8 Validation Rules](#68-validation-rules)
  - [6.9 Environment Overrides](#69-environment-overrides)
- [7. Trading Strategy](#7-trading-strategy)
  - [7.1 CVD Iceberg Fade (Built-in)](#71-cvd-iceberg-fade-built-in)
  - [7.2 Rhai Scripting (`strategies/`)](#72-rhai-scripting-strategies)
  - [7.3 Bar Strategies (`bar_strategies.rs`) — Jurik Trend & Smart Money Flow](#73-bar-strategies-bar_strategiesrs--jurik-trend--smart-money-flow)
- [8. Module Reference (`src/`)](#8-module-reference-src)
  - [8.1 `main.rs` — Orchestrator](#81-mainrs--orchestrator)
  - [8.2 `config.rs` — Configuration Loader](#82-configrs--configuration-loader)
  - [8.3 `websocket.rs` — Ingestion Feed](#83-websocketrs--ingestion-feed)
  - [8.4 `strategy_engine.rs` — Core Engine](#84-strategy_enginers--core-engine)
  - [8.5 `orders.rs` — Order Manager](#85-ordersrs--order-manager)
  - [8.6 `session.rs` — Analytics](#86-sessionrs--analytics)
  - [8.7 `scripting.rs` — Rhai Bridge](#87-scriptingrs--rhai-bridge)
  - [8.8 `bar_strategies.rs` — Indicator Library](#88-bar_strategiesrs--indicator-library)
  - [8.9 `telegram.rs` — Notifications](#89-telegramrs--notifications)
  - [8.10 `lib.rs` — Crate Root](#810-librs--crate-root)
- [9. Order Management & Execution Guarantees](#9-order-management--execution-guarantees)
- [10. Risk & Position Management](#10-risk--position-management)
- [11. Session Tracking & Analytics](#11-session-tracking--analytics)
- [12. Backtesting Suite](#12-backtesting-suite)
  - [12.1 Rust Binaries](#121-rust-binaries)
  - [12.2 Python Harness (`backtest/`)](#122-python-harness-backtest)
  - [12.3 Binary Data Format](#123-binary-data-format)
- [13. Binaries & Utilities](#13-binaries--utilities)
- [14. Logging & Observability](#14-logging--observability)
- [15. Setup & Installation](#15-setup--installation)
- [16. Running the Engine](#16-running-the-engine)
- [17. Backtest Workflows](#17-backtest-workflows)
- [18. Testing](#18-testing)
- [19. Deployment & Operations](#19-deployment--operations)
- [20. Archived Research](#20-archived-research)
- [21. FAQ & Troubleshooting](#21-faq--troubleshooting)
- [22. Roadmap](#22-roadmap)
- [23. Contributing](#23-contributing)
- [24. License](#24-license)

---

## 1. Overview

Vaelen (`delta_trading_engine` crate, `src/main.rs:23`) is a **production-grade HFT engine** targeting Delta Exchange India perpetual futures (`https://api.india.delta.exchange`, `wss://public-socket.india.delta.exchange`). It was built to:

- Ingest every public trade tick over WebSocket with automatic reconnection and watchdog supervision.
- Maintain per-symbol state (price/CVD/volume queues, 5-minute candle ATR, 1-minute volume P95) entirely in-memory via lock-free-friendly `parking_lot::RwLock` and bounded `VecDeque` queues.
- Evaluate entry signals either from the **built-in CVD Iceberg Fade** logic or from **user-supplied Rhai scripts** (`strategies/*.rhai`).
- Manage the full lifecycle of paper and live positions (entry → passive TP limit → SL / trailing-stop / timeout exit) with deterministic CSV journaling.
- Survive exchange outages (circuit breaker, exponential backoff with jitter, idempotent `client_order_id` reconciliation) without leaking positions.

Paper mode is the default (`config.toml:8` `paper_trade_mode = true`); flipping one flag routes real orders through the signed REST API (`src/orders.rs:131`).

> **Crate name:** `delta_trading_engine` (`Cargo.toml:2`) · **Edition:** 2021 · **Entry point:** `src/main.rs:23` (`#[tokio::main]`).

---

## 2. Key Features

| Area | Highlights |
|------|------------|
| **Ingestion** | `tokio-tungstenite` WebSocket with `User-Agent` spoofing, 30 s TCP keepalive, `FeedStale`/`FeedResumed` broadcast, 5 s watchdog (`src/websocket.rs:224`), exponential backoff + ±20% jitter (`src/websocket.rs:250`). |
| **Strategy** | CVD divergence + volume-spike + price-impact filter (`src/strategy_engine.rs:1135`), 5-min candle ATR (`src/strategy_engine.rs:248`), 1-min volume P95 (`src/strategy_engine.rs:326`), Rhai hot-scriptable `on_tick` (`src/scripting.rs:66`), Jurik Trend + Smart Money Flow bar indicators (`src/bar_strategies.rs:136`). |
| **Risk** | Per-symbol BPS-based TP/SL, ATR-multiplied trailing stop with floor, `hold_ticks` timeout, `max_concurrent_positions` global cap, `entry_cooldown_ticks` anti-churn. |
| **Execution** | HMAC-SHA256 signing (`src/orders.rs:38`), idempotent `client_order_id` (UUID v4), retry with `retry_on_status` (`config.toml:53`), duplicate-order reconciliation via `GET /v2/orders/client_order_id/{id}` (`src/orders.rs:424`), circuit breaker after N consecutive failures (`src/orders.rs:315`). |
| **Paper Trading** | Realistic limit-order fill model (must **trade through**, not just touch, `src/strategy_engine.rs:373`), 15-tick maker timeout, fee + slippage accounting identical to live path. |
| **Analytics** | `SessionTracker` (`src/session.rs:36`) — gross/net PnL, win rate, profit factor, Sharpe/Sortino (annualized), max drawdown, equity curve, CSV exports. |
| **Ops** | Structured `tracing` with `EnvFilter`, optional JSON logs + daily rolling file (`src/main.rs:30`), Telegram HTML alerts (`src/telegram.rs:8`), heartbeat every 60 s, graceful `Ctrl-C` shutdown (`src/main.rs:144`). |
| **Backtest** | Rust tick-replay binaries (`src/bin/backtest.rs`, `combined_backtest.rs`) + Python harness (`backtest/walk_forward.py`) with Optuna TPE, bootstrap LCB profit factor, walk-forward CV, and automatic under-performance code-switch. |

---

## 3. Architecture & Data Flow

```
┌─────────────────┐      mpsc<EngineMessage>       ┌──────────────────────┐
│  WebSocket Feed │ ─────────────────────────────► │   Strategy Engine    │
│  websocket.rs   │   Trade / FeedStale/Resumed    │  strategy_engine.rs  │
│  run_ingestion_ │                                │  run_strategy_engine │
│  feed           │                                │                      │
└────────┬────────┘                                └──────┬───────┬───────┘
         │  broadcast shutdown                             │       │
         │                                                 │       ├──► OrderManager (REST, HMAC)
         │         ┌─────────────────┐                     │       │    orders.rs
         │         │   SessionTracker│◄────────────────────┘       │
         │         │   session.rs    │                             ├──► CSV journal (trades/*.csv)
         │         └────────┬────────┘                             │
         │                  │                                      └──► Telegram (optional)
         │                  │                                           telegram.rs
┌────────┴────────┐  ┌──────┴──────┐
│  Heartbeat 60s  │  │ Ctrl-C trap │
│  main.rs:130    │  │ main.rs:144 │
└─────────────────┘  └─────────────┘
```

**Concurrency model** (`src/main.rs:79`):

- `trade_tx/trade_rx` — bounded `mpsc` channel (capacity `trade_channel_buffer`, default 1000).
- `shutdown_tx` — `broadcast` channel (capacity 16) fanned to ingestion, strategy, and heartbeat tasks.
- `OrderManager` + `SessionTracker` — `Arc` shared; `SessionTracker` behind `parking_lot::RwLock`.
- Three `tokio::spawn` workers: ingestion, strategy, heartbeat. `tokio::select!` drives Ctrl-C shutdown and `join!` awaits termination.

**Message types** (`src/websocket.rs:43`):

```rust
pub enum EngineMessage { Trade(TradeEvent), FeedStale, FeedResumed }
```

Feed staleness **halts new entries** in live mode (`src/strategy_engine.rs:491`) but never blocks exits.

---

## 4. Project Structure

```
vaelen/
├── Cargo.toml                  # Crate manifest (tokio, rhai, reqwest, hmac, etc.)
├── Cargo.lock
├── config.toml                 # Live engine config (checked in as example; override via env)
├── .env                        # API credentials (gitignored) — DELTA_API_KEY / SECRET
├── .gitignore
├── src/
│   ├── lib.rs                  # Crate root re-exports (src/lib.rs:1)
│   ├── main.rs                 # Orchestrator, logging setup, task spawning
│   ├── config.rs               # AppConfig structs + validation + env overlay
│   ├── websocket.rs            # WS ingestion, decode_trade_side, backoff+jitter
│   ├── strategy_engine.rs      # SymbolState, ATR, P95, signal & position logic
│   ├── orders.rs               # OrderManager, signing, retry, circuit breaker
│   ├── session.rs              # SessionTracker, Sharpe/Sortino, CSV exports
│   ├── scripting.rs            # VaelenScriptEngine (Rhai) + ScriptContext
│   ├── bar_strategies.rs       # CandleAggregator, JurikTrend, SmartMoneyFlow, combine
│   ├── telegram.rs             # TelegramNotifier (spawn + mpsc)
│   └── bin/
│       ├── backtest.rs         # Tick-replay backtester (binary trade format)
│       ├── combined_backtest.rs# Candle-replay Jurik+Flow backtester
│       ├── verify.rs           # GET /v2/wallet/balances credential check
│       └── test_live_api.rs    # 4-step REST + signing smoke test
├── strategies/
│   └── iceberg_fade.rhai       # Example Rhai strategy (on_tick)
├── backtest/
│   ├── requirements.txt        # hftbacktest, numpy, pandas, toml, optuna, matplotlib
│   ├── backtest_common.py      # Binance download → HFTBacktest npz conversion
│   ├── convert_data.py         # CSV → flat binary (.bin) for Rust backtester
│   ├── download_data.py        # Binance Vision daily trades downloader
│   ├── download_klines.py
│   ├── run_backtest.py         # Thin wrapper that builds & runs Rust backtest binary
│   ├── walk_forward.py         # Rolling WFO (Optuna, bootstrap LCB PF, codeswitch)
│   ├── walk_forward_patch.py
│   ├── symbol_validation.py
│   ├── test_live_bot_functionality.py
│   ├── data/                   # (gitignored) raw .csv / .zip
│   ├── processed/              # (gitignored) .npz / .bin
│   └── logs/
├── archive/
│   └── iceberg_fade_v1/
│       ├── README.md           # Kill notice for v1 hypothesis
│       └── reconciled_diagnostic_report.md  # Full audit (see §20)
├── trades/                     # (gitignored) per-session CSV journals
├── logs/                       # (gitignored) daily rolling engine.log
└── target/                     # (gitignored) Rust build artifacts
```

---

## 5. Technology Stack

| Layer | Crate / Tool | Version | Notes |
|-------|--------------|---------|-------|
| Async runtime | `tokio` | 1.37 | `full` features |
| WebSocket | `tokio-tungstenite` | 0.21 | `native-tls` |
| HTTP | `reqwest` | 0.12 | `json` feature, keepalive 30 s |
| Serialization | `serde` + `serde_json` | 1.0 | `derive` |
| Crypto | `hmac` + `sha2` + `hex` | 0.12 / 0.10 / 0.4 | HMAC-SHA256 signing |
| Time | `chrono` | 0.4 | `serde` feature, `Local` timestamps |
| Logging | `tracing` + `tracing-subscriber` + `tracing-appender` | 0.1 / 0.3 / 0.2 | `env-filter`, `fmt`, `json`, daily rolling |
| Config | `config` + `toml` | 0.15 / 0.8 | File + `DELTA__` env overlay |
| Concurrency | `parking_lot` | 0.12 | `RwLock` for `SessionTracker` |
| Scripting | `rhai` | 1.26 | `sync` feature |
| Utilities | `anyhow`, `thiserror`, `uuid` (v4), `rand`, `serde_with`, `url`, `dotenvy`, `futures-util` | — | — |
| Backtest (Python) | `hftbacktest`, `numpy`, `pandas`, `optuna`, `matplotlib`, `toml` | see `backtest/requirements.txt:1` | — |

MSRV is governed by `edition = "2021"` (`Cargo.toml:4`).

---

## 6. Configuration (`config.toml`)

All tuning is file-driven — **no recompilation** needed. Loaded via `AppConfig::load()` (`src/config.rs:207`) which reads `config.toml` + `DELTA__` env overlay and then calls `validate()` (`src/config.rs:135`).

### 6.1 General

```toml
[general]
api_base_url = "https://api.india.delta.exchange"
paper_trade_mode = true          # true = paper, false = live REST orders
max_concurrent_positions = 5     # global cap across all symbols (src/strategy_engine.rs:1158)
trade_channel_buffer = 1000      # mpsc capacity (src/main.rs:79)
trades_dir = "trades"            # CSV journal dir (src/strategy_engine.rs:442)
log_level = "info"               # trace|debug|info|warn|error
json_logs = false                # true = JSON to stdout
log_file = "logs/engine.log"     # optional daily rolling file (None to disable)
```

### 6.2 WebSocket

```toml
[websocket]
ws_url = "wss://public-socket.india.delta.exchange"
symbols = ["BTCUSD", "ETHUSD", "BNBUSD", "XRPUSD", "DOGEUSD", "LINKUSD"]
ping_interval_secs = 0           # 0 = rely on server pings
watchdog_timeout_secs = 30       # no-msg timeout → FeedStale + reconnect (src/websocket.rs:80)
reconnect_base_secs = 1
reconnect_max_secs = 60
user_agent = "Mozilla/5.0 ..."
```

Backoff is `2×` with ±20% jitter, clamped to `[1, max]` (`src/websocket.rs:250`).

### 6.3 Order Manager

```toml
[order_manager]
request_timeout_secs = 10
max_retries = 5                  # per-order retry budget (src/orders.rs:213)
retry_base_delay_secs = 1        # exponential: base * 2^attempt
retry_max_delay_secs = 60
retry_on_status = [429, 500, 502, 503, 504]
track_fills = true               # use place_order_with_retry path
fill_timeout_secs = 30           # wait_for_fill deadline (src/orders.rs:487)
# max_consecutive_failures = 3   # circuit breaker threshold (default, src/config.rs:72)
```

### 6.4 Fees

```toml
[fees]
taker_fee_rate = 0.0005           # 5 bps market orders
maker_fee_rate = 0.0002           # 2 bps limit orders (informational; engine uses taker)
slippage_bps = 5.0                # 5 bps per round-trip leg (src/strategy_engine.rs:742)
```

Fees/slippage are applied symmetrically on entry + exit for PnL accounting.

### 6.5 Strategy (per-symbol)

Each entry in `[[strategy.symbols]]` (`config.toml:69`) maps to `SymbolConfig` (`src/config.rs:94`):

| Field | Type | Meaning |
|-------|------|---------|
| `symbol` | `String` | Exchange symbol, must match `websocket.symbols` |
| `product_id` | `u64` | Delta product ID (e.g. BTCUSD = 27) |
| `contract_size` | `f64` | Contracts → notional multiplier (BTC 0.001, ETH 0.01, DOGE 100) |
| `order_size` | `i64` | Contracts per entry |
| `tick_size` | `f64` | Minimum price increment (floor for ATR) |
| `stop_loss_bps` | `f64` | SL distance in bps (e.g. 15 = 0.15%) |
| `take_profit_bps` | `f64` | TP distance in bps (e.g. 45 = 0.45%) |
| `hold_ticks` | `usize` | Max ticks before forced timeout exit |
| `entry_cooldown_ticks` | `u64` | Ticks since last entry before new entry allowed |
| `trailing_stop_atr_mult` | `f64` | Trailing distance = `max(ATR*mult, min_distance)` |
| `min_trailing_stop_distance` | `f64` | Absolute floor (price units) |
| `atr_period` | `usize` | Candles for ATR mean (default 14) |
| `lookback_ticks` | `usize` | Ticks for CVD/price divergence window |
| `max_price_impact_threshold` | `f64` | `abs(delta_price)/cum_volume` ceiling |
| `volume_threshold` | `f64` | *(legacy, superseded by P95)* |
| `min_cvd_notional_usd` | `f64` | Minimum rolling volume to consider signal |
| `max_capacity` | `usize` | `VecDeque` capacity; must be > `lookback_ticks` |
| `strategy_type` | `String` | `"cvd_iceberg"` (default, `src/config.rs:32`) or custom |
| `script_path` | `Option<String>` | Path to `.rhai` file; if set, script takes priority |

Example (BTCUSD):

```toml
{ symbol = "BTCUSD", product_id = 27, contract_size = 0.001, order_size = 1,
  tick_size = 0.5, stop_loss_bps = 15.0, take_profit_bps = 45.0,
  hold_ticks = 600, entry_cooldown_ticks = 2000,
  trailing_stop_atr_mult = 1.5, min_trailing_stop_distance = 1.0,
  atr_period = 14, lookback_ticks = 24,
  max_price_impact_threshold = 1e-6, volume_threshold = 0.5,
  min_cvd_notional_usd = 10000.0, max_capacity = 100 }
```

### 6.6 Session Tracking

```toml
[session_tracking]
enabled = true
risk_free_rate = 0.05            # annualized, for Sharpe
equity_snapshot_interval = 100    # ticks between equity samples
initial_equity = 10000.0
ticks_per_day_estimate = 10000.0  # legacy alias (prefer below)
estimated_ticks_per_day = 50000.0 # used for annualization (src/session.rs:191)
```

### 6.7 Notifications

```toml
[notifications.telegram]
enabled = false
bot_token = ""
chat_id = ""
```

When enabled, `TelegramNotifier::spawn` (`src/telegram.rs:8`) opens a 100-slot `mpsc` channel and posts HTML-formatted alerts for entries and exits.

### 6.8 Validation Rules

`AppConfig::validate()` (`src/config.rs:135`) enforces:

- `api_base_url` non-empty and valid URL.
- `websocket.symbols` non-empty; `trade_channel_buffer` / `max_concurrent_positions` / `equity_snapshot_interval` > 0.
- `reconnect_base_secs` > 0 and `reconnect_max_secs` ≥ base.
- Per-symbol: `max_capacity` > 0, `0 < lookback_ticks < max_capacity`, `atr_period` > 0, `order_size` > 0, `contract_size`/`tick_size` finite & positive.
- `0 ≤ maker/taker fee ≤ 1`.

Failures surface as `anyhow!("Configuration validation failed: ...")` (`src/config.rs:219`).

### 6.9 Environment Overrides

Any `config.toml` key can be overridden via `DELTA__` env vars with `__` as separator (`src/config.rs:214`):

```bash
DELTA__GENERAL__PAPER_TRADE_MODE=false
DELTA__GENERAL__LOG_LEVEL=debug
DELTA__WEBSOCKET__WATCHDOG_TIMEOUT_SECS=60
```

Credentials are **not** in `config.toml`; they come from `.env` / env:

```
DELTA_API_KEY=...
DELTA_API_SECRET=...
```

---

## 7. Trading Strategy

### 7.1 CVD Iceberg Fade (Built-in)

**Hypothesis:** aggressive taker volume that *fails* to move price indicates hidden (iceberg) liquidity absorbing flow — a mean-reversion opportunity.

**State maintained per symbol** (`src/strategy_engine.rs:68`):

- `price_queue`, `cvd_queue`, `size_queue`, `side_queue` — bounded `VecDeque`s (capacity `max_capacity`).
- `current_cvd` — cumulative volume delta (`+size` on buy, `-size` on sell, `src/strategy_engine.rs:533`).
- `rolling_volume_sum` / `rolling_buy/sell_volume` — incremental O(1) rolling sums over `lookback_ticks`.
- `candle_tr_buffer` + `curr_atr` — 5-minute candle True Range mean (`src/strategy_engine.rs:248`).
- 1-minute volume P95 — `volume_buffer` (1000 slots) + `cached_p95_volume` recomputed every 5 closed minutes (`src/strategy_engine.rs:326`).

**Signal logic** (`src/strategy_engine.rs:1135`):

```
past_price = price_queue[current_len - 1 - lookback_ticks]
past_cvd   = cvd_queue[current_len - 1 - lookback_ticks]
delta_price = price - past_price
cum_volume  = rolling_volume_sum
price_impact = |delta_price| / cum_volume

volume_spike = size > cached_p95_volume          // size exceeds 95th percentile of 1m volumes
cooldown     = total_ticks - last_entry_tick >= entry_cooldown_ticks
can_absorb   = !feed_is_stale
            && volume_spike
            && cum_volume > min_cvd_notional_usd
            && price_impact < max_price_impact_threshold
            && cooldown

if can_absorb:
    if current_cvd > past_cvd && delta_price <= 0  → want_sell (fade buy pressure)
    if current_cvd < past_cvd && delta_price >= 0  → want_buy  (fade sell pressure)
```

**CVD semantics:** Delta's `trades` channel encodes `r` as *buyer role* (`t`/`taker` = buyer aggressor → CVD `+= size`, `m`/`maker` = seller aggressor → CVD `-= size`), with optional explicit `side` override. Decoded in `decode_trade_side()` (`src/websocket.rs:58`).

**Entry gating:** signal is ignored if `active_count >= max_concurrent_positions` (`src/strategy_engine.rs:1158`) or feed is stale (`src/strategy_engine.rs:1157`).

**Historical note:** the v1 Iceberg Fade hypothesis was **killed** after rigorous decoupled forward-return tests showed systematically negative edge (see §20). The current engine retains the same microstructure primitives but routes them through configurable per-symbol params and optional Rhai override — treat live deployment as experimental and validate via WFO (§12) first.

### 7.2 Rhai Scripting (`strategies/`)

When `script_path` is set for a symbol (`src/config.rs:122`), `VaelenScriptEngine::new()` (`src/scripting.rs:31`) compiles the file and `execute_on_tick()` (`src/scripting.rs:66`) is called each tick **before** the built-in check. Script signals take priority; built-in CVD logic is skipped when the script fires.

**Context injected** (`src/scripting.rs:9`):

| Field | Type | Description |
|-------|------|-------------|
| `price` | `f64` | Current trade price |
| `size` | `f64` | Current trade size |
| `is_buy` | `bool` | Aggressor is buy |
| `now_secs` | `u64` | `timestamp_ns / 1e9` |
| `current_cvd` | `f64` | Running CVD |
| `p95_vol` | `f64` | Cached 1-min P95 volume (`INF` until 10 samples) |
| `atr` | `f64` | Current 5-min ATR (0 during warmup) |
| `rolling_volume` | `f64` | Rolling volume sum |
| `past_price` | `f64` | Price `lookback_ticks` ago |
| `past_cvd` | `f64` | CVD `lookback_ticks` ago |
| `ticks_since_entry` | `u64` | Ticks since last entry |

**Script API:**

```rhai
fn on_tick(ctx) {
    // read: ctx.price, ctx.size, ctx.is_buy, ctx.current_cvd, ctx.p95_vol,
    //       ctx.atr, ctx.rolling_volume, ctx.past_price, ctx.past_cvd, ctx.ticks_since_entry
    // signal: ctx.buy(); ctx.sell();
}
```

**Example** (`strategies/iceberg_fade.rhai:1`):

```rhai
fn on_tick(ctx) {
    let price = ctx.price;
    let delta_price = price - ctx.past_price;
    let cum_taker_volume = ctx.rolling_volume;
    let price_impact = 0.0;
    if cum_taker_volume > 0.0 {
        price_impact = delta_price.abs() / cum_taker_volume;
    }
    let volume_spike = ctx.size > ctx.p95_vol;
    let cooldown_elapsed = ctx.ticks_since_entry >= 5000;
    let min_cvd = 10000.0;
    let max_impact = 0.0001;
    let can_absorb = volume_spike && cum_taker_volume > min_cvd
                  && price_impact < max_impact && cooldown_elapsed;
    if can_absorb {
        if ctx.current_cvd > ctx.past_cvd && delta_price <= 0.0 { ctx.sell(); }
        else if ctx.current_cvd < ctx.past_cvd && delta_price >= 0.0 { ctx.buy(); }
    }
}
```

Scripts are loaded once at engine start (`src/strategy_engine.rs:417`); compilation errors are logged and the symbol falls back to built-in logic.

### 7.3 Bar Strategies (`bar_strategies.rs` — Jurik Trend & Smart Money Flow)

Pure indicator library used by `combined_backtest` (candle domain). Not wired into the live tick engine by default, but available for research and future integration.

- **`CandleAggregator`** (`src/bar_strategies.rs:34`) — buckets ticks into fixed-interval candles (validates interval > 0, price finite, volume ≥ 0). `push()` returns a completed candle on bucket rollover.
- **`JurikTrend`** (`src/bar_strategies.rs:136`) — 15-stage Jurik filter (adaptive `alpha`/`beta` from `jurik_coefficients()`, `src/bar_strategies.rs:243`) with ATR-based ratchet. Emits `TrendState::Green/Red` and `Signal::Buy/Sell` on state flips after `warmup` bars.
- **`SmartMoneyFlow`** (`src/bar_strategies.rs:249`) — composite of momentum wave (double EMA), Chaikin Money Flow (`src/bar_strategies.rs:361`), and MFI, smoothed over `smoothing` bars. Signals on zero-cross.
- **`combine()`** (`src/bar_strategies.rs:382`) — gated entry: both indicators must be `ready`; Jurik state change forces `Exit`; otherwise requires `Green+Buy` or `Red+Sell` agreement.

---

## 8. Module Reference (`src/`)

### 8.1 `main.rs` — Orchestrator

- Loads `.env` via `dotenvy`, reads `DELTA_API_KEY`/`SECRET` with `dummy_*` fallback (`src/main.rs:75`).
- Builds `tracing_subscriber` with `EnvFilter` (env `RUST_LOG` or `config.general.log_level`), `fmt` layer (JSON or text), and optional daily rolling file (`src/main.rs:46`).
- Creates bounded `mpsc` + `broadcast` shutdown channels, `OrderManager` (`src/main.rs:84`), `SessionTracker` (`src/main.rs:92`), optional `TelegramNotifier` (`src/main.rs:100`).
- Spawns three tasks: `run_strategy_engine`, `run_ingestion_feed`, heartbeat loop (`src/main.rs:113`).
- `tokio::select!` on `Ctrl-C` → `shutdown_tx.send(())` → `join!` all tasks (`src/main.rs:144`).

### 8.2 `config.rs` — Configuration Loader

- Structs: `AppConfig`, `GeneralConfig`, `WebSocketConfig`, `OrderManagerConfig`, `FeesConfig`, `StrategyConfig`, `SymbolConfig`, `SessionTrackingConfig`, `NotificationsConfig`, `TelegramConfig` (`src/config.rs:5`).
- Defaults: `default_strategy_type()` → `"cvd_iceberg"` (`src/config.rs:32`), `default_max_consecutive_failures()` → `3` (`src/config.rs:72`).
- `AppConfig::load_from_path(path)` (`src/config.rs:211`) — `config` crate builder with `File::with_name` + `Environment::with_prefix("DELTA").separator("__")`.
- Unit tests: `test_valid_config_passes`, `test_invalid_url`, `test_invalid_fees` (`src/config.rs:230`).

### 8.3 `websocket.rs` — Ingestion Feed

- Types: `TradeMessage` (raw Delta JSON), `TradeEvent` (normalized), `EngineMessage` (`src/websocket.rs:14`).
- `decode_trade_side()` (`src/websocket.rs:58`) — documented buyer-role inversion.
- `run_ingestion_feed()` (`src/websocket.rs:73`) — outer reconnect loop + inner message loop; handles `Text`/`Binary` trades, `Ping→Pong`, `Close`, watchdog (5 s poll, `src/websocket.rs:224`), and `shutdown_rx`.
- `calculate_backoff_with_jitter()` (`src/websocket.rs:250`) — `next = clamp(2×current × jitter(0.8–1.21), 1, max)`, jitter from `subsec_nanos % 41`.
- Subscription: `{"type":"subscribe","payload":{"channels":[{"name":"trades","symbols":[...]}]}}` (`src/websocket.rs:121`).

### 8.4 `strategy_engine.rs` — Core Engine

- **Types:** `ActiveOrder`, `PaperTrade`/`PendingPaperOrder`, `LiveTrade`, `SymbolState` (`src/strategy_engine.rs:18`). `SymbolState::new(max_capacity)` pre-allocates all `VecDeque`s.
- **Helpers:**
  - `update_candle_atr()` (`src/strategy_engine.rs:248`) — buckets by `(ts / 300) * 300` (5-min), computes TR as `max(high-low, |high-prev_close|, |low-prev_close|)`, mean over `atr_period`, floor `max(tick_size*5, price*0.0005)`, warmup returns `0.0`. Tests: `test_candle_atr_known_sequence_and_warmup`, `test_zero_atr_floor` (`src/strategy_engine.rs:142`).
  - `compute_rolling_p95()` (`src/strategy_engine.rs:316`) — sort + `idx = len*0.95`, returns `INF` if `<10` samples.
  - `update_1m_volume_p95()` (`src/strategy_engine.rs:326`) — 1-min bucket, `volume_buffer` (1000), recompute P95 every 5 closes.
  - `update_pending_paper_orders()` (`src/strategy_engine.rs:358`) — trade-through fill (`price < limit` for buys, `price > limit` for sells), 15-tick timeout, test `test_paper_trade_limit_order_realism` (`src/strategy_engine.rs:195`).
- **Main loop** `run_strategy_engine()` (`src/strategy_engine.rs:401`) — documented in §3 & §10. Key constants: `HEARTBEAT_TICK_INTERVAL=200`, `BASIS_POINTS_DENOMINATOR=10000`, `SHUTDOWN_POLL_INTERVAL_MS=500`.

### 8.5 `orders.rs` — Order Manager

- `generate_signature()` (`src/orders.rs:38`) — `HMAC-SHA256(method+timestamp+path+payload)` hex-encoded.
- `OrderRequest`/`OrderResponse`/`FillInfo`/`OrderResult` (`src/orders.rs:13`), `OrderError` (`src/orders.rs:54`).
- `OrderManager::new()` (`src/orders.rs:99`) — `reqwest::Client` with keepalive 30 s, `pool_idle_timeout(None)`, configurable timeout; `AtomicU32` fail counter + `RwLock<bool>` circuit.
- `place_order()` (`src/orders.rs:131`) — builds POST `/v2/orders`, adds `api-key`/`signature`/`timestamp`, classifies `open`/`partial_fill`/`filled` by `status` string.
- `place_order_with_retry()` (`src/orders.rs:213`) — single `client_order_id` per logical order (stable across retries, `src/orders.rs:230`), duplicate detection (`400` + `duplicate` + `client_order_id` → `GET /v2/orders/client_order_id/{id}`), retry on `retry_on_status` + timeout/connect, exponential backoff, circuit breaker trip at `max_consecutive_failures`.
- `cancel_order()` (`src/orders.rs:341`) — DELETE `/v2/orders` with `product_id`+`id`.
- `get_order_status_raw/by_client_id/status()` (`src/orders.rs:388`) — GET helpers.
- `wait_for_fill()` (`src/orders.rs:487`) — poll loop until `Filled`/`Partial` or `fill_timeout_secs`.
- Tests: `test_circuit_breaker_transitions`, `test_client_order_id_stability`, `test_duplicate_client_order_id_recovery`, `test_genuine_400_error_trips_cb`, `test_duplicate_recovery_get_fails_degrades_gracefully` (`src/orders.rs:522`) — all use ephemeral `TcpListener` mocks.

### 8.6 `session.rs` — Analytics

- `TradeRecord` (per-trade), `EquitySnapshot` (timestamped equity/drawdown/peak), `SessionTracker` (`src/session.rs:10`).
- Counters: `trades_entered/closed`, `winning/losing`, `total_gross/fees/slippage/net`, `peak_equity`, `max_drawdown`, `equity_curve` (capacity 10000, `src/session.rs:6`).
- Methods: `record_trade()`/`record_trade_entry()`/`tick()`/`update_equity_snapshot()` (gated by `tick_counter % interval == 0`), `calculate_sharpe_ratio()`/`calculate_sortino_ratio()` (annualized via `sqrt(252 * snapshots_per_day)`, `src/session.rs:191`), `win_rate()`/`avg_win()`/`avg_loss()`/`profit_factor()`, `print_summary()`/`export_equity_curve()`/`export_trades()`.

### 8.7 `scripting.rs` — Rhai Bridge

- `VaelenScriptEngine` holds `rhai::Engine` + `AST` (`src/scripting.rs:4`).
- `new(script_path)` (`src/scripting.rs:31`) registers `ScriptContext` getters + `buy()`/`sell()` callbacks, compiles file via `engine.compile_file`.
- `execute_on_tick(ctx)` (`src/scripting.rs:66`) — pushes `ctx` clone into `Scope`, calls `on_tick`, reads back `should_buy/sell`.

### 8.8 `bar_strategies.rs` — Indicator Library

See §7.3.

### 8.9 `telegram.rs` — Notifications

- `TelegramNotifier::spawn(token, chat_id)` (`src/telegram.rs:8`) — creates `mpsc(100)`, spawns task looping on `rx.recv()` → `POST https://api.telegram.org/bot{token}/sendMessage` with `{"chat_id","text","parse_mode":"HTML"}`. Returns `Sender<String>` for engine to `try_send`.

### 8.10 `lib.rs` — Crate Root

Re-exports `config`, `orders`, `scripting`, `session`, `strategy_engine`, `telegram`, `bar_strategies`, `websocket` (`src/lib.rs:1`).

---

## 9. Order Management & Execution Guarantees

1. **Signing** — every REST call is HMAC-SHA256 signed over `METHOD + timestamp(secs) + path + json_payload` (`src/orders.rs:45`).
2. **Idempotency** — each logical order gets one `UUID v4` `client_order_id` (`src/orders.rs:230`) reused across retries, so a network partition that delivered the order but lost the response does **not** create a duplicate.
3. **Duplicate reconciliation** — on `400` containing `duplicate` + `client_order_id`, engine does `GET /v2/orders/client_order_id/{id}` (`src/orders.rs:266`) and maps the true state (`open`/`partial`/`filled`/`cancelled`) instead of blindly failing.
4. **Retry policy** — retries only on configured `retry_on_status` (default 429/5xx) plus timeout/connect errors (`src/orders.rs:306`); genuine 400s (e.g. invalid product) are **not** retried and immediately trip the breaker if `max_consecutive_failures` is reached (`src/orders.rs:704` test).
5. **Circuit breaker** — `AtomicU32 consecutive_failures` + `RwLock<bool> circuit_open` (`src/orders.rs:88`). Trips after `max_consecutive_failures` (default 3) consecutive *non-retryable* failures (`src/orders.rs:315`); all subsequent `place_order` calls fail fast with `CircuitBreakerOpen` until `reset_circuit_breaker()` is called.
6. **Entry orders** — placed as `limit` + `post_only=true` (`src/orders.rs:152`); if not filled within 5 s, cancelled (`src/strategy_engine.rs:577`).
7. **TP orders** — passive `limit` at `entry ± take_profit_bps` (`src/strategy_engine.rs:647`); tracked in `active_orders` with `kind="TP"`.
8. **Exit orders** — SL as taker `limit` at current price, trailing/timeout as `market` (`src/strategy_engine.rs:875`), with optional `track_fills` + `wait_for_fill` reconciliation on shutdown.

---

## 10. Risk & Position Management

| Mechanism | Config | Code |
|-----------|--------|------|
| **Take profit** | `take_profit_bps` (45 = 0.45%) | `entry * (1 ± tp_mult)` (`src/strategy_engine.rs:1181`) |
| **Stop loss** | `stop_loss_bps` (15 = 0.15%) | `entry * (1 ∓ sl_mult)` |
| **Trailing stop** | `trailing_stop_atr_mult` (1.5) + `min_trailing_stop_distance` | `dist = max(ATR*mult, floor)`; triggers when `highest - entry > dist` (long) then exits if `price ≤ highest - dist` (`src/strategy_engine.rs:697`) |
| **ATR** | `atr_period` (14), 5-min candles | `update_candle_atr()` (`src/strategy_engine.rs:248`), floor `max(tick*5, price*0.0005)` |
| **Timeout** | `hold_ticks` (600) | `ticks_elapsed >= hold_ticks` → `TIMEOUT` (`src/strategy_engine.rs:727`) |
| **Global cap** | `max_concurrent_positions` (5) | `active_count >= cap` → signal dropped (`src/strategy_engine.rs:1158`) |
| **Cooldown** | `entry_cooldown_ticks` (2000) | `total_ticks - last_entry_tick >= cooldown` (`src/strategy_engine.rs:1141`) |
| **Paper fill realism** | maker timeout 15 ticks | Must trade **through** limit (`price < limit` for buy, `src/strategy_engine.rs:373`), else order expires |

Exit priority (paper, `src/strategy_engine.rs:722`): `STOP-LOSS` > `TAKE-PROFIT` > `TRAILING-STOP` > `TIMEOUT` (live is similar but TP is a separate passive limit order, so `hit_tp` is not polled in the live exit loop, `src/strategy_engine.rs:848`).

Every exit records a `TradeRecord` with `gross_pnl`, `fees`, `slippage`, `net_pnl` (`src/strategy_engine.rs:787`), pushes to `SessionTracker`, sends Telegram alert, writes CSV line, and logs via `tracing::info!`.

On shutdown, remaining open trades are force-closed at `last_price` (paper) or via `market` order (live) with reason `SHUTDOWN` (`src/strategy_engine.rs:1236`).

---

## 11. Session Tracking & Analytics

`SessionTracker` (`src/session.rs:36`) is the single source of truth for post-session stats.

- **Equity snapshots** — taken every `equity_snapshot_interval` ticks (and on every `record_trade`), storing `equity = initial + total_net_pnl`, `drawdown = (peak - equity)/peak`, `peak_equity` (`src/session.rs:115`).
- **Sharpe** (`src/session.rs:157`) — mean excess return / std dev, annualized by `sqrt(252 * snapshots_per_day)` where `snapshots_per_day = ticks_per_day_estimate / interval`.
- **Sortino** (`src/session.rs:198`) — same but denominator is downside deviation; returns `INF` if no downside (`src/session.rs:224`).
- **Win rate / avg win/loss / profit factor** — derived from `trades: Vec<TradeRecord>` (`src/session.rs:245`).
- **Exports** — `export_equity_curve(path)` and `export_trades(path)` write CSV with headers (`src/session.rs:338`).
- **Console summary** — `print_summary()` prints a formatted block (`src/session.rs:301`).

---

## 12. Backtesting Suite

### 12.1 Rust Binaries

#### `backtest` — Tick-replay (`src/bin/backtest.rs:1`)

```
cargo run --release --bin backtest -- <config_path> <symbol> <binary_data_path>
```

- Loads `AppConfig` from `config_path`, forces `paper_trade_mode=true` (`src/bin/backtest.rs:36`).
- Reads flat binary `TickRecord` (`ts: u64, px: f64, qty: f64, is_buy: u8`, 32 bytes, `src/bin/backtest.rs:14`) via `unsafe slice::from_raw_parts`.
- Replays each tick as `TradeEvent` through the **real** `run_strategy_engine` (same code as live), then prints JSON:

```json
{"symbol":"BTCUSD","total_ticks":12345,"total_trades":7,
 "gross_pnl": 1.2345,"fees":0.0123,"slippage":0.0045,
 "net_pnl":1.2177,"win_rate":0.5714,"trades":[...]}
```

#### `combined_backtest` — Candle-replay (`src/bin/combined_backtest.rs:1`)

```
cargo run --release --bin combined_backtest -- <symbol> <candle_data_path> [interval_secs=300] [strategy=combined] [target_notional=1000]
```

- Reads `CandleRecord` (`timestamp, open, high, low, close, volume, funding_rate`, 56 bytes, `src/bin/combined_backtest.rs:6`) — enforces 300 s interval.
- Runs `JurikTrend` (14, 1.5, 14, 1.5, warmup 100) + `SmartMoneyFlow` (10, 21, 14, 4) per candle, combines via `combine()` (or `jurik`/`flow` solo).
- Position sizing: `contracts = floor(target_notional / (close * contract_value))`, at least 1.
- Costs: `fee_rate = 0.0005 * 1.18` (GST), `slippage = 5 bps`, funding PnL tracked (`src/bin/combined_backtest.rs:71`).
- Output JSON with `gross_pnl`, `fees_incl_gst`, `funding_pnl`, `net_pnl`, `return_on_10000`.

### 12.2 Python Harness (`backtest/`)

| Script | Purpose |
|--------|---------|
| `download_data.py` (`backtest/download_data.py:7`) | Download Binance Vision daily trades `.zip` (spot → fallback `futures/um`), extract to `backtest/data/`. Usage: `python download_data.py BTCUSDT 2024-01-15 [--market spot|futures/um]`. |
| `convert_data.py` (`backtest/convert_data.py:7`) | Convert Binance or Tardis CSV → flat `.bin` (32-byte `TickRecord`) for Rust `backtest`. Auto-detects header/binance vs tardis format. |
| `backtest_common.py` (`backtest/backtest_common.py:14`) | `download_and_convert_symbol_date()` — download → `pd.read_csv` → `hftbacktest` `event_dtype` → `.npz` in `processed/`. Handles `is_buyer_maker`, timestamp scaling, `BUY/SELL_EVENT`. |
| `run_backtest.py` (`backtest/run_backtest.py:7`) | Wrapper: builds `target/release/backtest` if missing, then shells out with `config + symbol + .bin`. |
| `walk_forward.py` (`backtest/walk_forward.py:211`) | **Core research harness** — rolling WFO (see below). |
| `download_klines.py` | Kline/candle download helper. |
| `symbol_validation.py` | Validates `contract_size`/`tick_size` vs sample price before WFO. |

#### Walk-Forward Optimization (`walk_forward.py:211`)

```
# Sweep both symbols with codeswitch
python backtest/walk_forward.py --sweep

# Single symbol, auto-discover .npz
python backtest/walk_forward.py 1000PEPEUSD
python backtest/walk_forward.py WIFUSD ./backtest/processed

# Explicit files
python backtest/walk_forward.py BTCUSD file1.npz file2.npz
```

- **Folds:** `WFO_FOLDS` (default 5) sequential train/test splits (`chunk_size = total // (folds+1)`).
- **Trials:** `WFO_TRIALS` (default 100) per fold, `WFO_JOBS` (default 8) parallel workers via `ProcessPoolExecutor` with `ask/tell` driver.
- **Search space** (`_suggest_params`, `walk_forward.py:131`): `take_profit_bps` 10–50, `stop_loss_bps` 5–50 (capped ≤ TP), `lookback_ticks` 10–40, `volume_threshold` 0.02–0.30, `max_price_impact` 1e-8–1e-4 (log), `min_cvd_notional` 10k–50k, `trailing_stop_atr_mult` 0.5–2.5, `min_trailing_stop_distance` 5e-5–0.005, `entry_cooldown` 1000–5000, `hold_ticks` 15–60.
- **Objective** (`objective_worker`, `walk_forward.py:149`): bootstrap 5th-percentile Profit Factor LCB (`calculate_bootstrap_pf_lcb`, `walk_forward.py:92`, 200 resamples) — penalizes small-sample luck.
- **Execution:** each trial dumps a temp TOML, invokes Rust `backtest` binary, parses JSON, returns `(gross_wins, gross_losses, fees, net, count, records)`.
- **Data hygiene:** `EXCLUDED_DAILY_SUFFIXES` (`walk_forward.py:170`) drops known Binance Futures mispriced archives; `discover_symbol_files` + `load_and_concat` sort by `exch_ts`.
- **Codeswitch** (`apply_codeswitch`, `walk_forward.py:405`): if `WIFUSD` (candidate) fails *any* of — majority folds PF>1, LCB PF>1 or mean PF>1, trades>0 — then `emergency_drop_wifusd()` regex-strips it from `config.toml` (`walk_forward.py:387`).

Env knobs: `WFO_FOLDS`, `WFO_JOBS`, `WFO_TRIALS`.

### 12.3 Binary Data Format

| File | Record | Size | Fields |
|------|--------|------|--------|
| Tick `.bin` (Rust backtest) | `TickRecord` (`src/bin/backtest.rs:14`) | 32 B (`repr(C)`) | `ts: u64` (ns), `px: f64`, `qty: f64`, `is_buy: u8` (1=buy), `padding: [u8;7]` |
| Candle `.bin` (combined) | `CandleRecord` (`src/bin/combined_backtest.rs:6`) | 56 B (`repr(C)`) | `timestamp: u64` (s), `open/high/low/close/volume/funding_rate: f64` |
| Processed `.npz` | `hftbacktest event_dtype` | — | `ev`, `local_ts`, `exch_ts`, `px`, `qty` |

---

## 13. Binaries & Utilities

| Binary | Source | Purpose |
|--------|--------|---------|
| `delta_trading_engine` | `src/main.rs` | Live/paper engine (default `cargo run`) |
| `backtest` | `src/bin/backtest.rs` | Tick-replay backtester |
| `combined_backtest` | `src/bin/combined_backtest.rs` | Candle-replay Jurik+Flow backtester |
| `verify` | `src/bin/verify.rs` | HMAC `GET /v2/wallet/balances` credential check (reads `.env`, prints status/body) |
| `test_live_api` | `src/bin/test_live_api.rs` | 4-step smoke test: tickers REST, HMAC signature, limit-order payload, cancel payload |

```bash
cargo run --bin verify
cargo run --bin test_live_api
cargo run --bin backtest -- config.toml BTCUSD ./backtest/processed/btc.bin
cargo run --bin combined_backtest -- BTCUSD ./backtest/processed/candles.bin 300 combined 1000
```

---

## 14. Logging & Observability

Configured in `src/main.rs:26`:

- `EnvFilter` from `RUST_LOG` env or `config.general.log_level`.
- `fmt` layer: `with_target(false)`, human-readable or `json()` if `json_logs=true`.
- Optional daily rolling file at `log_file` (`tracing_appender::rolling::daily`), JSON formatted, non-blocking.
- Log levels used: `info!` for lifecycle/signals/fills, `warn!` for feed staleness/timeouts/retries/circuit trips, `error!` for parse/order failures, `debug!` for per-200-tick heartbeats (`src/strategy_engine.rs:542`).
- Heartbeat task logs `System alive - HH:MM:SS` every 60 s (`src/main.rs:135`).
- All order/position events include symbol, price, side, PnL.

---

## 15. Setup & Installation

**Prerequisites:** Rust ≥ 1.75 (edition 2021), Python ≥ 3.9 (for backtest harness), Git.

```bash
# 1. Clone
git clone <repo-url> vaelen && cd vaelen

# 2. Rust deps (first build compiles ~60 crates)
cargo build --release

# 3. Python harness (optional, for backtesting)
python -m venv .venv && source .venv/bin/activate  # .venv/ on Windows
pip install -r backtest/requirements.txt

# 4. Credentials — create .env in project root (gitignored)
cat > .env <<'EOF'
DELTA_API_KEY=your_key
DELTA_API_SECRET=your_secret
PAPER_TRADE_MODE=true
EOF

# 5. Verify credentials (optional)
cargo run --bin verify
cargo run --bin test_live_api

# 6. Configure engine
cp config.toml config.toml.bak
$EDITOR config.toml   # set symbols, risk params, paper_trade_mode
```

> **Security:** `.env` is gitignored (`# Environment / Logs` in `.gitignore:20`). Never commit real keys. The example `config.toml` ships with paper mode on.

---

## 16. Running the Engine

```bash
# Paper mode (default) — no real orders, realistic fill simulation
cargo run --release

# Live mode — flip flag in config.toml or via env overlay
DELTA__GENERAL__PAPER_TRADE_MODE=false cargo run --release

# Custom log level
RUST_LOG=debug cargo run --release
DELTA__GENERAL__LOG_LEVEL=debug cargo run --release

# JSON logs
DELTA__GENERAL__JSON_LOGS=true cargo run --release
```

On start you should see:

```
INFO Starting Delta Trading Engine...
INFO Attempting WebSocket connection url=wss://public-socket.india.delta.exchange
INFO WebSocket connected successfully.
INFO Subscription payload sent payload={"type":"subscribe",...}
INFO Strategy engine initialized. Paper Trading: true. Symbols: ["BTCUSD", ...]
```

Trades are journaled to `trades/YYYY-MM-DD_HH-MM-SS.csv` (`src/strategy_engine.rs:443`) with header `symbol,entry_time,side,entry_price,exit_price,exit_reason,gross_pnl,fees,slippage,net_pnl`. Graceful shutdown on `Ctrl-C` force-closes open positions with `SHUTDOWN` reason.

**Telegram alerts** — set `[notifications.telegram]` in `config.toml` and restart; entry/exit messages are HTML-formatted (`src/strategy_engine.rs:802`).

---

## 17. Backtest Workflows

### Quick tick backtest (single symbol)

```bash
# 1. Download raw trades
python backtest/download_data.py BTCUSDT 2024-01-15

# 2. Convert to Rust binary format
python backtest/convert_data.py backtest/data/BTCUSDT-trades-2024-01-15.csv /tmp/btc.bin

# 3. Run Rust engine in replay mode
cargo run --release --bin backtest -- config.toml BTCUSD /tmp/btc.bin | tail -n1 | jq .

# Or via Python wrapper
python backtest/run_backtest.py --symbol BTCUSD --data /tmp/btc.bin --config config.toml
```

### Walk-forward optimization sweep

```bash
# Prepare .npz files (auto via backtest_common or manual)
# Then run WFO (5 folds, 100 trials/fold, 8 jobs — tune via env)
WFO_FOLDS=5 WFO_TRIALS=100 WFO_JOBS=8 python backtest/walk_forward.py --sweep
WFO_TRIALS=200 python backtest/walk_forward.py 1000PEPEUSD
```

Check `backtest/processed/` for `.npz` and `backtest/data/` for raw CSVs (both gitignored).

---

## 18. Testing

```bash
# All Rust unit + integration tests (config, ATR, P95, paper fills, orders, WS backoff)
cargo test

# Verbose
cargo test -- --nocapture

# Single module
cargo test --lib config::tests
cargo test --lib strategy_engine::tests
cargo test --lib orders::tests
cargo test --lib websocket::tests
cargo test --lib bar_strategies::tests
```

**Test inventory:**

| Module | Tests | What they cover |
|--------|-------|-----------------|
| `config.rs:230` | 3 | valid config passes; invalid URL; fee bounds |
| `strategy_engine.rs:138` | 3 | candle ATR warmup & known sequence; zero-ATR floor; limit-order trade-through realism |
| `orders.rs:522` | 5 | circuit breaker transitions; client_order_id stability across retries; duplicate recovery via GET; genuine 400 trips breaker; GET-failure degradation |
| `websocket.rs:263` | 1 | backoff jitter bounds & cap |
| `bar_strategies.rs:401` | 2 | aggregator emits only completed candles; Jurik warms up and holds state |

Python harness has no formal test suite; validate via `python backtest/symbol_validation.py` and by running `walk_forward.py` on a small slice.

---

## 19. Deployment & Operations

- **Build:** `cargo build --release` produces `target/release/delta_trading_engine` (+ `backtest`, `combined_backtest`, `verify`, `test_live_api`). No runtime Python dependency for live trading.
- **Process supervision:** run under `systemd`, `pm2`, or Docker with `Restart=always`; the engine already handles WS reconnects internally, but a supervisor catches panics.
- **Resource profile:** per-symbol `VecDeque`s bounded by `max_capacity` (default 100) + `volume_buffer` 1000 + `candle_tr_buffer` 1000 — negligible RAM. CPU is dominated by per-tick ATR/P95 updates (O(1) amortized) and periodic P95 sorts (every 5 minutes, 1000 elements).
- **Clock:** ATR bucketing and fill timeouts use `timestamp_ns / 1e9` from exchange; ensure system clock is NTP-synced for HMAC timestamps.
- **Live checklist before `paper_trade_mode=false`:**
  1. `cargo run --bin verify` returns `SUCCESS`.
  2. `config.toml` `product_id`/`contract_size`/`tick_size` match Delta's current specs (validate via `symbol_validation.py`).
  3. `max_concurrent_positions` and `order_size` sized to your margin.
  4. `track_fills=true` and `fill_timeout_secs` tuned to your latency.
  5. Run a WFO sweep and confirm `mean OOS PF > 1` and `LCB PF > 1` — do **not** deploy a killed hypothesis (§20) without re-validation.
  6. Set `log_file` and `trades_dir` to persistent volumes.

---

## 20. Archived Research

### `archive/iceberg_fade_v1/` — Killed Hypothesis

| File | Description |
|------|-------------|
| `archive/iceberg_fade_v1/README.md` | Kill notice — systematic adverse selection, cross-symbol failure, unprofitability even at 100% fills. **Must not be deployed** without fundamental changes. |
| `archive/iceberg_fade_v1/reconciled_diagnostic_report.md` | Full audit: decoupled forward-return tests (win rates 6–36% across 10–1000 tick horizons, `p < 0.001`), 10-chunk volatility breakdown, fill-probability sensitivity (PF 0.65 even at instant fills). |
| `archive/iceberg_fade_v1/strategy.py` *(if present)* | Archived Python CVD implementation. |
| `archive/iceberg_fade_v1/config.toml` *(if present)* | Parameter snapshot at time of archival. |

> **Do not re-enable** the archived v1 config without addressing the root cause (fading aggressive volume = flow momentum, not absorption). Any revival should start from the walk-forward harness and a new signal hypothesis.

---

## 21. FAQ & Troubleshooting

**Q: WebSocket keeps reconnecting / `FeedStale` warnings?**
Check `websocket.ws_url` and firewall. The watchdog (`watchdog_timeout_secs=30`) trips if no `trades` messages arrive — low-liquidity symbols or pre-market hours can trigger it. Increase timeout or reduce `symbols` to liquid pairs.

**Q: `Configuration validation failed` on start?**
Read the error — common causes: empty `api_base_url`, `lookback_ticks ≥ max_capacity`, `reconnect_max_secs < reconnect_base_secs`, missing `websocket.symbols`. See §6.8.

**Q: Orders fail with `CircuitBreakerOpen`?**
The breaker tripped after `max_consecutive_failures` non-retryable errors (e.g. bad `product_id`, insufficient margin). Fix the root cause, then call `reset_circuit_breaker()` (requires code change to expose an admin endpoint) or restart the process.

**Q: Paper trades never fill?**
Paper fills require **trade-through** (`src/strategy_engine.rs:373`): a buy limit at 100 only fills when `price < 100`, not `price == 100`. Tight limits in a flat market will time out after 15 ticks. Widen `take_profit_bps` or check `tick_size`.

**Q: `cargo test` hangs on order tests?**
Those tests bind ephemeral `TcpListener`s; a firewall or occupied port can stall them. Run `cargo test -- --test-threads=1` and check for `Address already in use`.

**Q: Backtest shows `Mean OOS PF ≈ 0.6` — is the strategy profitable?**
No. PF < 1 means gross wins < gross losses + fees. The reconciled report (§20) shows PF 0.65 even at instant fills — the edge is negative. Do not go live on that result.

---

## 22. Roadmap

- [ ] Expose `reset_circuit_breaker` via admin HTTP endpoint or Telegram command.
- [ ] Wire `bar_strategies` (Jurik + Smart Money Flow) into live `strategy_engine` as an alternative `strategy_type`.
- [ ] Per-symbol Rhai `on_exit` hook for custom risk logic.
- [ ] Prometheus metrics endpoint (ticks/s, fills, PnL, breaker state).
- [ ] Docker image + `docker-compose` with persistent `trades/` + `logs/` volumes.
- [ ] Multi-exchange abstraction (Binance, Bybit) behind a `MarketData` trait.

---

## 23. Contributing

1. Fork & branch from `main`.
2. `cargo fmt && cargo clippy -- -D warnings` — CI will reject unformatted code.
3. Add tests for new logic (especially `strategy_engine` and `orders`).
4. Update this README and `config.toml` comments if you add config keys.
5. Open a PR with a clear description and WFO results if touching strategy code.

---

## 24. License

Proprietary / All rights reserved unless a `LICENSE` file is added. Contact the repository owner for licensing terms.

---

*Vaelen `delta_trading_engine` v0.5.0 — Rust 2021 edition. Issues: https://github.com/PrathamPai-2007/vaelen/issues*
