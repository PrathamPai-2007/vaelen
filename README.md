# Vaelen - Quantitative Order Flow, MACD Momentum & Arbitrage Trading System

Vaelen is a production-grade, high-performance quantitative trading system designed for **Delta Exchange India** and global crypto perpetual markets. It combines a low-latency **Rust HFT core** for tick-level order flow momentum and passive limit order execution with a modular **Python research and backtesting suite** for structural arbitrage and multi-dataset quantitative benchmarks.

---

## 🚀 Core Trading Strategies

Vaelen deploys three distinct, non-correlated quantitative strategies:

### 1. ⚡ 1-Minute Trend-Following MACD Momentum Strategy (`strategy_type = "macd_momentum"`)
A high-precision intraday momentum strategy that detects normalized MACD price expansions confirmed by a higher-timeframe trend filter.
* **Normalized MACD % Line**:
  $$\text{MACD}_{\%} = \left( \frac{\text{EMA}_{12} - \text{EMA}_{26}}{\text{Price}} \right) \times 100$$
* **15-Minute 200 EMA Macro Trend Filter**:
  * **Long Entry Trigger**: $\text{MACD}_{\%} > +0.015\%$ AND $\text{Price} > \text{EMA}_{200, 15\text{m}}$
  * **Short Entry Trigger**: $\text{MACD}_{\%} < -0.015\%$ AND $\text{Price} < \text{EMA}_{200, 15\text{m}}$
* **Risk & Exit Controls**:
  * Hard Stop-Loss: $1.5 \times \text{ATR}_{14}$
  * Target Take-Profit: $1:1.5$ Risk-Reward ($2.25 \times \text{ATR}_{14}$)
* **Execution & Friction Optimization**:
  * **Passive Limit (Maker) Entries**: Entries are placed as Limit orders (`order_type = "limit"`), incurring only $0.02\%$ maker fees with $0.0\text{ bps}$ entry slippage.
  * **Delta Scalper Offer (0% Closing Fee)**: Trades held $\le 15\text{ minutes}$ ($30\text{ minutes}$ for BTC/ETH) qualify for Delta Exchange's **0.00% closing fee discount**, saving over **71.4% in cumulative fee drag**.

---

### 2. 🏛️ Same-Venue Gold Funding Arbitrage (`XAUTUSD` vs `PAXGUSD`)
A delta-neutral structural arbitrage strategy capitalizing on persistent funding rate divergences between two tokenized gold perpetuals on Delta Exchange India.
* **Quantitative Edge**: `XAUTUSD` (Tether Gold) carries high offshore leverage demand, generating a continuous ~22% annualized funding rate. `PAXGUSD` (Paxos Gold) trades near traditional gold interest rates (~0.22%).
* **Execution**: Long `PAXGUSD` / Short `XAUTUSD`.
* **Risk Profile**: Single-venue portfolio margin nets out directional gold delta. Margin is only required to buffer transient basis de-peg noise ($\pm30-300\text{ bps}$), eliminating cross-exchange liquidation risk.
* **Engine**: Python Paper Trading & Live Execution engine (`backtest/paper_trader_gold_arb.py`).

---

### 3. 🧊 Institutional Iceberg-Absorption Fade Model (Rust Core)
A high-frequency microstructural model detecting passive liquidity absorption in real-time.
* **Volume-Weighted Price Impact**: Detects institutional absorption by measuring price movement relative to taker volume over a sliding lookback window ($|\Delta P| / V_{\text{cum}}$). Fades massive taker volume when price fails to break support/resistance.
* **Execution**: Asynchronous Rust engine executing maker limit entries to capture fee rebates. Zero-allocation hot path with bounded `VecDeque` ring buffers.

---

## 📁 Repository Structure

```text
.
├── Cargo.toml                          # Rust project manifest & dependencies
├── config.toml                         # Unified configuration for Rust HFT & Python suite
├── .env                                # Environment API credentials (DELTA_API_KEY / SECRET)
├── src/                                # Rust Low-Latency Live Engine
│   ├── main.rs                         # WebSocket trade ingestion, MACD & CVD engines, order dispatch
│   ├── config.rs                       # Strongly-typed TOML parser (AppConfig, MACDMomentumConfig)
│   ├── orders.rs                       # REST OrderManager state machine & retry logic
│   ├── session.rs                      # Real-time PnL, equity snapshots & Sharpe ratio tracker
│   ├── bin/verify.rs                   # API authentication & balance verification tool
│   └── bin/test_live_api.rs            # Live bot REST/WebSocket API verification binary
└── backtest/                           # Python Quantitative Research & Strategy Suite
    ├── strategy.py                     # Python MACDMomentumStrategy & CVDMomentumStrategy
    ├── test_macd_strategy.py           # Automated unit tests for MACD strategy logic
    ├── test_gold_arb.py                # Automated unit tests for Gold Arbitrage engine
    ├── test_live_bot_functionality.py  # Automated tests for real bot price fetching & order dispatch
    ├── symbol_validation.py            # Dynamic product API configuration validator
    ├── run_1month_expanded_backtest.py # 30-day expanded multi-symbol backtest runner
    ├── run_multi_symbol_1m_backtest.py # 7-day multi-symbol 1m MACD ranking runner
    ├── run_sol_sustained_backtest.py   # Sustained multi-day SOLUSD backtest runner
    ├── run_macd_benchmarks.py          # MACD threshold sensitivity benchmark runner
    ├── run_fast_summary.py             # Formatted terminal backtest summary runner
    ├── run_backtest.py                 # CLI backtest entry point
    ├── paper_trader_gold_arb.py        # Core Gold Funding Arbitrage Paper Trading Engine
    ├── run_gold_arb_paper.py           # Gold Arb CLI runner & CSV telemetry logger
    ├── download_data.py                # Binance Public Data daily trade tick downloader
    ├── convert_data.py                 # Raw trade CSV to binary HFT NPZ converter
    ├── stress_test_gold_arb.py         # Gold Arb de-peg shock simulator
    └── diagnose_scalper_offer.py       # Scalper Offer fee discount analyzer
```

---

## 🧪 Testing & Verification Suite

Vaelen includes a comprehensive, 100% softcoded automated testing suite covering strategy logic, risk rules, AND real live bot API execution:

### 1. Automated Python Strategy & Bot Tests
Run all 10 automated unit tests (strategy math, ATR stop loss, fee calculations, and live bot order flow payloads):
```bash
python -m unittest discover -s backtest -p "test_*.py"
```

### 2. Live Product API Validation
Verify active symbols (`SOLUSD`, `ETHUSD`, `BTCUSD`, `WIFUSD`, `PAXGUSD`, `XAUTUSD`) dynamically against Delta Exchange:
```bash
python backtest/symbol_validation.py
```

### 3. Rust Live Bot API Verification
Check Rust HMAC signature generation, REST ticker endpoints, and order dispatch state machines:
```bash
cargo check --bin test_live_api
```

---

## 🛠️ Quick Start Guide

### 1. Configure Environment Credentials
Create a `.env` file in the root directory:
```ini
DELTA_API_KEY=your_delta_api_key
DELTA_API_SECRET=your_delta_api_secret
```

### 2. Run Strategy Backtests & Benchmarks
Run 30-day expanded backtest:
```bash
python backtest/run_1month_expanded_backtest.py
```
Run 7-day multi-symbol ranking:
```bash
python backtest/run_multi_symbol_1m_backtest.py
```

### 3. Run Gold Funding Arbitrage Engine (Python)
```bash
# Test run (3 cycles):
python backtest/run_gold_arb_paper.py --test-run --cycles 3

# Continuous live telemetry (10s logging):
python backtest/run_gold_arb_paper.py --interval 10
```

### 4. Build & Launch Rust HFT Live Engine
```bash
cargo check
cargo run --bin verify   # Verify API Auth & Credentials
cargo run --release      # Launch Live/Paper Engine
```

---

## 📄 License

MIT License. Designed for quantitative strategy development, high-frequency execution, and market microstructure research.
