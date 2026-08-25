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

def simulate_hold_through_noise(rates_bps_8h, min_hold_days, round_trip_cost=35.06):
    """
    Simulates a hold-through-noise carry strategy with a minimum commitment window.
    min_hold_days: 3, 7, or 14 days (9, 21, 42 8-hour periods).
    Round trip cost: 35.06 bps (paid once per entry/exit cycle).
    """
    min_hold_periods = min_hold_days * 3
    n = len(rates_bps_8h)
    
    cycles = []
    in_position = False
    pos_direction = 0  # 1 = Long Spot/Short Perp (collecting positive funding), -1 = Short Spot/Long Perp
    entry_period = 0
    cycle_gross_bps = 0.0
    
    # Trailing 3-day (9 period) window signal to prevent churn
    trailing_window = 9
    
    for i in range(n):
        # Current trailing average rate
        start_idx = max(0, i - trailing_window + 1)
        trail_avg = np.mean(rates_bps_8h[start_idx:i+1])
        
        if not in_position:
            # Enter if trailing average has a clear directional skew (|trail_avg| > 0.2 bps)
            if abs(trail_avg) > 0.2:
                in_position = True
                pos_direction = 1 if trail_avg > 0 else -1
                entry_period = i
                cycle_gross_bps = rates_bps_8h[i] * pos_direction
        else:
            # Inside active position
            period_gain = rates_bps_8h[i] * pos_direction
            cycle_gross_bps += period_gain
            held_periods = i - entry_period + 1
            
            # Check exit condition: must meet minimum commitment window AND trailing funding flipped
            funding_flipped = (trail_avg * pos_direction < 0)
            is_last_period = (i == n - 1)
            
            if (held_periods >= min_hold_periods and funding_flipped) or is_last_period:
                # Close cycle
                net_cycle_bps = cycle_gross_bps - round_trip_cost
                cycles.append({
                    'entry_period': entry_period,
                    'exit_period': i,
                    'duration_periods': held_periods,
                    'duration_days': held_periods / 3.0,
                    'gross_bps': cycle_gross_bps,
                    'net_bps': net_cycle_bps,
                    'direction': pos_direction
                })
                in_position = False
                cycle_gross_bps = 0.0

    if not cycles:
        return {
            'cycle_count': 0, 'avg_duration_days': 0.0, 'total_net_bps': 0.0,
            'mean_net_bps_per_cycle': 0.0, 'ann_net_pct': 0.0, 't_stat': 0.0, 'p_val': 1.0,
            'ci_low': 0.0, 'ci_high': 0.0
        }

    net_bps_list = np.array([c['net_bps'] for c in cycles])
    durations = np.array([c['duration_days'] for c in cycles])
    
    total_net_bps = np.sum(net_bps_list)
    avg_dur_days = np.mean(durations)
    mean_net_per_cycle = np.mean(net_bps_list)
    
    # Annualized Net Return (%) over total 500 periods (~167 days)
    total_days = n / 3.0
    ann_net_pct = (total_net_bps / 10000.0) * (365.0 / total_days) * 100.0
    
    t_stat, p_val = stats.ttest_1samp(net_bps_list, 0.0) if len(net_bps_list) > 1 else (0.0, 1.0)
    ci_low, ci_high = bootstrap_ci(net_bps_list)

    return {
        'cycle_count': len(cycles),
        'avg_duration_days': avg_dur_days,
        'total_net_bps': total_net_bps,
        'mean_net_bps_per_cycle': mean_net_per_cycle,
        'ann_net_pct': ann_net_pct,
        't_stat': float(t_stat),
        'p_val': float(p_val),
        'ci_low': ci_low,
        'ci_high': ci_high
    }

def analyze_tail_risk(rates_bps_8h):
    """
    Splits sample into Typical Periods (Middle 80%) and Blowout/Tail Periods (Bottom 10% & Top 10%).
    """
    p10, p90 = np.percentile(rates_bps_8h, [10, 90])
    
    typical_mask = (rates_bps_8h >= p10) & (rates_bps_8h <= p90)
    blowout_mask = ~typical_mask
    
    typical_rates = rates_bps_8h[typical_mask]
    blowout_rates = rates_bps_8h[blowout_mask]
    
    # Raw gross yields
    raw_typical = np.abs(typical_rates)
    raw_blowout = np.abs(blowout_rates)
    
    mean_ann_typical = np.mean(typical_rates) * 3 * 365
    median_ann_typical = np.median(typical_rates) * 3 * 365
    
    mean_ann_blowout = np.mean(blowout_rates) * 3 * 365
    
    return {
        'p10_bps': p10,
        'p90_bps': p90,
        'typical_count': len(typical_rates),
        'typical_mean_ann_bps': mean_ann_typical,
        'typical_med_ann_bps': median_ann_typical,
        'typical_raw_8h_bps': np.mean(raw_typical),
        'blowout_count': len(blowout_rates),
        'blowout_mean_ann_bps': mean_ann_blowout,
        'blowout_raw_8h_bps': np.mean(raw_blowout),
    }

def run_v2_diagnostic():
    symbols = [
        ("1000PEPEUSD", "1000PEPEUSDT"),
        ("WIFUSD", "WIFUSDT"),
        ("DOGEUSD", "DOGEUSDT"),
        ("XRPUSD", "XRPUSDT"),
        ("BTCUSD", "BTCUSDT"),
        ("ETHUSD", "ETHUSDT"),
    ]

    print("=" * 120)
    print("V2 FUNDING CARRY DIAGNOSTIC: HOLD-THROUGH-NOISE STRATEGY & TAIL-RISK SEPARATION")
    print("Round-Trip Entry/Exit Cost Model: 35.06 bps (Delta Exchange Schedule + 18% GST + Slippage)")
    print("=" * 120)

    hold_windows = [3, 7, 14]  # Days
    all_results = {}

    for delta_sym, binance_sym in symbols:
        raw_data = fetch_binance_funding_history(binance_sym, limit=1000)
        if not raw_data:
            continue

        rates = np.array([float(d['fundingRate']) for d in raw_data])
        rates_bps_8h = rates * 10000.0
        ann_rates_bps = rates_bps_8h * 3 * 365.0

        # Part 1: Tail Risk & Typical vs Blowout Regime
        tail_info = analyze_tail_risk(rates_bps_8h)

        # Part 2: Hold-Through-Noise Commitment Windows
        hold_results = {}
        for w_days in hold_windows:
            res = simulate_hold_through_noise(rates_bps_8h, min_hold_days=w_days)
            hold_results[w_days] = res

        all_results[delta_sym] = {
            'mean_ann_bps': np.mean(ann_rates_bps),
            'med_ann_bps': np.median(ann_rates_bps),
            'tail_info': tail_info,
            'hold_results': hold_results
        }

    # Display Part 1: Typical vs Blowout Regime Breakdown
    print("\n" + "=" * 120)
    print("1. TYPICAL VS. BLOWOUT PERIOD ECONOMICS (Middle 80% vs. Tail 20%)")
    print("=" * 120)
    hdr1 = f"{'Symbol':>12} | {'Mean Ann (Full)':>15} | {'Med Ann (Full)':>15} | {'Typical Ann Mean':>16} | {'Typical 8h Yield':>16} | {'Blowout Ann Mean':>17}"
    print(hdr1)
    print("-" * len(hdr1))

    for sym, d in all_results.items():
        t = d['tail_info']
        print(f"{sym:>12} | {d['mean_ann_bps']:+14.2f} bps | {d['med_ann_bps']:+14.2f} bps | {t['typical_mean_ann_bps']:+15.2f} bps | {t['typical_raw_8h_bps']:15.4f} bps | {t['blowout_mean_ann_bps']:+16.2f} bps")

    # Display Part 2: Hold-Through-Noise Commitment Windows
    print("\n" + "=" * 120)
    print("2. HOLD-THROUGH-NOISE STRATEGY RESULTS (3-Day, 7-Day, and 14-Day Commitment Windows)")
    print("=" * 120)

    go_counts = {3: 0, 7: 0, 14: 0}

    for w_days in hold_windows:
        print(f"\n--- Commitment Window: {w_days} Days (Min Hold = {w_days*3} 8h Periods) ---")
        hdr2 = f"{'Symbol':>12} | {'Cycles':>7} | {'Avg Duration':>12} | {'Net EV / Cycle':>16} | {'Ann Net Return (%)':>20} | {'95% CI (bps)':>18} | {'p-val':>7}"
        print("-" * len(hdr2))
        print(hdr2)
        print("-" * len(hdr2))

        for sym, d in all_results.items():
            h_res = d['hold_results'][w_days]
            ci_str = f"[{h_res['ci_low']:+.1f}, {h_res['ci_high']:+.1f}]"
            print(f"{sym:>12} | {h_res['cycle_count']:7d} | {h_res['avg_duration_days']:11.1f}d | {h_res['mean_net_bps_per_cycle']:+15.2f} bps | {h_res['ann_net_pct']:+19.2f}% | {ci_str:>18} | {h_res['p_val']:7.4f}")

            if h_res['ann_net_pct'] > 0 and h_res['p_val'] < 0.05:
                go_counts[w_days] += 1

    print("\n" + "=" * 120)
    print("SUMMARY GO / NO-GO VERDICT BY COMMITMENT WINDOW:")
    print("=" * 120)
    for w_days in hold_windows:
        cnt = go_counts[w_days]
        status = "PASSED (GO)" if cnt > 3 else "FAILED (NO-GO)"
        print(f"  * {w_days:2d}-Day Minimum Hold Window : {cnt}/6 symbols passed positive net EV with p < 0.05 -> {status}")
    print("=" * 120)

if __name__ == "__main__":
    run_v2_diagnostic()
