import json
import urllib.request
import numpy as np
from scipy import stats

def fetch_binance_funding_history(symbol, limit=1000):
    url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit={limit}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data
    except Exception as e:
        print(f"Error fetching funding for {symbol}: {e}")
        return []

def bootstrap_ci(arr, num_samples=2000, alpha=0.05):
    if len(arr) == 0:
        return 0.0, 0.0
    boot_means = []
    np.random.seed(42)
    n = len(arr)
    for _ in range(num_samples):
        sample = np.random.choice(arr, size=n, replace=True)
        boot_means.append(np.mean(sample))
    lower = np.percentile(boot_means, alpha / 2 * 100)
    upper = np.percentile(boot_means, (1 - alpha / 2) * 100)
    return float(lower), float(upper)

def analyze_funding_carry():
    symbols = [
        ("1000PEPEUSD", "1000PEPEUSDT"),
        ("WIFUSD", "WIFUSDT"),
        ("DOGEUSD", "DOGEUSDT"),
        ("XRPUSD", "XRPUSDT"),
        ("BTCUSD", "BTCUSDT"),
        ("ETHUSD", "ETHUSDT"),
    ]

    print("=" * 120)
    print("DELTA-NEUTRAL FUNDING RATE / BASIS CAPTURE (CARRY) DIAGNOSTIC AUDIT")
    print("Methodology: Decoupled 8-Hour Funding Rate History & Cost Model")
    print("=" * 120)

    # Fee & Slippage Parameters (Delta Exchange Schedule with 18% GST)
    # Taker fee: 0.05% * 1.18 = 0.059% (5.9 bps)
    # Maker fee: 0.02% * 1.18 = 0.0236% (2.36 bps)
    # Spot Buy fee: 0 bps | Spot Sell fee: 0.10% * 1.18 = 0.118% (11.8 bps)
    # Slippage per leg: 5.0 bps
    
    perp_entry_cost = 5.9 + 5.0  # Taker entry: 10.9 bps
    spot_buy_cost = 0.0 + 5.0    # Spot buy: 5.0 bps
    entry_total_cost = perp_entry_cost + spot_buy_cost  # 15.9 bps

    perp_exit_cost_maker = 2.36 + 0.0  # Maker exit: 2.36 bps
    perp_exit_cost_taker = 5.9 + 5.0   # Taker exit: 10.9 bps
    spot_sell_cost = 11.8 + 5.0        # Spot sell: 16.8 bps
    
    exit_total_cost_maker = perp_exit_cost_maker + spot_sell_cost  # 19.16 bps
    exit_total_cost_taker = perp_exit_cost_taker + spot_sell_cost  # 27.70 bps

    round_trip_cost_asym = entry_total_cost + exit_total_cost_maker  # 35.06 bps
    round_trip_cost_full = entry_total_cost + exit_total_cost_taker  # 43.60 bps

    summary_results = []

    for delta_sym, binance_sym in symbols:
        raw_data = fetch_binance_funding_history(binance_sym, limit=1000)
        if not raw_data:
            print(f"No funding data for {binance_sym}")
            continue

        rates = np.array([float(d['fundingRate']) for d in raw_data])
        mark_prices = np.array([float(d['markPrice']) for d in raw_data])
        timestamps = np.array([int(d['fundingTime']) for d in raw_data])

        n_periods = len(rates)
        
        # Funding in bps per 8h period (1 rate unit = 100%, 0.0001 = 1 bp = 0.01%)
        funding_bps_8h = rates * 10000.0
        
        # Annualized rate in bps = 8h_bps * 3 * 365
        annualized_rates_bps = funding_bps_8h * 3 * 365.0
        
        mean_ann_bps = np.mean(annualized_rates_bps)
        med_ann_bps = np.median(annualized_rates_bps)

        # Raw carry yield per period if position is always aligned with funding (Long Spot/Short Perp when > 0, Short Spot/Long Perp when < 0)
        raw_carry_bps_8h = np.abs(funding_bps_8h)
        mean_raw_8h_bps = np.mean(raw_carry_bps_8h)

        # Funding Sign Flips
        signs = np.sign(rates)
        signs[signs == 0] = 1
        flips = np.diff(signs) != 0
        flip_count = np.sum(flips)
        flip_pct = (flip_count / (n_periods - 1)) * 100.0 if n_periods > 1 else 0.0

        # Fraction of periods exceeding cost thresholds
        exceed_12bps = np.mean(raw_carry_bps_8h > 12.0) * 100.0
        exceed_20bps = np.mean(raw_carry_bps_8h > 20.0) * 100.0
        exceed_35bps = np.mean(raw_carry_bps_8h > 35.06) * 100.0

        # Dynamic Carry Simulation (including Position Flips and Holding Costs)
        # Entry/Exit cost is paid whenever position is opened, flipped, or closed.
        # If position is held static across N periods, entry cost is paid once at start and exit cost at end.
        
        net_pnl_bps_static_asym = np.sum(raw_carry_bps_8h) - round_trip_cost_asym
        net_pnl_bps_static_full = np.sum(raw_carry_bps_8h) - round_trip_cost_full

        # Dynamic strategy: Only enter if |funding| > threshold, flip when sign changes
        # Rehedging cost on every flip = round_trip_cost_asym (or full)
        total_flip_costs_asym = flip_count * round_trip_cost_asym
        total_flip_costs_full = flip_count * round_trip_cost_full
        
        net_pnl_bps_dynamic_asym = np.sum(raw_carry_bps_8h) - entry_total_cost - total_flip_costs_asym - exit_total_cost_maker
        net_pnl_bps_dynamic_full = np.sum(raw_carry_bps_8h) - entry_total_cost - total_flip_costs_full - exit_total_cost_taker

        mean_net_8h_bps_static_asym = net_pnl_bps_static_asym / n_periods
        mean_net_8h_bps_dynamic_asym = net_pnl_bps_dynamic_asym / n_periods

        # Statistical Significance of 8h Raw vs Net Carry
        # Net 8h series under dynamic rehedging
        net_series_dynamic = raw_carry_bps_8h.copy()
        # Deduct entry on period 0, exit on last period, and flip costs on flip periods
        net_series_dynamic[0] -= entry_total_cost
        net_series_dynamic[-1] -= exit_total_cost_maker
        if flip_count > 0:
            flip_indices = np.where(flips)[0] + 1
            for f_idx in flip_indices:
                if f_idx < n_periods:
                    net_series_dynamic[f_idx] -= round_trip_cost_asym

        mean_net_dynamic = np.mean(net_series_dynamic)
        t_stat, p_val = stats.ttest_1samp(net_series_dynamic, 0.0) if n_periods > 1 else (0.0, 1.0)
        ci_low, ci_high = bootstrap_ci(net_series_dynamic)

        # Volatility Regime Breakdown (8-hour mark price returns)
        mark_returns = np.abs(np.diff(mark_prices) / mark_prices[:-1]) * 10000.0
        if len(mark_returns) > 0:
            p33, p66 = np.percentile(mark_returns, [33.3, 66.6])
            low_vol_mask = np.append([True], mark_returns <= p33)
            high_vol_mask = np.append([False], mark_returns > p66)
            
            mean_funding_low_vol = np.mean(annualized_rates_bps[low_vol_mask]) if np.any(low_vol_mask) else 0.0
            mean_funding_high_vol = np.mean(annualized_rates_bps[high_vol_mask]) if np.any(high_vol_mask) else 0.0
        else:
            mean_funding_low_vol, mean_funding_high_vol = 0.0, 0.0

        summary_results.append({
            'symbol': delta_sym,
            'periods': n_periods,
            'mean_ann_bps': mean_ann_bps,
            'med_ann_bps': med_ann_bps,
            'mean_raw_8h_bps': mean_raw_8h_bps,
            'flips': flip_count,
            'flip_pct': flip_pct,
            'exceed_12bps': exceed_12bps,
            'exceed_35bps': exceed_35bps,
            'mean_net_dynamic_bps': mean_net_dynamic,
            'ci_low': ci_low,
            'ci_high': ci_high,
            't_stat': t_stat,
            'p_val': p_val,
            'low_vol_ann': mean_funding_low_vol,
            'high_vol_ann': mean_funding_high_vol,
        })

        print(f"\nAsset: {delta_sym} ({binance_sym}) | Periods: {n_periods} (8h each, ~{n_periods*8/24:.0f} days)")
        print(f"  Annualized Funding Rate  : Mean = {mean_ann_bps:+.2f} bps ({mean_ann_bps/100:+.2f}%) | Median = {med_ann_bps:+.2f} bps")
        print(f"  Raw 8h Funding Yield     : Mean = {mean_raw_8h_bps:.4f} bps / period")
        print(f"  Funding Sign Flips       : {flip_count} flips ({flip_pct:.2f}% of 8h periods)")
        print(f"  Periods > 12 bps Funding : {exceed_12bps:.2f}% | Periods > 35 bps Funding: {exceed_35bps:.2f}%")
        print(f"  Rehedged Net 8h PnL      : Mean = {mean_net_dynamic:+.4f} bps / period")
        print(f"  95% Bootstrap CI (Net PnL): [{ci_low:+.4f}, {ci_high:+.4f}] bps")
        print(f"  t-statistic / p-value    : t = {t_stat:+.2f}, p = {p_val:.4f}")
        print(f"  Vol Regime Funding (Ann) : Low Vol = {mean_funding_low_vol:+.2f} bps | High Vol = {mean_funding_high_vol:+.2f} bps")

    # Table Summary
    print("\n" + "=" * 120)
    print("SUMMARY DIAGNOSTIC TABLE: DELTA-NEUTRAL FUNDING CARRY STRATEGY")
    print("=" * 120)
    header = f"{'Symbol':>12} | {'Periods':>7} | {'Mean Ann Rate':>13} | {'Raw 8h Yield':>12} | {'Flips (%)':>10} | {'Net 8h PnL':>11} | {'95% CI (bps)':>18} | {'p-val':>7}"
    print(header)
    print("-" * len(header))
    
    go_count = 0
    for r in summary_results:
        ci_str = f"[{r['ci_low']:+.2f}, {r['ci_high']:+.2f}]"
        print(f"{r['symbol']:>12} | {r['periods']:7d} | {r['mean_ann_bps']:+12.2f} bps | {r['mean_raw_8h_bps']:11.4f} bps | {r['flip_pct']:9.2f}% | {r['mean_net_dynamic_bps']:+10.4f} bps | {ci_str:>18} | {r['p_val']:7.4f}")
        if r['mean_net_dynamic_bps'] > 0 and r['p_val'] < 0.05:
            go_count += 1

    print("\n" + "=" * 120)
    print(f"GO / NO-GO VERDICT: {go_count} / {len(summary_results)} symbols passed positive net EV with p < 0.05.")
    if go_count > len(summary_results) / 2:
        print("VERDICT: GO - Strategy demonstrates statistically significant net carry edge.")
    else:
        print("VERDICT: NO-GO - High rehedging/spot fees erode carry yields below breakeven.")
    print("=" * 120)

if __name__ == "__main__":
    analyze_funding_carry()
