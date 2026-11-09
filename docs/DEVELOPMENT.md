# Development guide

## Prerequisites
- Rust stable (MSRV 1.75) and `cargo`.
- Python 3.10+ only for the optimisation harness in `backtest/` (`pip install -r backtest/requirements.txt`).
- Windows is the primary target (PowerShell scripts, NSSM service). Everything compiles on Linux and CI runs both.

## Everyday commands
```bash
cargo build                                   # debug build
cargo test                                    # 31 unit tests
cargo fmt --check                             # CI enforces this
cargo clippy --all-targets -- -D warnings     # CI enforces this (warnings are errors)
cargo run                                     # paper mode with ./config.toml
cargo run -- config.live                      # alternate config (no .toml suffix)
```
CI (`.github/workflows/ci.yml`) runs fmt, clippy, tests and `cargo audit` on Windows and Ubuntu for every push and PR. Run the first three locally before pushing.

## Configuration
Copy `config.example.toml` to `config.toml` (or `config.live.toml`, which is gitignored). Secrets are **never** put in files: set `DELTA_API_KEY` and `DELTA_API_SECRET` as environment variables (a `.env` file is loaded if present and is gitignored). Live mode refuses to start without them.

## Where to add things
| Task | Touch |
|------|-------|
| New strategy signal | a Rhai file in `strategies/` ([STRATEGIES.md](STRATEGIES.md)); no Rust needed |
| New indicator | `bar_strategies.rs` plus a unit test |
| Change fees or sizing | `costs.rs` only; everything else reads it |
| New config key | `config.rs` struct and validation, `config.example.toml`, root README table |
| New exchange endpoint | `orders.rs`; keep requests signed and idempotent |

## Testing conventions
- Unit tests live next to the code in `#[cfg(test)] mod tests`.
- Money-path logic (fees, sizing, fills, order retry, circuit breaker) must have a test that fails if the arithmetic changes.
- Tests must not need the real exchange.
- Run one test: `cargo test costs::tests::sizing_btc_eth -- --nocapture`.
- Rhai scripts are tested from Rust: build a `ScriptContext`, call `execute_on_tick`, assert `should_buy` / `should_sell` (see `scripting.rs`).

## Safety rules for changes
1. Paper mode stays the default. Do not commit `paper_trade_mode = false`.
2. Never log keys, secrets or signed headers.
3. A change that affects live orders needs a paper run (`scripts/run-live.ps1`) before it is deployed; follow the [runbook](RUNBOOK.md) checklist.

## Pull requests
Small, single-purpose commits with a message that says why. Include test output for anything touching `orders.rs`, `strategy_engine.rs` or `costs.rs`.
