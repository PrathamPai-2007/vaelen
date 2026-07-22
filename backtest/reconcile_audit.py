import os
import sys
import glob
import numpy as np
import random
from collections import deque
from scipy import stats
import toml

sys.path.append(os.path.abspath(os.path.dirname(__file__)))
from strategy import CVDMomentumStrategy
from walk_forward import walk_forward_optimization, calculate_bootstrap_pf_lcb, load_toml_config

# Set deterministic seeds
def seed_everything(seed=42):
    random.seed(seed)
    np.random.seed(seed)

def run_reconciled_audit():
    seed_everything(42)
    config = load_toml_config()
    
    print("="*95)
    print("     DETERMINISTIC RECONCILIATION AUDIT (Fixed Random Seed = 42)")
    print("="*95)

    # 1. SIGNAL VALIDITY (1000PEPEUSD.npz)
    print("\n--- 1. SIGNAL VALIDITY CHECK (1000PEPEUSD.npz) ---")
    data = np.load("backtest/processed/1000PEPEUSD.npz")['data']
    px = data['px'].astype(np.float64)
    qty = data['qty'].astype(np.float64)
    ev = data['ev'].astype(np.int64)
    side = np.where((ev & 536870912) != 0, 1.0, -1.0)
    n = len(px)
    cvd = np.cumsum(qty * side)
    
    lookback = 24
    min_cvd_notional = 10000.0
    max_impact = 1e-6
    cooldown = 2000
    
    kernel = np.ones(lookback + 1, dtype=np.float64)
    cum_vol_all = np.convolve(qty, kernel, mode='full')[:n]
    
    long_signals, short_signals = [], []
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
        
        can_absorb = (
            v > cached_p95 and
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

    print(f"Total Signal Triggers: {len(long_signals) + len(short_signals)} (Long: {len(long_signals)}, Short: {len(short_signals)})")
    horizons = [10, 25, 50, 100, 200, 500, 1000]
    header = f"{'Horizon':>8} | {'Count':>6} | {'Mean (bps)':>11} | {'Med (bps)':>10} | {'Win Rate':>9} | {'Long WR':>8} | {'Short WR':>8} | {'t-stat':>7} | {'p-val':>7}"
    print("-" * len(header))
    print(header)
    print("-" * len(header))
    
    for h in horizons:
        returns, long_rets, short_rets = [], [], []
        for idx in long_signals:
            if idx + h < n:
                ret = (px[idx + h] - px[idx]) / px[idx] * 10000.0
                returns.append(ret)
                long_rets.append(ret)
        for idx in short_signals:
            if idx + h < n:
                ret = (px[idx] - px[idx + h]) / px[idx] * 10000.0
                returns.append(ret)
                short_rets.append(ret)
        if returns:
            returns = np.array(returns)
            m_ret = np.mean(returns)
            med_ret = np.median(returns)
            wr = np.mean(returns > 0) * 100.0
            l_wr = np.mean(np.array(long_rets) > 0) * 100.0 if long_rets else 0.0
            s_wr = np.mean(np.array(short_rets) > 0) * 100.0 if short_rets else 0.0
            t_stat, p_val = stats.ttest_1samp(returns, 0.0)
            print(f"{h:8d} | {len(returns):6d} | {m_ret:11.4f} | {med_ret:10.4f} | {wr:8.2f}% | {l_wr:7.2f}% | {s_wr:7.2f}% | {t_stat:7.2f} | {p_val:7.4f}")

    # 2. VOLATILITY REGIME BREAKDOWN (1000PEPEUSD.npz across 10 chunks)
    print("\n--- 2. VOLATILITY REGIME BREAKDOWN (1000PEPEUSD.npz) ---")
    chunk_len = n // 10
    sym_cfg = config['strategy']['symbols'][0].copy()
    sym_cfg['contract_size'] = 1000.0
    sym_cfg['tick_size'] = 0.00000001
    
    chunks = []
    for c in range(10):
        seed_everything(42 + c)
        sub_data = data[c*chunk_len : (c+1)*chunk_len]
        sub_px = px[c*chunk_len : (c+1)*chunk_len]
        log_rets = np.diff(np.log(np.maximum(sub_px, 1e-9)))
        r_vol = np.std(log_rets) * 10000.0
        
        st = CVDMomentumStrategy(None, sym_cfg, config, verbose=False)
        for row in sub_data:
            st.on_tick(row)
        pf = st.gross_wins / max(st.gross_losses + st.total_fees, 1e-9) if st.total_trades > 0 else 0.0
        chunks.append({
            'chunk': c + 1,
            'vol_bps': r_vol,
            'trades': st.total_trades,
            'net_pnl': st.closed_pnl,
            'pf': pf
        })
        
    # Sort by Volatility Rank
    sorted_chunks = sorted(chunks, key=lambda x: x['vol_bps'])
    header = f"{'Vol Rank':>8} | {'Chunk #':>7} | {'Realized Vol (bps)':>19} | {'Trades':>7} | {'Net PnL ($)':>12} | {'PF':>8}"
    print("-" * len(header))
    print(header)
    print("-" * len(header))
    for rank, m in enumerate(sorted_chunks, 1):
        print(f"{rank:8d} | {m['chunk']:7d} | {m['vol_bps']:19.4f} | {m['trades']:7d} | ${m['net_pnl']:11.4f} | {m['pf']:8.4f}")

    # 3. FILL PROBABILITY SENSITIVITY (Deterministic Seeds per run)
    print("\n--- 3. FILL PROBABILITY SENSITIVITY ANALYSIS (1000PEPEUSD.npz) ---")
    probs = [1.00, 0.75, 0.55, 0.40, 0.20]
    header = f"{'Fill Probability':>18} | {'Trades Executed':>16} | {'Net Closed PnL ($)':>18} | {'Profit Factor':>14}"
    print("-" * len(header))
    print(header)
    print("-" * len(header))
    for prob in probs:
        seed_everything(42)
        cfg = sym_cfg.copy()
        cfg['fill_probability'] = prob
        st = CVDMomentumStrategy(None, cfg, config, verbose=False)
        for row in data:
            st.on_tick(row)
        pf = st.gross_wins / max(st.gross_losses + st.total_fees, 1e-9) if st.total_trades > 0 else 0.0
        prob_str = f"{prob*100:.0f}%"
        print(f"{prob_str:>18} | {st.total_trades:16d} | ${st.closed_pnl:17.4f} | {pf:14.4f}")

if __name__ == "__main__":
    run_reconciled_audit()
