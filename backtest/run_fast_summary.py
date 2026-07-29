import os
import sys
import toml
import numpy as np
import pandas as pd
from strategy import MACDMomentumStrategy

def load_toml_config():
    config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../config.toml'))
    with open(config_path, 'r') as f:
        return toml.load(f)

def benchmark_dataset(npz_file, symbol, threshold_pct):
    config = load_toml_config()
    
    symbol_config = None
    for sym_cfg in config['strategy']['symbols']:
        if sym_cfg['symbol'] == symbol:
            symbol_config = sym_cfg.copy()
            break

    if not symbol_config:
        symbol_config = {
            'symbol': symbol,
            'contract_size': 1000.0 if '1000' in symbol else 1.0,
            'order_size': 1000 if '1000' in symbol else 10,
            'tick_size': 0.00000001 if '1000' in symbol else 0.00001,
            'hold_ticks': 600,
            'entry_cooldown_ticks': 100,
            'atr_period': 14,
            'max_capacity': 1000,
        }

    if symbol == "1000PEPEUSD":
        symbol_config['contract_size'] = 1.0
        symbol_config['tick_size'] = 0.00001

    symbol_config['norm_threshold_pct'] = threshold_pct
    symbol_config['sl_atr_mult'] = 1.5
    symbol_config['risk_reward_ratio'] = 1.5

    data = np.load(npz_file)['data']
    strategy = MACDMomentumStrategy(None, symbol_config, config, verbose=False)

    equity_curve = [0.0]
    cumulative_pnl = 0.0
    wins = 0
    losses = 0

    for row in data:
        prev_pnl = strategy.closed_pnl
        strategy.on_tick(row)
        if strategy.closed_pnl != prev_pnl:
            pnl_diff = strategy.closed_pnl - prev_pnl
            cumulative_pnl = strategy.closed_pnl
            equity_curve.append(cumulative_pnl)
            if pnl_diff > 0:
                wins += 1
            else:
                losses += 1

    equity_arr = np.array(equity_curve)
    peak = np.maximum.accumulate(equity_arr)
    drawdowns = peak - equity_arr
    max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

    total_trades = len(strategy.trade_records)
    win_rate = (wins / total_trades * 100.0) if total_trades > 0 else 0.0
    pf = (strategy.gross_wins / strategy.gross_losses) if strategy.gross_losses > 0 else (float('inf') if strategy.gross_wins > 0 else 0.0)
    avg_pnl = (strategy.closed_pnl / total_trades) if total_trades > 0 else 0.0

    return {
        'Dataset': os.path.basename(npz_file),
        'Symbol': symbol,
        'Threshold %': f"{threshold_pct}%",
        'Ticks': f"{len(data):,}",
        'Trades': total_trades,
        'Wins': wins,
        'Losses': losses,
        'Win Rate': f"{win_rate:.1f}%",
        'Gross Wins ($)': f"${strategy.gross_wins:,.2f}",
        'Gross Losses ($)': f"${strategy.gross_losses:,.2f}",
        'Profit Factor': f"{pf:.2f}",
        'Total Fees ($)': f"${strategy.total_fees:,.2f}",
        'Net PnL ($)': f"${strategy.closed_pnl:,.2f}",
        'Max DD ($)': f"${max_dd:,.2f}",
        'Avg PnL/Trade ($)': f"${avg_pnl:,.2f}",
    }

def main():
    processed_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), 'processed'))
    datasets = [
        ('1000PEPEUSD.npz', '1000PEPEUSD'),
        ('1000PEPEUSD_jul10.npz', '1000PEPEUSD'),
        ('1000PEPEUSD_jul16.npz', '1000PEPEUSD'),
    ]

    thresholds = [0.01, 0.02, 0.05]
    records = []

    print("Running MACD Fast Summary Benchmarks...")
    for fn, sym in datasets:
        path = os.path.join(processed_dir, fn)
        if not os.path.exists(path):
            continue
        for th in thresholds:
            rec = benchmark_dataset(path, sym, th)
            records.append(rec)

    df = pd.DataFrame(records)
    print("\n" + df.to_string(index=False))

    output_csv = os.path.join(os.path.dirname(__file__), 'macd_fast_results.csv')
    df.to_csv(output_csv, index=False)
    print(f"\nResults saved to {output_csv}")

if __name__ == '__main__':
    main()
