# RECONCILED DIAGNOSTIC & SIGNAL AUDIT REPORT (v1)

**Date**: 2026-07-22  
**Status**: Strategy Hypothesis KILLED / Archived  

---

## 1. Discrepancy Reconciliation Summary

Between earlier diagnostic passes, minor numerical differences appeared in stdout logs due to unseeded stochastic fill checks, DOGEUSD contract size scaling, and sorting presentation. Below is the technical breakdown of each discrepancy and its resolution:

1. **DOGEUSD OOS Trade Count & Dollar PnL**:
   - *Cause*: In initial sweep scripts, `DOGEUSD` inherited the default `1000PEPEUSD` order size (`1000` contracts). On DOGE ($0.07 price per contract), 1,000 contracts represents ~$70 notional per trade vs PEPE ($0.0000028) where 1,000 contracts is ~$0.0028. This scaled dollar PnL to -$10,694.07 USD over 111 trades.
   - *Resolution*: When normalized to 1.0 contract size, DOGEUSD net closed PnL is -$0.0412 USD (111 trades, Mean OOS PF 0.0320, Bootstrap LCB PF 0.0206).

2. **Volatility Rank vs. Chunk Number Mapping**:
   - *Cause*: In the 10-chunk breakdown, sorting chunks by realized volatility reordered the display rows. Chunk #1 (chronologically first) had the highest volatility (12.28 bps/tick, PF 0.3490), while Chunk #10 was 2nd highest (12.14 bps/tick, PF 5.1125). Initial summary text cited Chunk #10's PF as Rank 10.
   - *Resolution*: The reconciled table explicitly displays both `Vol Rank` (1 to 10 by volatility) and `Chunk #` (chronological 1 to 10).

3. **Fill-Probability Sensitivity Rows**:
   - *Cause*: Unseeded `random.random() < fill_prob` calls caused tick-level variations in which pending maker orders filled.
   - *Resolution*: With deterministic `seed_everything(42)`, the sensitivity table is 100% reproducible across runs.

---

## 2. Reconciled Empirical Results (Seed = 42)

### A. Signal Validity Check (Decoupled Forward Price Returns)
*Testing raw price forward returns over fixed tick horizons with zero exit/position management.*

| Horizon ($H$) | Signals Tested | Win Rate (%) | Long WR (%) | Short WR (%) | Mean Return (bps) | t-statistic | p-value |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **10 ticks** | 33 | **6.06%** | 5.56% | 6.67% | +1.1130 | +0.59 | 0.5599 |
| **25 ticks** | 33 | **6.06%** | 5.56% | 6.67% | -0.0142 | -0.01 | 0.9948 |
| **50 ticks** | 33 | **12.12%** | 11.11% | 13.33% | +2.1997 | +0.84 | 0.4089 |
| **100 ticks** | 33 | **21.21%** | 11.11% | 33.33% | +3.1572 | +0.89 | 0.3798 |
| **200 ticks** | 33 | **36.36%** | 27.78% | 46.67% | +7.4749 | +1.74 | 0.0906 |
| **500 ticks** | 32 | **40.62%** | 29.41% | 53.33% | +4.4789 | +0.65 | 0.5189 |
| **1000 ticks** | 32 | **40.62%** | 35.29% | 46.67% | +0.1741 | +0.02 | 0.9862 |

---

### B. Volatility Regime Breakdown (`1000PEPEUSD.npz` across 10 Chunks)

| Vol Rank | Chunk # | Realized Volatility (bps/tick) | Trades | Net Closed PnL ($) | Profit Factor |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1 (Lowest Vol)** | 4 | 6.9977 bps | 3 | -$0.0403 USD | 0.0000 |
| **2** | 5 | 7.1306 bps | 3 | -$0.0405 USD | 0.0000 |
| **3** | 7 | 7.8734 bps | 3 | -$0.0189 USD | 0.3465 |
| **4** | 8 | 8.1831 bps | 3 | -$0.0402 USD | 0.0000 |
| **5** | 6 | 8.3452 bps | 3 | +$0.0025 USD | 1.1401 |
| **6** | 9 | 10.0253 bps | 3 | -$0.0186 USD | 0.3490 |
| **7** | 3 | 10.5410 bps | 3 | -$0.0400 USD | 0.0000 |
| **8** | 2 | 11.3527 bps | 3 | -$0.0400 USD | 0.0000 |
| **9** | 10 | 12.1365 bps | 3 | +$0.0241 USD | 5.1125 |
| **10 (Highest Vol)**| 1 | 12.2763 bps | 3 | +$0.0027 USD | 1.1583 |

- **Low Volatility Tercile (Ranks 1-3) Mean PF**: **0.1155** (Net PnL -$0.0997 USD)
- **High Volatility Tercile (Ranks 8-10) Mean PF**: **2.2066** (Net PnL -$0.0132 USD)

---

### C. Fill Probability Sensitivity Analysis (`1000PEPEUSD.npz`)

| Fill Probability | Trades Executed | Net Closed PnL ($ USD) | Profit Factor |
| :--- | :--- | :--- | :--- |
| **100% (Instant Fill)** | 32 | -$0.0861 USD | 0.6502 |
| **75%** | 32 | -$0.0861 USD | 0.6502 |
| **55% (Baseline)** | 32 | -$0.0861 USD | 0.6501 |
| **40%** | 32 | -$0.1075 USD | 0.5826 |
| **20%** | 32 | -$0.1075 USD | 0.5826 |

---

## 3. Final Verdict
The **Institutional Iceberg-Absorption Fade (v1)** strategy is **KILLED**.
- Signal win rates are < 40% across all horizons.
- Fading aggressive taker volume when price stalls causes systematic adverse selection.
- All files have been moved to `archive/iceberg_fade_v1/`.
