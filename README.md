# Vaelen - Institutional Order Flow & Arbitrage Trading Engine

A high-performance quantitative trading engine encompassing a low-latency Rust core for order flow momentum and a modular Python suite for structural arbitrage and strategy diagnostics. Designed primarily for cryptocurrency perpetual markets on **Delta Exchange India**.

---

## Core Strategies

The Vaelen engine implements multiple uncorrelated strategies:

### 1. Same-Venue Gold Funding Arbitrage (`XAUTUSD` vs `PAXGUSD`)
A delta-neutral structural arbitrage strategy capitalizing on a persistent funding rate divergence between two tokenized gold perpetuals on Delta Exchange India.
- **Edge**: `XAUTUSD` (Tether Gold) experiences high offshore leverage demand, carrying a continuous ~22% annualized funding rate. `PAXGUSD` (Paxos Gold) trades near traditional gold interest rates (~0.22%). 
- **Execution**: Long `PAXGUSD` / Short `XAUTUSD`. 
- **Risk Profile**: Single-venue portfolio margin nets out directional gold delta. Margin is only required to buffer transient basis de-peg noise ($\pm30-300$ bps), eliminating cross-exchange liquidation risk.
- **Engine**: Python-based Paper Trading & Live Execution engine (`backtest/paper_trader_gold_arb.py`).

### 2. Institutional Iceberg-Absorption Fade Model (Rust Core)
A high-frequency microstructural model detecting passive wall absorption in real-time.
- **Volume-Weighted Price Impact**: Detects institutional absorption by measuring price movement relative to taker volume over a sliding lookback window ($|\Delta P| / V_{cum}$). Fades massive taker volume when price fails to break support/resistance.
- **Execution**: Asynchronous Rust engine executing maker limit entries to capture fee rebates. Zero-allocation hot path with bounded `VecDeque` ring buffers.
- **O(1) Block-Cached 95th Percentile Filter**: Recomputes the rolling 1,000-tick 95th percentile volume every 500 ticks (`P95_UPDATE_INTERVAL`) with immediate cold-start initialization.

---

## Strategy Diagnostics, Evolution & Failure Points

Vaelen incorporates a rigorous quantitative pipeline with strict statistical bars ($p < 0.05$ bootstrap confidence, net-of-cost EV). A core philosophy of this project is to aggressively disprove strategies rather than blindly optimizing them. Below is the graveyard of strategies that were thoroughly audited and **killed** during development, along with their specific points of failure:

- **v1 Iceberg Fade (Mean Reversion)**: **KILLED**. Failed due to structural adverse selection. Limit orders providing liquidity were consistently run over during directional momentum expansion phases, causing asymmetrical losses that outweighed rebate capture.
- **v2 Momentum Breakout (incl. Scalper Offer)**: **KILLED**. Despite utilizing Delta Exchange's "Scalper Offer" (zero closing fee under 15/30 mins, effectively a 15.90 bps round-trip hurdle), the raw directional moves achieved by the signal (+0.50 to +4.40 bps) remained 4x–30x smaller than the necessary friction hurdle.
- **v3 Perpetual vs. Spot Basis Carry**: **KILLED**. While the perpetual funding rates offered strong yield, the physical spot execution friction (spot sell fee of 16.80 bps) completely destroyed the carry advantage. Total structural friction was ~35.06 bps.
- **v4 Cross-Exchange Perp-Perp Carry**: **KILLED**. Attempted to arbitrage funding between Binance and Bybit/Delta. The actual captured spread across 500 aligned 8h settlement periods was only 0.35–0.83 bps, massively dwarfed by asymmetric execution friction (25.76 bps). It also carried unacceptable cross-venue liquidation risk.
- **v5 Delta Options Synthetic Cash-and-Carry**: **KILLED**. Synthesizing a dated future via Put-Call Parity failed. The 4-leg execution fees (28.88 bps) and real order book bid-ask widths entirely consumed the implied basis premium (-37 to -47 bps net EV).
- **v6 Hedged Volatility Risk Premium (Iron Condor / Dynamic Stop-Loss)**: **KILLED**. Unhedged naked strangles showed high win rates but suffered catastrophic tail risk (-2,041 to -3,080 bps) from gap moves. Attempting to hedge this with OTM wings (Iron Condor) resulted in negative EV due to wing costs. Attempting to hedge with a dynamic stop-loss failed empirical gap-execution audits: exiting during post-breach market gaps resulted in -286 to -627 bps EV per cycle.

These extensive failures ultimately led to the discovery and validation of the **Same-Venue Gold Funding Arbitrage** strategy, which bypasses cross-venue liquidation risk, physical spot fees, and options bid-ask width by trading highly liquid perpetuals on a single margin engine.

---

## Repository Structure

```text
.
├── Cargo.toml                  # Rust project manifest
├── config.toml                 # Production engine & strategy configuration
├── .env                        # Environment credentials (API keys)
├── src/                        # Rust High-Speed Live Engine
│   ├── main.rs                 # WebSocket ingestion, strategy loop, order dispatch
│   ├── config.rs               # Strongly-typed configuration parser
│   ├── orders.rs               # REST OrderManager state machine
│   ├── session.rs              # Real-time PnL & Sharpe ratio tracker
│   └── bin/verify.rs           # API authentication verification tool
└── backtest/                   # Python Quant Research, Diagnostics, & Arbitrage Suite
    ├── paper_trader_gold_arb.py# Core Gold Funding Arb Execution Engine
    ├── run_gold_arb_paper.py   # Gold Arb CLI Runner & Telemetry Logger
    ├── symbol_validation.py    # Standing live product API validation
    ├── stress_test_gold_arb.py # Gold Arb De-Peg Margin Shock Simulator
    ├── strategy.py             # Pure Python CVD Momentum & Absorption strategy
    ├── walk_forward.py         # Multi-process Optuna Walk-Forward Optimizer
    └── [diagnostic scripts...] # Extensive suite of killed-strategy post-mortems
```

---

## Production Configuration (`config.toml`)

The configuration file handles both the Rust HFT engine and the Python Gold Arbitrage suite.

```toml
[general]
api_base_url = "https://api.india.delta.exchange"
paper_trade_mode = true

[gold_arb]
enabled = true
api_host = "https://api.india.delta.exchange"
leg_long = "PAXGUSD"
leg_short = "XAUTUSD"
effective_leverage = 3.0
position_sizing_pct = 0.50
max_depeg_stop_loss_bps = 300.0
paper_trading_initial_balance = 10000.0
```

---

## Quick Start Guide

### 1. Configure Environment Credentials
Create a `.env` file in the repository root:
```ini
DELTA_API_KEY=your_delta_api_key
DELTA_API_SECRET=your_delta_api_secret
```

### 2. Run the Gold Funding Arbitrage Paper Trader (Python)
Launch the paper trading engine to test execution and log real-time 8-hour funding accruals:

**Test Mode (Runs 3 cycles and exits):**
```bash
python backtest/run_gold_arb_paper.py --test-run --cycles 3
```

**Continuous Live Logging Mode (10s intervals):**
```bash
python backtest/run_gold_arb_paper.py --interval 10
```
Telemetry is automatically logged to `backtest/logs/gold_arb_telemetry.csv`.

### 3. Build & Run the Rust HFT Engine
```bash
cargo check
cargo build --release
cargo run --bin verify   # Verify API Auth
cargo run --release      # Run Live Engine (defaults to Paper Mode)
```

---

## License

MIT License. Developed for quantitative research and automated trading execution.
