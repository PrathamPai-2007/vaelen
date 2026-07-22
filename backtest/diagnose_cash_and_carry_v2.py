import json
import urllib.request
import time
import numpy as np
import datetime
import calendar
from scipy import stats

def fetch_binance_continuous(pair, contract_type='CURRENT_QUARTER', limit=1500):
    url = f"https://dapi.binance.com/dapi/v1/continuousKlines?pair={pair}&contractType={contract_type}&interval=1d&limit={limit}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data
    except Exception as e:
        return []

def fetch_binance_spot(symbol, limit=1500):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1d&limit={limit}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data
    except Exception as e:
        return []

def get_last_fridays(start_year=2021, end_year=2026):
    fridays = []
    for year in range(start_year, end_year + 1):
        for month in [3, 6, 9, 12]:
            cal = calendar.monthcalendar(year, month)
            last_friday = cal[-1][calendar.FRIDAY]
            if last_friday == 0:
                last_friday = cal[-2][calendar.FRIDAY]
            dt = datetime.datetime(year, month, last_friday, 0, 0, 0, tzinfo=datetime.timezone.utc)
            fridays.append(int(dt.timestamp() * 1000))
    return sorted(fridays)

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

def run_diagnostic():
    print("=" * 120)
    print("FIXED-MATURITY FUTURES CASH-AND-CARRY (BASIS CAPTURE) DIAGNOSTIC AUDIT")
    print("Methodology: Historical Binance Coin-M Quarterly Futures (Proxy) & Delta Exchange Fee Schedule")
    print("=" * 120)

    # Cost Model (Delta Schedule)
    # Spot Buy: 0 bps fee + 5.0 bps slip = 5.0 bps
    # Futures Sell: 5.9 bps fee + 5.0 bps slip = 10.9 bps
    # Spot Sell at Expiry: 11.8 bps fee + 5.0 bps slip = 16.8 bps
    # Futures Settlement: 5.9 bps fee (no slippage at expiry)
    # Total Round-Trip = 38.6 bps
    round_trip_cost_bps = 38.6

    symbols = [
        ("BTCUSD", "BTCUSDT"),
        ("ETHUSD", "ETHUSDT"),
        ("ADAUSD", "ADAUSDT"),  # Cardano proxy for altcoins
        ("XRPUSD", "XRPUSDT"),  # XRP
        ("DOGEUSD", "DOGEUSDT") # DOGE
    ]

    last_fridays = get_last_fridays(2021, 2026)
    
    results = []

    for pair, spot_sym in symbols:
        f_data = fetch_binance_continuous(pair, limit=1500)
        s_data = fetch_binance_spot(spot_sym, limit=1500)
        
        if not f_data or not s_data:
            continue
            
        # Map timestamp to daily close price
        f_map = {int(k[0]): float(k[4]) for k in f_data}
        s_map = {int(k[0]): float(k[4]) for k in s_data}
        
        cycle_evs = []
        cycle_durations = []
        entry_yields_ann = []
        max_14d_entry_yields_ann = []
        
        # A cycle starts on a Last Friday and ends on the NEXT Last Friday
        for i in range(len(last_fridays) - 1):
            start_ts = last_fridays[i]
            end_ts = last_fridays[i+1]
            
            # Start date: we enter on the day AFTER the last friday to ensure the new contract is active and liquid
            # Expiry date: we exit on the day BEFORE the next last friday, because on the last friday at 08:00 UTC it settles.
            entry_ts = start_ts + 86400000
            exit_ts = end_ts - 86400000
            
            if entry_ts in f_map and entry_ts in s_map and exit_ts in f_map and exit_ts in s_map:
                f_entry = f_map[entry_ts]
                s_entry = s_map[entry_ts]
                
                f_exit = f_map[exit_ts]
                s_exit = s_map[exit_ts]
                
                basis_entry = (f_entry - s_entry) / s_entry * 10000.0
                basis_exit = (f_exit - s_exit) / s_exit * 10000.0
                
                # Check 14-day threshold optimization
                max_basis = basis_entry
                for d in range(14):
                    scan_ts = entry_ts + d * 86400000
                    if scan_ts in f_map and scan_ts in s_map:
                        b = (f_map[scan_ts] - s_map[scan_ts]) / s_map[scan_ts] * 10000.0
                        if b > max_basis:
                            max_basis = b
                            
                duration_days = (exit_ts - entry_ts) / 86400000.0
                if duration_days <= 0: continue
                
                ann_yield = basis_entry * (365.0 / duration_days) / 100.0
                max_ann_yield = max_basis * (365.0 / duration_days) / 100.0
                
                # Assume held to expiry (basis_exit goes to 0 at settlement, but we measure 1 day prior, so we assume we capture the full entry basis minus whatever tiny basis remains)
                # Actually, settlement is at index price. So captured basis is EXACTLY basis_entry.
                # Net EV = basis_entry - round_trip_cost_bps
                net_ev_bps = basis_entry - round_trip_cost_bps
                
                cycle_evs.append(net_ev_bps)
                cycle_durations.append(duration_days)
                entry_yields_ann.append(ann_yield)
                max_14d_entry_yields_ann.append(max_ann_yield)

        if cycle_evs:
            c_arr = np.array(cycle_evs)
            mean_ev = float(np.mean(c_arr))
            t_stat, p_val = stats.ttest_1samp(c_arr, 0.0) if len(c_arr) > 1 else (0.0, 1.0)
            if mean_ev < 0:
                p_val = 1.0 - (p_val / 2) # One-sided for EV > 0
            else:
                p_val = p_val / 2
                
            ci_low, ci_high = bootstrap_ci(c_arr)
            
            results.append({
                'asset': pair.replace('USD', ''),
                'cycles': len(cycle_evs),
                'mean_duration': np.mean(cycle_durations),
                'mean_entry_basis': np.mean(c_arr + round_trip_cost_bps),
                'mean_entry_ann_yield': np.mean(entry_yields_ann),
                'max_14d_ann_yield': np.mean(max_14d_entry_yields_ann),
                'mean_net_ev': mean_ev,
                'ci_low': ci_low,
                'ci_high': ci_high,
                'p_val': float(p_val)
            })

    print("\n" + "=" * 120)
    print("1. BASIS EDGE & CONVERGENCE VALIDATION (Binance Proxy Data)")
    print("=" * 120)
    hdr1 = f"{'Asset':>8} | {'Cycles':>8} | {'Avg Tenor (d)':>15} | {'Avg Ann Yield':>15} | {'Net EV/Cycle (bps)':>20} | {'95% CI':>16} | {'p-value':>8}"
    print(hdr1)
    print("-" * len(hdr1))

    for r in results:
        ci_str = f"[{r['ci_low']:+.1f}, {r['ci_high']:+.1f}]"
        print(f"{r['asset']:>8} | {r['cycles']:8d} | {r['mean_duration']:15.1f} | {r['mean_entry_ann_yield']:14.2f}% | {r['mean_net_ev']:+16.2f} bps | {ci_str:>16} | {r['p_val']:8.4f}")

    print("\n" + "=" * 120)
    print("2. THRESHOLD OPTIMIZATION CHECK (Max Basis within first 14 days of listing)")
    print("=" * 120)
    hdr2 = f"{'Asset':>8} | {'Immed. Entry Ann Yield':>25} | {'Optimized Entry Ann Yield (14d wait)':>40}"
    print(hdr2)
    print("-" * len(hdr2))
    for r in results:
        print(f"{r['asset']:>8} | {r['mean_entry_ann_yield']:24.2f}% | {r['max_14d_ann_yield']:39.2f}%")

    print("\n" + "=" * 120)
    print("CAPITAL EFFICIENCY & PRACTICAL CONSTRAINTS")
    print("=" * 120)
    print("1. Tenor Constraint: Quarterly contracts average ~90 days from rolling CURRENT_QUARTER listing to expiry.")
    print("2. Cycles Per Year: ~4 sequential cycles per year max per capital unit.")
    print("3. Margin Requirement: Short futures require 20% initial margin (at 5x leverage) + Spot requires 100% capital.")
    print("   Total capital committed per cycle: 120% of position size. Yields must be diluted accordingly.")
    print("4. Delta Settlement: Cash-settled. Expiry convergence requires holding precisely to settlement timestamp.")

    passes = sum(1 for r in results if r['mean_net_ev'] > 0 and r['p_val'] < 0.05)
    print("\n" + "=" * 120)
    print(f"GO / NO-GO VERDICT: {passes} / {len(results)} assets passed positive net EV with p < 0.05.")
    if passes > len(results) / 2:
        print("VERDICT: GO - Statistically significant basis edge exceeds Delta round-trip friction.")
    else:
        print("VERDICT: NO-GO - Capturable basis is either negative, smaller than friction (38.6 bps), or not statistically significant.")
    print("=" * 120)

if __name__ == "__main__":
    run_diagnostic()
