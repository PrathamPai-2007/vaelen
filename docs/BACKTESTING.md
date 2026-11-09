# Backtesting

## Tools
| Tool | Input | Use |
|------|-------|-----|
| `backtest` (Rust) | binary tick file | replays ticks through the **real** `run_strategy_engine`, prints JSON |
| `combined_backtest` (Rust) | 5-minute candle `.bin` | runs Jurik trend + smart-money-flow bar strategies |
| `backtest/walk_forward.py` | `.npz` | Optuna walk-forward optimisation with bootstrap LCB profit factor |
| `backtest/download_klines.py`, `download_data.py`, `convert_data.py` | exchange data | fetch and convert inputs |

```bash
# tick replay
cargo run --release --bin backtest -- config.toml BTCUSD path/to/ticks.bin

# candle replay: <symbol> <file> [interval=300] [jurik|flow|combined] [target_notional=1000]
cargo run --release --bin combined_backtest -- BTCUSD backtest/data/BTCUSD_delta_5m_2026-02-25_2026-08-25.bin 300 combined 1000
```

Data files are gitignored (`backtest/data/`). Candle records are 56 bytes: `timestamp u64, open, high, low, close, volume, funding_rate` (f64).

## Cost model
All binaries use `costs.rs`: taker 0.05% / maker 0.02%, +18% GST on fees, slippage 5 bps on exits, funding tracked. Results are **net of costs** unless a field says `gross`.

## Results: bar strategies, 5-minute candles, 2026-02-25 to 2026-08-25
$1,000 notional per position, about 52,000 candles per symbol. `return_on_10000` is net PnL as a percent of a $10,000 account.

| Symbol | Strategy | Signals | Gross PnL | Fees (incl. GST) | Net PnL | Net return |
|--------|----------|--------:|----------:|-----------------:|--------:|-----------:|
| BTCUSD | jurik | 920 | -1,160 | 1,045 | -2,210 | -22.1% |
| BTCUSD | flow | 2,505 | -2,359 | 2,838 | -5,194 | -51.9% |
| BTCUSD | combined | 2,516 | -1,577 | 1,813 | -3,387 | -33.9% |
| ETHUSD | jurik | 814 | -815 | 951 | -1,763 | -17.6% |
| ETHUSD | flow | 2,493 | -2,870 | 2,906 | -5,775 | -57.7% |
| ETHUSD | combined | 2,394 | -1,276 | 1,846 | -3,122 | -31.2% |
| BNBUSD | jurik | 1,365 | -1,347 | 1,561 | -2,901 | -29.0% |
| BNBUSD | flow | 2,671 | -2,187 | 3,052 | -5,240 | -52.4% |
| BNBUSD | combined | 2,785 | -1,451 | 1,624 | -3,087 | -30.9% |
| XRPUSD | jurik | 910 | -1,342 | 1,073 | -2,436 | -24.4% |
| XRPUSD | flow | 2,567 | -2,105 | 3,020 | -5,108 | -51.1% |
| XRPUSD | combined | 2,508 | -1,518 | 1,885 | -3,418 | -34.2% |
| DOGEUSD | jurik | 1,002 | -1,431 | 1,177 | -2,622 | -26.2% |
| DOGEUSD | flow | 2,661 | -2,217 | 3,124 | -5,339 | -53.4% |
| DOGEUSD | combined | 2,591 | -1,866 | 1,867 | -3,746 | -37.5% |
| LINKUSD | jurik | 1,515 | -1,726 | 1,780 | -3,523 | -35.2% |
| LINKUSD | flow | 2,754 | -2,555 | 3,230 | -5,744 | -57.4% |
| LINKUSD | combined | 2,967 | -1,434 | 1,706 | -3,142 | -31.4% |

### What this says
- **These bar strategies have no edge on 5-minute Delta data.** Gross PnL is negative on all 18 runs, so costs are not the only problem; the signals themselves lose.
- Costs make it worse: more signals means more fees, which is why `flow` (about 2,500 signals) loses most.
- They are therefore **not enabled in the live engine**. The live universe and tick-level strategy are governed by the walk-forward gate in the [runbook](RUNBOOK.md): out-of-sample profit factor above 1 and bootstrap LCB above 1, with at least 30 trades per fold.
- The point of this harness is that a losing idea shows up as a losing number before any capital is risked.

Reproduce: run the `combined_backtest` command above for each symbol and strategy. The results are deterministic for a given data file.
