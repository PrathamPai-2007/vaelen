# ARCHIVED HYPOTHESIS: Iceberg-Absorption Fade (v1)

**STATUS**: KILLED / REJECTED (2026-07-22)

## Executive Summary
This directory contains the archived implementation (`strategy.py`), production configuration (`config.toml`), and full reconciled diagnostic audit (`reconciled_diagnostic_report.md`) for the **Institutional Iceberg-Absorption Fade Strategy (v1)**.

The strategy hypothesis has been **formally killed** and marked as **NO-GO**. It must NOT be deployed to live/paper trading or re-attempted without fundamental architectural changes.

---

## Why Was This Hypothesis Killed?

1. **Systematic Adverse Selection (Negative Forward Returns)**
   - Decoupled signal testing (evaluating raw price forward returns over 10 to 1000 ticks with zero exit/position management) proved that fading aggressive volume spikes when price impact is low has **negative expected value**.
   - Win rates across short-to-medium horizons are strictly below 40% (typically 6% - 36%), and mean forward returns in the direction of the trade are **statistically significantly negative** ($p < 0.001$).
   - High aggressive volume slamming into a price level is a signal of **flow momentum / order book sweeping**, NOT mean-reverting absorption. Fading it causes systematic adverse selection.

2. **Cross-Symbol & Multi-Fold Failure**
   - In 5-fold rolling walk-forward optimization across all available crypto perpetual symbols (`1000PEPEUSD`, `DOGEUSD`, `BTCUSD`, `ETHUSD`, `WIFUSD`), **zero symbols** achieved an Out-of-Sample Profit Factor > 1.0 or a positive bootstrap 5th-percentile LCB PF.
   - On high-liquidity assets (`BTCUSD`, `ETHUSD`), the optimizer converged to 0 trades because no parameter set yielded positive expected value.

3. **Execution Unprofitability Even Under 100% Instant Fills**
   - Under perfect execution (100% maker fill probability, zero entry slippage), the strategy still loses money (PF = 0.6502). The failure is caused by negative entry signal edge, not execution friction.

---

## Retained Artifacts
- `strategy.py`: Archived CVD momentum iceberg fade implementation.
- `config.toml`: Parameter configuration snapshot prior to archiving.
- `reconciled_diagnostic_report.md`: Full empirical metrics, volatility breakdown, and sensitivity tables.
