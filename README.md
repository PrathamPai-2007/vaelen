# Vaelen - Quantitative Order Flow & Institutional Absorption Trading System

Vaelen is a production-grade, high-performance quantitative trading system designed for **Delta Exchange India** and crypto perpetual markets. It uses a low-latency **Rust HFT core** for both live tick-level order flow absorption and blisteringly fast historical backtesting. A modular **Python orchestration suite** manages data ingestion, multi-core Walk-Forward Optimization (WFO), and parameter sweeps.

---

## 🚀 Core Trading Strategy

### 𞟊 Institutional Iceberg-Absorption Fade Model (Native Rust Engine)
A high-frequency microstructural model detecting passive liquidity absorption in real-time.
* **Volume-Weighted Price Impact**: Detects institutional iceberg absorption by measuring price movement relative to aggressive taker volume over a sliding lookback window (`|Delta P| / V_cum`). Fades massive taker volume when price fails to break support/resistance.
* **P95 Volume Spike Gate**: Ensures trades only trigger on significant volume bursts exceeding the rolling 95th percentile volume buffer.
* **Execution & Friction Optimization**: Asynchronous Rust engine executing maker limit entries to capture fee rebates with zero-allocation hot paths and bounded `VecDeque` ring buffers.

### � Vaelen Scripting Engine (VSE)
Vaelen now features a **"PineScript-like" Execution Environment** powered by Rhai. You no longer need to modify the core Rust engine to test new strategies. Simply write your logic in a `.rhai` file and point the config to it.

**Zero-Overhead Native Indicators**
To maintain nanosecond performance, the Rust engine computes all complex rolling indicators natively on every tick and instantly exposes them to your script's context.

**Example Script (`strategies/iceberg_fade.rhai`)**:
```rust
fn on_tick(ctx) {
    let delta_price = ctx.price - ctx.past_price;
    let price_impact = 0.0;
    if ctx.rolling_volume > 0.0 {
        price_impact = delta_price.abs() / ctx.rolling_volume;
    }

    let volume_spike = ctx.size > ctx.p95_vol;
    let can_absorb = volume_spike && ctx.rolling_volume > 10000.0 && price_impact < 0.0001;


    if can_absorb {
        if ctx.current_cvd > ctx.past_cvd && delta_price <= 0.0 {
            ctx.sell(); // Fade aggressive buying at ceiling
        } else if ctx.current_cvd < ctx.past_cvd && delta_price >= 0.0 {
            ctx.buy(); // Fade aggressive selling at floor
        }
    }
}
```

---

## 💲 Zero-Latency Telegram Alerts
Vaelen is built with an integrated, async Telegram notifier that broadcasts trading activity securely without blocking the HFT core, 

To enable notifications, add the following to `config.toml`:
A�`toml
[notifications.telegram]
enabled = true
bot_token = "123456789:YOUR_BOT_TOKEN_HERE"
chat_id = "YOUR_CHAT_ID_HERE"
```
Alerts include entry direction, prices, size, exact Take-Profit/Stop-Loss outcomes, and executed PnL.

---

## �B Repository Structure

```text
.
├── Cargo.toml                          # Rust project manifest & dependencies
├─– config.toml                         # Unified configuration for Rust HFT & Python suite
├── strategies/                         # � User-provided Rhai scripts
Ⓞ    ├─– iceberg_fade.rhai               # Reference PineScript-like strategy
✜── .env                                # Environment API credentials (DELTA_API_KEY / SECRET)
├─– src/                                # Rust Low-Latency Core (Live & Backtest)
✄    ├── main.rs                         # Live WebSocket ingestion & order dispatch entry point
✄    ├─– bin/backtest.rs                 # ⚡ Native Rust High-Performance Historical Backtester
✄    ├─– telegram.rs                     # 💲 Asynchronous zero-latency Telegram alert actor
✄    ├─– config.rs                       # Strongly-typed TOML parser (AppConfig, SymbolConfig)
✄    ├── scripting.rs                    # �| Vaelen Scripting Engine wrapper mapping AST hooks
✄    └── strategy_engine.rs              # Zero-allocation iceberg fade state machine
└── backtest/                           # Python Orchestration & Data Ingestion Suite
    ├── walk_forward.py                 # Multi-core Walk-Forward Optimization using Optuna
    ├─– convert_data.py                 # Converts raw CSV to 32-byte C-struct flat binaries (.bin)
    └── symbol_validation.py            # Dynamic product API configuration validator
```

---

