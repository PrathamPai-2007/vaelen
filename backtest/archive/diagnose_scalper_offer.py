import os
import glob
import numpy as np
from collections import deque
from scipy import stats

def analyze_scalper_offer(filepath, lookback=24, min_cvd_notional=10000.0, cooldown=2000):
    data = np.load(filepath)['data']
    px = data['px'].astype(np.float64)
    qty = data['qty'].astype(np.float64)
    ev = data['ev'].astype(np.int64)
    side = np.where((ev & 536870912) != 0, 1.0, -1.0)
    
    ts = data['exch_ts'] if 'exch_ts' in data.dtype.names else data['local_ts']
    ts = ts.astype(np.float64)
    
    n = len(px)
    if n < 1000:
        return None
        
    cvd = np.cumsum(qty * side)
    kernel = np.ones(lookback + 1, dtype=np.float64)
    cum_vol_all = np.convolve(qty, kernel, mode='full')[:n]
    
    horizons = [10, 25, 50, 100, 200, 500, 1000]
    
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

    # Fee Schedules & Window
    fname = os.path.basename(filepath)
    is_major = ('BTCUSD' in fname or 'ETHUSD' in fname)
    qualifying_window_sec = 1800.0 if is_major else 900.0 # 30 min for BTC/ETH, 15 min for others
    
    # Costs
    full_taker_cost = 21.80 # 5.90 fee + 5 slip + 5.90 fee + 5 slip
    scalper_cost = 15.90    # 5.90 fee + 5 slip + 0.00 fee + 5 slip
    
    horiz_results = {}

    for h in horizons:
        raw_returns = []
        scalper_net_returns = []
        durations_sec = []
        qualifying_flags = []
        
        for tr in triggers:
            idx = tr['idx']
            direction = tr['dir']
            if idx + h < n:
                if direction == 1:
                    raw_ret = (px[idx + h] - px[idx]) / px[idx] * 10000.0
                else:
                    raw_ret = (px[idx] - px[idx + h]) / px[idx] * 10000.0
                    
                dur_sec = (ts[idx + h] - ts[idx]) / 1000.0 # ts in ms
                qualifies = (dur_sec <= qualifying_window_sec)
                
                cost = scalper_cost if qualifies else full_taker_cost
                net_ret = raw_ret - cost
                
                raw_returns.append(raw_ret)
                scalper_net_returns.append(net_ret)
                durations_sec.append(dur_sec)
                qualifying_flags.append(qualifies)

        if raw_returns:
            raw_arr = np.array(raw_returns)
            net_arr = np.array(scalper_net_returns)
            dur_arr = np.array(durations_sec)
            qual_arr = np.array(qualifying_flags)
            
            med_dur_sec = float(np.median(dur_arr))
            mean_dur_sec = float(np.mean(dur_arr))
            qual_pct = float(np.mean(qual_arr) * 100.0)
            
            raw_wr = float(np.mean(raw_arr > 0) * 100.0)
            orig_adj_wr_21 = float(np.mean(raw_arr > full_taker_cost) * 100.0)
            scalper_adj_wr = float(np.mean(raw_arr > np.where(qual_arr, scalper_cost, full_taker_cost)) * 100.0)
            scalper_mean_net_bps = float(np.mean(net_arr))
            
            t_stat, p_val = stats.ttest_1samp(net_arr, 0.0) if len(net_arr) > 1 else (0.0, 1.0)
            
            horiz_results[h] = {
                'count': len(raw_arr),
                'med_dur_sec': med_dur_sec,
                'mean_dur_sec': mean_dur_sec,
                'qual_pct': qual_pct,
                'raw_wr': raw_wr,
                'orig_adj_wr_21': orig_adj_wr_21,
                'scalper_adj_wr': scalper_adj_wr,
                'scalper_mean_net_bps': scalper_mean_net_bps,
                't_stat': float(t_stat),
                'p_val': float(p_val)
            }

    return {
        'total_count': len(triggers),
        'window_sec': qualifying_window_sec,
        'horizons': horiz_results
    }

def run_diagnostics():
    print("=" * 120)
    print("V2 MOMENTUM BREAKOUT: DELTA 'SCALPER OFFER' COST MODEL RE-RUN")
    print("BTCUSD/ETHUSD Window: 30 Min | Other Futures Window: 15 Min")
    print("Qualifying Cost: 15.90 bps (5.90 entry fee + 5 slip + 0 exit fee + 5 slip)")
    print("Exceeding Cost:  21.80 bps (5.90 entry fee + 5 slip + 5.90 exit fee + 5 slip)")
    print("=" * 120)

    files = sorted(glob.glob("backtest/processed/*.npz"))
    target_files = []
    for filepath in files:
        fname = os.path.basename(filepath)
        if 'USDT' in fname: continue
        if any(ex in fname for ex in ['_jul10.npz', '_jul11.npz', '_jul12.npz', '_jul13.npz', '_jul14.npz', '_jul15.npz', '_jul16.npz', '_jul17.npz', '_jul18.npz']) and not 'WIFUSD' in fname:
            if not fname.startswith('DOGEUSD_jul10'):
                continue
        target_files.append(filepath)

    summary_verdicts = []

    for filepath in target_files:
        fname = os.path.basename(filepath)
        res = analyze_scalper_offer(filepath)
        if not res: continue

        window_min = res['window_sec'] / 60.0
        print(f"\n==========================================================================================================")
        print(f" ASSET: {fname:<25} | Scalper Offer Window: {window_min:.0f} Min | Total Signals: {res['total_count']}")
        print(f"==========================================================================================================")
        
        header = f"{'Horizon':>8} | {'Count':>6} | {'Med Dur (s)':>11} | {'Qual. (%)':>9} | {'Raw WR':>8} | {'Orig WR (21.8b)':>15} | {'Scalper WR':>12} | {'Scalper EV (b)':>14} | {'p-val':>7}"
        print("-" * len(header))
        print(header)
        print("-" * len(header))

        for h, v in res['horizons'].items():
            print(f"{h:8d} | {v['count']:6d} | {v['med_dur_sec']:11.1f} | {v['qual_pct']:8.1f}% | {v['raw_wr']:7.2f}% | {v['orig_adj_wr_21']:14.2f}% | {v['scalper_adj_wr']:11.2f}% | {v['scalper_mean_net_bps']:+13.4f} | {v['p_val']:7.4f}")
            
            if v['scalper_adj_wr'] > 52.0 and v['p_val'] < 0.05 and v['scalper_mean_net_bps'] > 0:
                summary_verdicts.append((fname, h, v['scalper_adj_wr'], v['scalper_mean_net_bps'], v['p_val'], v['count']))

    print("\n" + "=" * 120)
    print("PASSED HORIZONS SUMMARY (Scalper Cost-Adj WR > 52% & p < 0.05 & Net EV > 0):")
    print("=" * 120)
    if summary_verdicts:
        for item in summary_verdicts:
            print(f"Asset: {item[0]} | Horizon: {item[1]}t | Scalper WR: {item[2]:.2f}% | Net EV: {item[3]:+.4f} bps | p-val: {item[4]:.4f} | Count: {item[5]}")
    else:
        print("NONE. Zero horizons across all symbols met the >52% cost-adjusted win rate with p < 0.05 under Delta's Scalper Offer cost model.")
        
    print("=" * 120)

if __name__ == "__main__":
    run_diagnostics()
