# Architecture

Vaelen is one async Rust process with three long-lived tasks joined by a bounded channel and a shutdown broadcast.

```
 Delta WebSocket ──► ingestion (websocket.rs) ──mpsc<EngineMessage>──► strategy engine (strategy_engine.rs)
   trades channel     reconnect + watchdog          bounded, applies          │  per-symbol state, signals, risk
                                                    back-pressure             ├──► OrderManager (orders.rs) ─► Delta REST (live)
                                                                              │        or paper fill model (paper)
                                                                              ├──► SessionTracker (session.rs) ─► CSV / metrics
                                                                              └──► Telegram (telegram.rs, async, fire-and-forget)
 main.rs: wires config → tasks, heartbeat every 60 s, Ctrl-C / Ctrl-Break → broadcast shutdown → join all
```

## Data flow

1. **Ingest.** `websocket.rs` subscribes to the public `trades` channel for each configured symbol and emits `EngineMessage::Trade`. A watchdog sends `FeedStale` when no data arrives within `watchdog_timeout_secs`, and `FeedResumed` after reconnecting (exponential backoff with jitter). The engine uses these to avoid acting on stale state.
2. **State.** For each symbol the engine keeps bounded `VecDeque`s of price, CVD and volume, a 5-minute candle ATR, and a 1-minute volume P95. Everything is in memory; there is no database on the hot path.
3. **Signal.** Either the built-in CVD "iceberg fade" logic or a Rhai script (`strategies/*.rhai`, see [STRATEGIES.md](STRATEGIES.md)) says buy or sell. A script that sets both is ignored.
4. **Risk.** Per-symbol take-profit and stop-loss in bps, an ATR-scaled trailing stop with a floor, a `hold_ticks` timeout, `entry_cooldown_ticks`, and the global `max_concurrent_positions` cap.
5. **Execute.** `OrderManager` signs requests (HMAC-SHA256) and sends them with retries. Paper mode takes the same path through a fill model instead of REST.
6. **Record.** `SessionTracker` accumulates gross/net PnL, fees, slippage, win rate, profit factor, Sharpe/Sortino and drawdown. Trades are journalled to `trades/`.

## Design decisions worth knowing

- **Paper and live share one code path.** `paper_trade_mode` only changes the final execution step. The tick backtest binary calls the same `run_strategy_engine`, so backtest behaviour is live behaviour.
- **Paper fills are pessimistic.** A resting limit order fills only when the market trades *through* it, and only against the opposite side, so maker fills are not assumed for free (tests: `test_paper_trade_limit_order_realism`, `test_maker_queue_needs_opposite_side_and_clears_queue`).
- **One cost model.** `costs.rs` is the single source for maker 0.02% / taker 0.05% fees, +18% GST, slippage `max(half-spread, bps)` and contract sizing.
- **Orders are idempotent.** Each order carries a UUID `client_order_id`. On a duplicate or ambiguous response the manager looks the order up by that id instead of re-sending, so a retry cannot double a position.
- **Circuit breaker.** After N consecutive failures the manager refuses new orders (`CircuitBreakerOpen`). It does not auto-reset; see the [runbook](RUNBOOK.md).
- **Scripts are sandboxed.** Rhai runs with an operation cap, no `eval`/`import`, small string/array limits, and the file must live under `strategies/`.
- **Back-pressure over reordering.** The ingest channel is bounded (`trade_channel_buffer`); slow strategy code slows ingestion rather than reordering ticks.

## Module map

| File | Responsibility |
|------|----------------|
| `main.rs` | config, logging (console + daily JSON file), task wiring, graceful shutdown |
| `config.rs` | TOML + env loading, validation (URLs, fee ranges, symbol rules) |
| `websocket.rs` | ingestion, reconnect/backoff, trade-direction decoding, watchdog |
| `strategy_engine.rs` | per-symbol state, signals, risk, paper fill model |
| `orders.rs` | signing, retries, idempotency, circuit breaker |
| `costs.rs` | fees, GST, slippage, sizing |
| `session.rs` | performance analytics and CSV export |
| `scripting.rs` | Rhai bridge (`ScriptContext`, sandbox) |
| `bar_strategies.rs` | candle aggregator, `JurikTrend`, `SmartMoneyFlow`, `combine()` |
| `telegram.rs` | async alert sender |
| `bin/` | `backtest`, `combined_backtest`, `verify` (credential check), `test_live_api` |
