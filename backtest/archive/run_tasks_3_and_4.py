import os
import sys
import glob
import numpy as np
import toml

sys.path.append(os.path.abspath(os.path.dirname(__file__)))
from strategy import CVDMomentumStrategy
from walk_forward import load_toml_config

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
        realized_vol = np.std(log_rets) * 10000.0  # std of log returns in bps per tick
        
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


if __name__ == "__main__":
    filepath = "backtest/processed/1000PEPEUSD.npz"
    print("\n" + "="*80)
    print(" TASK 3: REGIME / VOLATILITY BREAKDOWN (1000PEPEUSD across 10 Chunks)")
    print("="*80)
    vol_res = run_volatility_regime_analysis(filepath, n_chunks=10)
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

    print("\n" + "="*80)
    print(" TASK 4: FILL PROBABILITY SENSITIVITY ANALYSIS (1000PEPEUSD)")
    print("="*80)
    fill_res = run_fill_sensitivity(filepath)
    header = f"{'Fill Probability':>18} | {'Trades Executed':>16} | {'Net Closed PnL ($)':>18} | {'Profit Factor':>14}"
    print(header)
    print("-" * len(header))
    for r in fill_res:
        prob_str = f"{r['fill_prob']*100:.0f}%"
        print(f"{prob_str:>18} | {r['trades']:16d} | ${r['net_pnl']:17.4f} | {r['pf']:14.4f}")
