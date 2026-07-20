# Institutional Order Flow Trading Engine

A high-performance, low-latency institutional order flow trading engine written in Rust with a parallelized zero-copy Python research and Walk-Forward Optimization (WFO) pipeline. Designed for high-frequency cryptocurrency perpetual markets on Delta Exchange.

The engine implements an **Institutional Iceberg-Absorption Fade Model** based on **Volume-Weighted Price Impact**, detecting passive wall absorption in real-time, executing maker limit entries to capture fee rebates, and enforcing strict volatility-adaptive risk boundaries.

---

## Core System Architecture

```mermaid
graph TD
    A[Delta Exchange Market Feed] -->|WebSocket / TCP_NODELAY| B[Ingestion Layer]
    B -->|tokio::sync::mpsc Bounded Channel| C[Strategy Engine]
    C --> D[SymbolState Bounded Queues]
    D -->|O(1) Hot Path| E{Iceberg Absorption Check}
    
    subgraph "Strategy Evaluation Circuit"
        E -->|Lookback Window| F[Volume-Weighted Price Impact: |ΔP| / V_cum]
        E -->|Block-Cached O(1)| G[95th Percentile Volume Filter]
        E -->|USD Notional Gate| H[Institutional Volume Threshold]
    end
    
    F & G & H -->|Signal Triggered| I{Paper Trade / Live Execution}
    I -->|Paper Mode| J[Paper Trade Simulator & Session Tracker]
    I -->|Live Mode| K[Order Manager & Lifecycle State Machine]
    K -->|Post-Only Maker Limit| L[Delta Exchange REST API]
    L -->|Fill Confirmed| M[Passive TP Limit + Taker SL Protection]
```

---

## Key Features & Micro-Structural Design

- **Volume-Weighted Price Impact Model**: Detects institutional absorption by measuring price movement relative to taker volume over a sliding lookback window ($|\Delta P| / V_{cum}$). Fades massive taker volume when price fails to break support/resistance.
- **Zero-Allocation Hot Path**: Bounded `VecDeque` ring buffers per symbol eliminate runtime heap allocations during tick processing.
- **O(1) Block-Cached 95th Percentile Filter**: Recomputes the rolling 1,000-tick 95th percentile volume every 500 ticks (`P95_UPDATE_INTERVAL`) with immediate cold-start initialization. Removes per-tick sorting and speeds up execution by over $30\times$.
- **Process-Isolated Zero-Copy WFO Engine**: Uses `ProcessPoolExecutor` paired with memory-mapped array slices (`np.load(..., mmap_mode="r")`) to bypass the Python GIL and scale across multi-core architectures with zero IPC data serialization overhead.
- **Automated Codeswitch Clause**: Continuously evaluates candidate symbols against Out-of-Sample (OOS) performance constraints ($PF_{fee} \ge 1.0$, non-zero trade count). Underperforming assets trigger an emergency drop, programmatically stripping them from `config.toml` and reverting to the isolated `1000PEPEUSD` production configuration.
- **Maker Order Execution & Timeout Protection**: Entries are posted as maker limit orders to capture maker fee rebates. Unfilled limit entries automatically time out after 5 seconds to prevent stale execution.
- **Asynchronous Architecture**: Built on Tokio with decoupled channel communication, non-blocking HTTP order management with retry backoff, and `TCP_NODELAY` socket optimization.

---

## Mathematical & Algorithmic Model

### 1. Cumulative Volume Delta (CVD) & Lookback Drift Prevention
For each incoming trade tick $i$ with price $P_i$, size $S_i$, and aggressor direction $D_i \in \{+1 \text{ (buy)}, -1 \text{ (sell)}\}$:

$$\text{CVD}_i = \text{CVD}_{i-1} + D_i \cdot S_i$$

The rolling volume over lookback window $L$ is precisely maintained without lookback drift:

$$V_{cum, i} = \sum_{k=i-L+1}^{i} S_k$$

### 2. Volume-Weighted Price Impact
$$\text{Price Impact}_i = \frac{|P_i - P_{i-L}|}{V_{cum, i}}$$

An absorption event occurs when $V_{cum, i} \cdot P_i > \text{min\_cvd\_notional\_usd}$, $S_i > \text{P95}(V)$, and:

$$\text{Price Impact}_i < \text{max\_price\_impact\_threshold}$$

### 3. Signal Generation
- **Short Fade**: Aggressive buying ($\text{CVD}_i > \text{CVD}_{i-L}$) but price hits a ceiling ($\Delta P_i \le 0$).
- **Long Fade**: Aggressive selling ($\text{CVD}_i < \text{CVD}_{i-L}$) but price hits a floor ($\Delta P_i \ge 0$).

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
└── backtest/                   # Python Quant Research & Optimization Suite
    ├── strategy.py             # Pure Python CVD Momentum & Absorption strategy
    ├── walk_forward.py         # Multi-process Optuna Walk-Forward Optimizer
    ├── run_backtest.py         # Historical tick backtest runner
    ├── download_data.py        # Binance Data Vision tick archive fetcher
    └── convert_data.py         # CSV to HFT binary .npz array converter
```

---

## Production Configuration (`config.toml`)

```toml
[general]
api_base_url = "https://api.india.delta.exchange"
paper_trade_mode = true
max_concurrent_positions = 5
trades_dir = "trades"
log_level = "info"

[websocket]
ws_url = "wss://public-socket.india.delta.exchange"
symbols = ["1000PEPEUSD"]

[strategy]
symbols = [
  { symbol = "1000PEPEUSD", product_id = 114716, contract_size = 1.0, order_size = 1000,
    tick_size = 0.00001, stop_loss_bps = 8.0, take_profit_bps = 25.0, hold_ticks = 600,
    entry_cooldown_ticks = 2000, trailing_stop_atr_mult = 1.655861, min_trailing_stop_distance = 0.000192,
    atr_period = 14, lookback_ticks = 24, max_price_impact_threshold = 1e-6,
    volume_threshold = 0.369142, min_cvd_notional_usd = 10000.0, max_capacity = 62 }
]
```

---

## Quick Start Guide

### 1. Build Rust Engine
```bash
cargo check
cargo build --release
```

### 2. Configure Environment Credentials
Create a `.env` file in the repository root:
```ini
DELTA_API_KEY=your_delta_api_key
DELTA_API_SECRET=your_delta_api_secret
```

### 3. Verify API Authentication
```bash
cargo run --bin verify
```

### 4. Run Live Engine (Paper Mode by default)
```bash
cargo run --release
```

### 5. Run Walk-Forward Optimization Sweep (Python)
```bash
python backtest/walk_forward.py --sweep
```

---

## License

MIT License. Developed for quantitative research and automated trading execution.
