# Vaelen Live Runbook (Windows)

## Universe & risk (locked)
- Live: `BTCUSD` + `ETHUSD` only. No memecoins.
- $2,000 notional/entry, max 2 concurrent (1/symbol) = $4k on $10k equity (~0.4x effective).
- Exchange leverage ceiling 10x (headroom only).
- Costs: taker 0.05%, maker 0.02%, +18% GST. Entries/TP are maker (queue-modeled).

## Start / stop
```powershell
# Paper smoke
.\scripts\run-live.ps1 -Config config.live.toml
# Live (requires env keys + verify green)
$env:DELTA_API_KEY="..."; $env:DELTA_API_SECRET="..."
.\scripts\run-live.ps1 -Live -Config config.live.toml
# Service
nssm start VaelenLive
nssm stop VaelenLive   # sends Ctrl-Break -> cancel orders -> market-close -> flush
```

## Pre-live checklist
1. `cargo run --release --bin verify` SUCCESS.
2. `config.live.toml` product_id/tick/contract match Delta `/v2/products`.
3. `track_fills=true`, timeouts 10/30s, `max_retries=5`.
4. WFO holdout: OOS PF>1 and LCB>1 per symbol, >=30 trades/fold.
5. `logs/` + `trades/` on persistent disk, outside OneDrive.

## Circuit breaker tripped (`CircuitBreakerOpen`)
1. Do NOT auto-reset. Read logs for root cause (bad product_id, margin, 4xx).
2. Fix config/keys/margin.
3. Restart the process (reset is manual-only by design).
4. Verify one paper signal fills via queue before re-enabling live.

## Feed stale
- Entries halt; exits continue. Check network/WS URL/symbols.
- Watchdog 30s -> auto-reconnect with jitter. `dropped_trades` counter in logs = backpressure; reduce symbols or raise `trade_channel_buffer`.

## Shutdown leaves a position
1. `trades/*.csv` last rows show `SHUTDOWN` exits.
2. If a live exit failed, the log has `CRITICAL: Failed to close position` with the exchange order id — reconcile via Delta UI/API by `client_order_id`.
3. Resting TPs are cancelled before market-close; TP fills close the linked trade as `TAKE-PROFIT`.

## Kill switch
Stop the service / press Ctrl-C (or Ctrl-Break). Engine cancels resting orders, market-closes live trades, drains maker queue as CANCELLED, flushes CSV, prints session summary.
