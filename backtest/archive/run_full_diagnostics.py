import os
import sys
import glob
import numpy as np
from collections import deque
from scipy import stats
import toml

sys.path.append(os.path.abspath(os.path.dirname(__file__)))
from strategy import CVDMomentumStrategy
from walk_forward import walk_forward_optimization, calculate_bootstrap_pf_lcb, load_toml_config

# ---------------------------------------------------------------------------
# INVESTIGATION 1: SIGNAL VALIDITY CHECK (Decoupled Raw Forward Returns)
# ---------------------------------------------------------------------------
def run_signal_validity_check(filepath, lookback=24, min_cvd_notional=10000.0, max_impact=1e-6, cooldown=2000):
    data = np.load(filepath)['data']
    px = data['px'].astype(np.float64)
    qty = data['qty'].astype(np.float64)
    ev = data['ev'].astype(np.int64)
    side = np.where((ev & 536870912) != 0, 1.0, -1.0)
    
    n = len(px)
    if n < 1000:
        return None, 0, 0
        
    cvd = np.cumsum(qty * side)
    kernel = np.ones(lookback + 1, dtype=np.float64)
    cum_vol_all = np.convolve(qty, kernel, mode='full')[:n]
    
    horizons = [10, 25, 50, 100, 200, 500, 1000]
    long_signals = []
    short_signals = []
    
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
        impact = abs(delta_p) / cum_vol if cum_vol > 0 else 0.0
        
        volume_spike = v > cached_p95
        can_absorb = (
            volume_spike and
            cum_vol > min_cvd_notional and
            impact < max_impact and
            (i - last_signal_idx) >= cooldown
        )
        
        if can_absorb:
            cvd_diff = cvd[i] - cvd[past_idx]
            if cvd_diff > 0 and delta_p <= 0.0:
                short_signals.append(i)
                last_signal_idx = i
            elif cvd_diff < 0 and delta_p >= 0.0:
                long_signals.append(i)
                last_signal_idx = i
                
    results = {}
    for h in horizons:
        returns = []
        long_rets = []
        short_rets = []
        
        for idx in long_signals:
            if idx + h < n:
                ret_bps = (px[idx + h] - px[idx]) / px[idx] * 10000.0
                returns.append(ret_bps)
                long_rets.append(ret_bps)
        for idx in short_signals:
            if idx + h < n:
                ret_bps = (px[idx] - px[idx + h]) / px[idx] * 10000.0
                returns.append(ret_bps)
                short_rets.append(ret_bps)
                
        if len(returns) > 0:
            returns = np.array(returns)
            mean_ret = np.mean(returns)
            med_ret = np.median(returns)
            std_ret = np.std(returns)
            win_rate = np.mean(returns > 0) * 100.0
            t_stat, p_val = stats.ttest_1samp(returns, 0.0) if len(returns) > 1 else (0.0, 1.0)
            
            long_wr = (np.mean(np.array(long_rets) > 0) * 100.0) if len(long_rets) else 0.0
            short_wr = (np.mean(np.array(short_rets) > 0) * 100.0) if len(short_rets) else 0.0
            
            results[h] = {
                'count': len(returns),
                'mean_bps': mean_ret,
                'median_bps': med_ret,
                'std_bps': std_ret,
                'win_rate': win_rate,
                'long_wr': long_wr,
                'short_wr': short_wr,
                't_stat': t_stat,
                'p_val': p_val
            }
    return results, len(long_signals), len(short_signals)


# ---------------------------------------------------------------------------
# INVESTIGATION 3: REALIZED VOLATILITY REGIME BREAKDOWN
# ---------------------------------------------------------------------------
def run_volatility_regime_analysis(filepath, n_chunks=10):
    data = np.load(filepath)['data']
    px = data['px'].astype(np.float64)
    n = len(px)
    chunk_len = n // n_chunks
    if chunk_len < 500:
        return []

    config = load_toml_config()
    symbol_config = config['strategy']['symbols'][0].copy()
    symbol_config['contract_size'] = 1000.0 if "1000" in filepath else 1.0
    symbol_config['tick_size'] = 0.00000001 if "1000" in filepath else 0.001

    chunk_metrics = []
    for c in range(n_chunks):
        start = c * chunk_len
        end = min((c + 1) * chunk_len, n)
        sub_px = px[start:end]
        sub_data = data[start:end]
        
        log_rets = np.diff(np.log(np.maximum(sub_px, 1e-9)))
        realized_vol = np.std(log_rets) * 10000.0
        
        strategy = CVDMomentumStrategy(None, symbol_config, config, verbose=False)
        for row in sub_data:
            strategy.on_tick(row)
            
        denom = max(strategy.gross_losses + strategy.total_fees, 1e-9)
        pf = strategy.gross_wins / denom if strategy.total_trades > 0 else 0.0
        
        chunk_metrics.append({
            'chunk': c + 1,
            'realized_vol_bps': realized_vol,
            'trades': strategy.total_trades,
            'net_pnl': strategy.closed_pnl,
            'pf': pf,
            'wins': strategy.gross_wins,
            'losses': strategy.gross_losses
        })

    return chunk_metrics


# ---------------------------------------------------------------------------
# INVESTIGATION 4: FILL PROBABILITY SENSITIVITY ANALYSIS
# ---------------------------------------------------------------------------
def run_fill_sensitivity(filepath):
    data = np.load(filepath)['data']
    config = load_toml_config()
    base_sym_config = config['strategy']['symbols'][0].copy()
    base_sym_config['contract_size'] = 1000.0 if "1000" in filepath else 1.0
    base_sym_config['tick_size'] = 0.00000001 if "1000" in filepath else 0.001

    probs = [1.00, 0.75, 0.55, 0.40, 0.20]
    results = []

    for prob in probs:
        cfg = base_sym_config.copy()
        cfg['fill_probability'] = prob
        strategy = CVDMomentumStrategy(None, cfg, config, verbose=False)
        for row in data:
            strategy.on_tick(row)

        denom = max(strategy.gross_losses + strategy.total_fees, 1e-9)
        pf = strategy.gross_wins / denom if strategy.total_trades > 0 else 0.0
        
        results.append({
            'fill_prob': prob,
            'trades': strategy.total_trades,
            'net_pnl': strategy.closed_pnl,
            'pf': pf,
            'gross_wins': strategy.gross_wins,
            'gross_losses': strategy.gross_losses,
            'fees': strategy.total_fees
        })

    return results


def main():
    print("\n" + "="*95)
    print("      DEEP DIAGNOSTIC EVALUATION: SIGNAL VALIDITY & METHODOLOGY AUDIT")
    print("="*95)

    # 1. SIGNAL VALIDITY CHECK
    print("\n" + "-"*95)
    print(" 1. SIGNAL VALIDITY CHECK (Decoupled Raw Forward Returns)")
    print("-" * 95)
    
    files = sorted(glob.glob("backtest/processed/*.npz"))
    target_files = [f for f in files if any(k in os.path.basename(f) for k in ["1000PEPEUSD.npz", "WIFUSD_jul10.npz", "BTCUSD.npz", "ETHUSD.npz"])]
    if not target_files:
        target_files = files[:3]

    for filepath in target_files:
        fname = os.path.basename(filepath)
        res, n_long, n_short = run_signal_validity_check(filepath)
        if not res:
            continue
        print(f"\nAsset: {fname} | Long Signals: {n_long} | Short Signals: {n_short} | Total Triggers: {n_long + n_short}")
        header = f"{'Horizon':>8} | {'Count':>6} | {'Mean (bps)':>11} | {'Med (bps)':>10} | {'Win Rate':>9} | {'Long WR':>8} | {'Short WR':>7} | {'t-stat':>7} | {'p-val':>7}"
        print("-" * len(header))
        print(header)
        print("-" * len(header))
        for h, v in res.items():
            print(f"{h:8d} | {v['count']:6d} | {v['mean_bps']:11.4f} | {v['median_bps']:10.4f} | {v['win_rate']:8.2f}% | {v['long_wr']:7.2f}% | {v['short_wr']:7.2f}% | {v['t_stat']:7.2f} | {v['p_val']:7.4f}")

    # 2. CROSS-SYMBOL ROBUSTNESS SCAN
    print("\n" + "-"*95)
    print(" 2. CROSS-SYMBOL ROBUSTNESS SCAN (5-Fold WFO Pipeline Across Symbols)")
    print("-" * 95)
    
    scan_symbols = [
        ("1000PEPEUSD", ["backtest/processed/1000PEPEUSD.npz"]),
        ("WIFUSD", ["backtest/processed/WIFUSD_jul10.npz", "backtest/processed/WIFUSD_jul16.npz"]),
        ("BTCUSD", ["backtest/processed/BTCUSD.npz"]),
        ("ETHUSD", ["backtest/processed/ETHUSD.npz"]),
        ("DOGEUSD", ["backtest/processed/DOGEUSD.npz"]),
    ]

    sweep_results = []
    for sym_name, file_list in scan_symbols:
        existing_files = [f for f in file_list if os.path.exists(f)]
        if not existing_files:
            continue
        try:
            arr_list = [np.load(f)['data'] for f in existing_files]
            concat_data = np.concatenate(arr_list, axis=0) if len(arr_list) > 1 else arr_list[0]
            print(f"\nRunning 5-Fold WFO for {sym_name} ({len(concat_data):,} ticks)...")
            res = walk_forward_optimization(concat_data, sym_name if sym_name in ["1000PEPEUSD", "WIFUSD"] else "1000PEPEUSD")
            res['symbol'] = sym_name
            sweep_results.append(res)
        except Exception as e:
            print(f"Error evaluating {sym_name}: {e}")

    print(f"\n{'='*90}")
    print(f"CROSS-SYMBOL WFO SUMMARY METRICS")
    print(f"{'='*90}")
    header = f"{'Symbol':<14}{'TotalTicks':>12}{'OOS_Trades':>12}{'OOS_PnL':>12}{'Mean_OOS_PF':>13}{'Std_OOS_PF':>12}{'LCB_OOS_PF':>12}"
    print(header)
    print("-" * len(header))
    for r in sweep_results:
        print(f"{r['symbol']:<14}{r['total_ticks']:>12,}"
              f"{r['total_oos_trades']:>12}"
              f"  ${r['total_oos_net_pnl']:>9.4f}"
              f"{r['mean_oos_pf']:>13.4f}"
              f"{r['std_oos_pf']:>12.4f}"
              f"{r['agg_oos_lcb_pf']:>12.4f}")
    print(f"{'='*90}")

    # 3. REGIME / VOLATILITY BREAKDOWN
    print("\n" + "-"*95)
    print(" 3. REGIME / VOLATILITY BREAKDOWN (10 Timeline Chunks on 1000PEPEUSD)")
    print("-" * 95)
    vol_res = run_volatility_regime_analysis("backtest/processed/1000PEPEUSD.npz", n_chunks=10)
    if vol_res:
        vol_res.sort(key=lambda x: x['realized_vol_bps'])
        header = f"{'Vol Rank':>8} | {'Chunk #':>7} | {'Realized Vol (bps)':>19} | {'Trades':>7} | {'Net PnL ($)':>12} | {'PF':>8}"
        print(header)
        print("-" * len(header))
        for rank, m in enumerate(vol_res, 1):
            print(f"{rank:8d} | {m['chunk']:7d} | {m['realized_vol_bps']:19.4f} | {m['trades']:7d} | ${m['net_pnl']:11.4f} | {m['pf']:8.4f}")
            
        low_vol_pfs = [m['pf'] for m in vol_res[:3]]
        high_vol_pfs = [m['pf'] for m in vol_res[-3:]]
        print(f"\nLow Volatility Tercile Mean PF:  {np.mean(low_vol_pfs):.4f}")
        print(f"High Volatility Tercile Mean PF: {np.mean(high_vol_pfs):.4f}")

    # 4. FILL PROBABILITY SENSITIVITY
    print("\n" + "-"*95)
    print(" 4. FILL PROBABILITY SENSITIVITY ANALYSIS (1000PEPEUSD)")
    print("-" * 95)
    fill_res = run_fill_sensitivity("backtest/processed/1000PEPEUSD.npz")
    header = f"{'Fill Probability':>18} | {'Trades Executed':>16} | {'Net Closed PnL ($)':>18} | {'Profit Factor':>14}"
    print(header)
    print("-" * len(header))
    for r in fill_res:
        prob_str = f"{r['fill_prob']*100:.0f}%"
        print(f"{prob_str:>18} | {r['trades']:16d} | ${r['net_pnl']:17.4f} | {r['pf']:14.4f}")

if __name__ == "__main__":
    main()
