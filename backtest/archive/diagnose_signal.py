import os
import glob
import numpy as np
from collections import deque
from scipy import stats

def analyze_v2_signal_variants(filepath, lookback=24, min_cvd_notional=10000.0, cooldown=2000):
    data = np.load(filepath)['data']
    px = data['px'].astype(np.float64)
    qty = data['qty'].astype(np.float64)
    ev = data['ev'].astype(np.int64)
    side = np.where((ev & 536870912) != 0, 1.0, -1.0)
    
    n = len(px)
    if n < 1000:
        return None
        
    cvd = np.cumsum(qty * side)
    
    kernel = np.ones(lookback + 1, dtype=np.float64)
    cum_vol_all = np.convolve(qty, kernel, mode='full')[:n]
    
    horizons = [10, 25, 50, 100, 200, 500, 1000]
    
    # Store trigger dicts: {'idx': i, 'direction': 1 or -1, 'magnitude_bps': float}
    triggers = []
    
    vol_buf = deque(maxlen=1000)
    cached_p95 = float('inf')
    last_signal_idx = -cooldown
    
    for i in range(n):
        v = qty[i]
        vol_buf.append(v)
        if len(vol_buf) >= 10 and (i % 500 == 0 or cached_p95 == float('inf')):
            cached_p95 = float(np.percentile(vol_buf, 95))
            
        if i < lookback + 1:
            continue
            
        past_idx = i - lookback
        delta_p = px[i] - px[past_idx]
        cum_vol = cum_vol_all[i]
        volume_spike = v > cached_p95
        is_valid_time = (i - last_signal_idx) >= cooldown
        
        if volume_spike and cum_vol > min_cvd_notional and is_valid_time:
            cvd_diff = cvd[i] - cvd[past_idx]
            mag_bps = (abs(delta_p) / px[past_idx]) * 10000.0
            
            if cvd_diff > 0 and delta_p > 0.0:
                triggers.append({'idx': i, 'dir': 1, 'mag_bps': mag_bps})
                last_signal_idx = i
            elif cvd_diff < 0 and delta_p < 0.0:
                triggers.append({'idx': i, 'dir': -1, 'mag_bps': mag_bps})
                last_signal_idx = i

    if not triggers:
        return None

    # Costs
    taker_fee_bps = 5.0
    slippage_bps = 5.0
    maker_fee_bps = 2.0
    
    full_taker_cost = (taker_fee_bps + slippage_bps) * 2  # 20 bps
    asym_cost = (taker_fee_bps + slippage_bps) + maker_fee_bps  # 12 bps

    # Compute percentiles for magnitude filtering
    mags = np.array([tr['mag_bps'] for tr in triggers])
    p75_val = np.percentile(mags, 75) if len(mags) >= 4 else 0.0
    p90_val = np.percentile(mags, 90) if len(mags) >= 10 else 0.0

    filter_levels = {
        'All Triggers': lambda tr: True,
        'Top Quartile (P75+)': lambda tr: tr['mag_bps'] >= p75_val,
        'Top Decile (P90+)': lambda tr: tr['mag_bps'] >= p90_val,
    }

    results_by_filter = {}

    for filter_name, cond_fn in filter_levels.items():
        filtered_triggers = [tr for tr in triggers if cond_fn(tr)]
        horiz_results = {}

        for h in horizons:
            returns = []
            for tr in filtered_triggers:
                idx = tr['idx']
                direction = tr['dir']
                if idx + h < n:
                    if direction == 1:
                        ret = (px[idx + h] - px[idx]) / px[idx] * 10000.0
                    else:
                        ret = (px[idx] - px[idx + h]) / px[idx] * 10000.0
                    returns.append(ret)

            if returns:
                arr = np.array(returns)
                mean_ret = float(np.mean(arr))
                med_ret = float(np.median(arr))
                raw_wr = float(np.mean(arr > 0) * 100.0)
                adj_wr_20 = float(np.mean(arr > full_taker_cost) * 100.0)
                adj_wr_12 = float(np.mean(arr > asym_cost) * 100.0)
                t_stat, p_val = stats.ttest_1samp(arr, 0.0) if len(arr) > 1 else (0.0, 1.0)
                
                horiz_results[h] = {
                    'count': len(arr),
                    'mean_bps': mean_ret,
                    'median_bps': med_ret,
                    'raw_wr': raw_wr,
                    'adj_wr_20': adj_wr_20,
                    'adj_wr_12': adj_wr_12,
                    't_stat': float(t_stat),
                    'p_val': float(p_val)
                }
        results_by_filter[filter_name] = {
            'total_count': len(filtered_triggers),
            'horizons': horiz_results
        }

    return results_by_filter

def run_diagnostics():
    print("="*120)
    print("V2 MOMENTUM SIGNAL: STRUCTURAL COST-REDUCTION & MAGNITUDE-FILTERING TEST")
    print("Full Taker Round-Trip Cost: 20 bps | Asymmetric Taker-Maker Cost: 12 bps")
    print("="*120)
    
    files = sorted(glob.glob("backtest/processed/*.npz"))
    target_files = []
    for filepath in files:
        fname = os.path.basename(filepath)
        if 'USDT' in fname:
            continue
        if any(ex in fname for ex in ['_jul10.npz', '_jul11.npz', '_jul12.npz', '_jul13.npz', '_jul14.npz', '_jul15.npz', '_jul16.npz', '_jul17.npz', '_jul18.npz']) and not 'WIFUSD' in fname:
            if not fname.startswith('DOGEUSD_jul10'):
                continue
        target_files.append(filepath)

    summary_verdict = []

    for filepath in target_files:
        fname = os.path.basename(filepath)
        res = analyze_v2_signal_variants(filepath)
        if not res:
            continue

        print(f"\n==========================================================================================================")
        print(f" ASSET: {fname}")
        print(f"==========================================================================================================")

        for filter_name, f_data in res.items():
            cnt = f_data['total_count']
            print(f"\n--- Filter Level: {filter_name} (Sample Size: {cnt} signals) ---")
            header = f"{'Horizon':>8} | {'Count':>6} | {'Mean (bps)':>11} | {'Raw WR':>8} | {'Adj WR (20bps)':>14} | {'Adj WR (12bps)':>14} | {'t-stat':>7} | {'p-val':>7}"
            print("-" * len(header))
            print(header)
            print("-" * len(header))

            for h, v in f_data['horizons'].items():
                print(f"{h:8d} | {v['count']:6d} | {v['mean_bps']:11.4f} | {v['raw_wr']:7.2f}% | {v['adj_wr_20']:13.2f}% | {v['adj_wr_12']:13.2f}% | {v['t_stat']:7.2f} | {v['p_val']:7.4f}")
                
                if v['adj_wr_12'] > 52.0 and v['p_val'] < 0.05:
                    summary_verdict.append((fname, filter_name, h, v['adj_wr_12'], v['p_val'], v['count']))

    print("\n" + "="*120)
    print("PASSED HORIZONS SUMMARY (Cost-Adj WR > 52% & p < 0.05 under 12 bps Asymmetric Cost):")
    print("="*120)
    if summary_verdict:
        for item in summary_verdict:
            print(f"Asset: {item[0]} | Filter: {item[1]} | Horizon: {item[2]}t | Adj WR (12bps): {item[3]:.2f}% | p-val: {item[4]:.4f} | Count: {item[5]}")
    else:
        print("NONE. Zero horizons across all symbols and filter levels met the >52% cost-adjusted win rate with p < 0.05.")

if __name__ == "__main__":
    run_diagnostics()
