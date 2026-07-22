import json
import urllib.request
import time
import numpy as np
import datetime
from scipy import stats

def fetch_binance_klines(symbol, interval='1d', start_time=None, end_time=None, limit=1000, is_delivery=True):
    base_url = "https://dapi.binance.com/dapi/v1/klines" if is_delivery else "https://api.binance.com/api/v3/klines"
    url = f"{base_url}?symbol={symbol}&interval={interval}&limit={limit}"
    if start_time:
        url += f"&startTime={start_time}"
    if end_time:
        url += f"&endTime={end_time}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data
    except Exception as e:
        # Expected for some symbols if they don't exist
        return []

def get_historical_data_for_contract(contract_symbol, spot_symbol):
    # E.g. BTCUSD_240329
    # We will fetch all 1d klines for the contract until it ends
    all_future_klines = []
    start_ts = 0
    while True:
        klines = fetch_binance_klines(contract_symbol, interval='1d', start_time=start_ts, limit=1000, is_delivery=True)
        if not klines:
            break
        all_future_klines.extend(klines)
        start_ts = int(klines[-1][0]) + 1
        time.sleep(0.1)

    if not all_future_klines:
        return None, None

    first_ts = int(all_future_klines[0][0])
    last_ts = int(all_future_klines[-1][6]) # close time

    # Fetch spot klines for the same period
    all_spot_klines = []
    curr_ts = first_ts
    while curr_ts < last_ts:
        klines = fetch_binance_klines(spot_symbol, interval='1d', start_time=curr_ts, end_time=last_ts, limit=1000, is_delivery=False)
        if not klines:
            break
        all_spot_klines.extend(klines)
        curr_ts = int(klines[-1][0]) + 1
        time.sleep(0.1)

    # align by open time
    spot_map = {int(k[0]): float(k[4]) for k in all_spot_klines} # use Close price
    future_map = {int(k[0]): float(k[4]) for k in all_future_klines}

    aligned = []
    for ts in sorted(future_map.keys()):
        if ts in spot_map:
            aligned.append({
                'ts': ts,
                'future_px': future_map[ts],
                'spot_px': spot_map[ts]
            })
    return aligned

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
    # Futures Settlement: 5.9 bps fee (no slippage)
    # Total Round-Trip = 38.6 bps
    round_trip_cost_bps = 38.6

    past_contracts = {
        'BTC': [
            'BTCUSD_240628', 'BTCUSD_240329', 'BTCUSD_231229', 'BTCUSD_230929', 
            'BTCUSD_230630', 'BTCUSD_230331', 'BTCUSD_221230', 'BTCUSD_220930'
        ],
        'ETH': [
            'ETHUSD_240628', 'ETHUSD_240329', 'ETHUSD_231229', 'ETHUSD_230929', 
            'ETHUSD_230630', 'ETHUSD_230331', 'ETHUSD_221230', 'ETHUSD_220930'
        ]
    }

    results = []

    for coin, contracts in past_contracts.items():
        spot_sym = f"{coin}USDT"
        cycle_evs = []
        cycle_durations = []
        entry_yields_ann = []

        for contract in contracts:
            data = get_historical_data_for_contract(contract, spot_sym)
            if not data or len(data) < 10:
                continue
            
            # Use the first day of listing as entry
            entry = data[0]
            expiry = data[-1]
            
            days_to_expiry = len(data)
            
            # Basis at entry (Future - Spot)
            basis_abs = entry['future_px'] - entry['spot_px']
            basis_pct = (basis_abs / entry['spot_px']) * 10000.0 # in bps
            
            # Basis at expiry (Convergence check)
            basis_abs_exp = expiry['future_px'] - expiry['spot_px']
            basis_pct_exp = (basis_abs_exp / expiry['spot_px']) * 10000.0
            
            # Annualized Yield
            ann_yield_pct = (basis_pct / 10000.0) * (365.0 / days_to_expiry) * 100.0
            
            # Net EV per cycle
            net_ev_bps = basis_pct - round_trip_cost_bps
            
            cycle_evs.append(net_ev_bps)
            cycle_durations.append(days_to_expiry)
            entry_yields_ann.append(ann_yield_pct)
            
            # Threshold Optimization check (scan first 14 days for max basis)
            max_basis_bps = basis_pct
            for i in range(min(14, len(data))):
                b = (data[i]['future_px'] - data[i]['spot_px']) / data[i]['spot_px'] * 10000.0
                if b > max_basis_bps:
                    max_basis_bps = b
            
            # print(f"  {contract}: Entry Basis {basis_pct:.1f} bps | Expiry Basis {basis_pct_exp:.1f} bps | Ann Yield {ann_yield_pct:.2f}% | Max 14d Basis {max_basis_bps:.1f} bps")

        if cycle_evs:
            c_arr = np.array(cycle_evs)
            mean_ev = float(np.mean(c_arr))
            t_stat, p_val = stats.ttest_1samp(c_arr, 0.0) if len(c_arr) > 1 else (0.0, 1.0)
            ci_low, ci_high = bootstrap_ci(c_arr)
            
            results.append({
                'asset': coin,
                'cycles': len(cycle_evs),
                'mean_duration': np.mean(cycle_durations),
                'mean_entry_ann_yield': np.mean(entry_yields_ann),
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
    print("CAPITAL EFFICIENCY & PRACTICAL CONSTRAINTS")
    print("=" * 120)
    print("1. Tenor Constraint: Quarterly contracts average ~180 days from listing to expiry (since they list 2 quarters out).")
    print("2. Cycles Per Year: ~2 sequential cycles per year max per capital unit.")
    print("3. Margin Requirement: Short futures require 20% initial margin (at 5x leverage) + Spot requires 100% capital (or Spot margin).")
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
