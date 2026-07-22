import os
import glob
import numpy as np
from collections import deque
from scipy import stats

def analyze_scalper_offer_single(filepath):
    data = np.load(filepath)['data']
    px = data['px'].astype(np.float64)
    qty = data['qty'].astype(np.float64)
    ev = data['ev'].astype(np.int64)
    side = np.where((ev & 536870912) != 0, 1.0, -1.0)
    ts = data['exch_ts'].astype(np.float64) if 'exch_ts' in data.dtype.names else data['local_ts'].astype(np.float64)
    
    n = len(px)
    lookback = 24
    min_cvd_notional = 10000.0
    cooldown = 2000
    
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

    fname = os.path.basename(filepath)
    is_major = ('BTCUSD' in fname or 'ETHUSD' in fname)
    qualifying_window_sec = 1800.0 if is_major else 900.0
    
    full_taker_cost = 21.80
    scalper_cost = 15.90
    
    print(f"\n==========================================================================================================")
    print(f" ASSET: {fname:<25} | Window: {qualifying_window_sec/60:.0f} Min | Signals: {len(triggers)}")
    print(f"==========================================================================================================")
    header = f"{'Horizon':>8} | {'Count':>6} | {'Med Dur (s)':>11} | {'Qual. (%)':>9} | {'Raw WR':>8} | {'Orig WR (21.8b)':>15} | {'Scalper WR':>12} | {'Scalper EV (b)':>14} | {'p-val':>7}"
    print("-" * len(header))
    print(header)
    print("-" * len(header))

    for h in horizons:
        raw_returns = []
        scalper_net_returns = []
        durations_sec = []
        qualifying_flags = []
        
        for tr in triggers:
            idx = tr['idx']
            direction = tr['dir']
            if idx + h < n:
                raw_ret = (px[idx + h] - px[idx]) / px[idx] * 10000.0 if direction == 1 else (px[idx] - px[idx + h]) / px[idx] * 10000.0
                dur_sec = (ts[idx + h] - ts[idx]) / 1000.0
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
            qual_pct = float(np.mean(qual_arr) * 100.0)
            raw_wr = float(np.mean(raw_arr > 0) * 100.0)
            orig_adj_wr = float(np.mean(raw_arr > full_taker_cost) * 100.0)
            scalper_adj_wr = float(np.mean(raw_arr > np.where(qual_arr, scalper_cost, full_taker_cost)) * 100.0)
            scalper_mean_net_bps = float(np.mean(net_arr))
            t_stat, p_val = stats.ttest_1samp(net_arr, 0.0) if len(net_arr) > 1 else (0.0, 1.0)
            
            print(f"{h:8d} | {len(raw_arr):6d} | {med_dur_sec:11.1f} | {qual_pct:8.1f}% | {raw_wr:7.2f}% | {orig_adj_wr:14.2f}% | {scalper_adj_wr:11.2f}% | {scalper_mean_net_bps:+13.4f} | {p_val:7.4f}")

if __name__ == "__main__":
    for f in ["backtest/processed/BTCUSD.npz", "backtest/processed/ETHUSD.npz"]:
        if os.path.exists(f):
            analyze_scalper_offer_single(f)
