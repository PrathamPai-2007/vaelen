# Writing strategies

There are two ways to produce signals.

## 1. Rhai script (tick level)
Set `script_path` on a symbol in `config.toml` (the file must be under `strategies/`). The script defines one function, called on every trade tick:

```rhai
fn on_tick(ctx) {
    // sell into hidden buy absorption: CVD rising, price not
    if ctx.size > ctx.p95_vol && ctx.current_cvd > ctx.past_cvd && ctx.price <= ctx.past_price {
        ctx.sell();
    }
}
```

### `ctx` fields (read-only)
| Field | Meaning |
|-------|---------|
| `price`, `size`, `is_buy` | the current trade |
| `current_cvd`, `past_cvd` | cumulative volume delta now and at the lookback point |
| `past_price` | price at the lookback point |
| `rolling_volume` | taker volume over the lookback window |
| `p95_vol` | 95th percentile 1-minute trade size |
| `atr` | 5-minute ATR |
| `ticks_since_entry` | ticks since the last entry on this symbol (use for cooldowns) |

### Actions
`ctx.buy()` and `ctx.sell()`. Calling both in one tick cancels the signal; calling neither does nothing.

### Sandbox limits
500 operations per call, strings 512 chars, arrays 16 items, no `eval`, no `import`. A script that errors is logged and skipped for that tick; it cannot crash the engine. The compiled file's hash is logged at start so you can show live and backtest ran the same script.

The shipped example is `strategies/iceberg_fade.rhai`. The tests in `scripting.rs` show how to unit-test a script.

## 2. Bar strategies (candle level, Rust)
`bar_strategies.rs` provides:
- `CandleAggregator`: ticks to fixed-interval candles
- `JurikTrend`: trend state (Green/Red)
- `SmartMoneyFlow`: money-flow buy/sell signals
- `combine(jurik, flow, previous)`: trade only with the trend, exit when the trend flips

These run in `combined_backtest` today ([BACKTESTING.md](BACKTESTING.md)); they are not wired into the live engine.

## Checklist before trusting a strategy
1. Unit test the signal logic.
2. Backtest net of fees, GST and slippage (the default in every binary).
3. Check it out of sample: `backtest/walk_forward.py` reports a bootstrap lower confidence bound on profit factor per fold. Require LCB > 1.
4. Paper trade for a week before any live key is set.
