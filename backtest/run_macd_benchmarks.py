import os
import sys
import toml
import numpy as np
import pandas as pd
from strategy import MACDMomentumStrategy
from symbol_validation import validate_symbol_config

def load_toml_config():
    config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../config.toml'))
    with open(config_path, 'r') as f:
        return toml.load(f)

def run_single_benchmark(npz_path, symbol, threshold_pct=0.01, sl_atr_mult=1.5, risk_reward_ratio=1.5):
    config = load_toml_config()
    
    # Extract symbol config
    symbol_config = None
    for sym_cfg in config['strategy']['symbols']:
        if sym_cfg['symbol'] == symbol:
            symbol_config = sym_cfg.copy()
            break

    if not symbol_config:
        # Fallback default symbol config if not explicitly in config.toml
        symbol_config = {
            'symbol': symbol,
            'product_id': 114716,
            'contract_size': 1.0,
            'order_size': 10,
            'tick_size': 0.01 if 'BTC' in symbol or 'ETH' in symbol else 0.00001,
            'stop_loss_bps': 8.0,
            'take_profit_bps': 25.0,
            'hold_ticks': 600,
            'entry_cooldown_ticks': 100,
            'trailing_stop_atr_mult': 1.65,
            'min_trailing_stop_distance': 0.0001,
            'atr_period': 14,
            'lookback_ticks': 24,
            'volume_threshold': 0.36,
            'min_cvd_notional_usd': 10000.0,
            'max_capacity': 1000,
        }

    if symbol == "1000PEPEUSD":
        symbol_config['contract_size'] = 1000.0
        symbol_config['tick_size'] = 0.00000001

    if symbol == "1000SHIBUSD":
        symbol_config['contract_size'] = 1000.0
        symbol_config['tick_size'] = 0.00000001

    # Override parameters
    symbol_config['norm_threshold_pct'] = threshold_pct
    symbol_config['sl_atr_mult'] = sl_atr_mult
    symbol_config['risk_reward_ratio'] = risk_reward_ratio

    data = np.load(npz_path)['data']
    
    # Instantiate strategy in non-verbose mode for fast benchmarking
    strategy = MACDMomentumStrategy(None, symbol_config, config, verbose=False)

    equity_curve = [0.0]
    cumulative_pnl = 0.0
    wins = 0
    losses = 0

    for row in data:
        prev_closed = strategy.closed_pnl
        strategy.on_tick(row)
        if strategy.closed_pnl != prev_closed:
            pnl_diff = strategy.closed_pnl - prev_closed
            cumulative_pnl = strategy.closed_pnl
            equity_curve.append(cumulative_pnl)
            if pnl_diff > 0:
                wins += 1
            else:
                losses += 1

    # Drawdown calculation
    equity_arr = np.array(equity_curve)
    peak = np.maximum.accumulate(equity_arr)
    drawdowns = peak - equity_arr
    max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

    total_trades = len(strategy.trade_records)
    win_rate = (wins / total_trades * 100.0) if total_trades > 0 else 0.0
    profit_factor = (strategy.gross_wins / strategy.gross_losses) if strategy.gross_losses > 0 else (float('inf') if strategy.gross_wins > 0 else 0.0)
    avg_pnl = (strategy.closed_pnl / total_trades) if total_trades > 0 else 0.0

    return {
        'dataset': os.path.basename(npz_path),
        'symbol': symbol,
        'threshold_pct': threshold_pct,
        'sl_atr_mult': sl_atr_mult,
        'rr_ratio': risk_reward_ratio,
        'total_ticks': len(data),
        'total_trades': total_trades,
        'wins': wins,
        'losses': losses,
        'win_rate_pct': win_rate,
        'gross_wins': strategy.gross_wins,
        'gross_losses': strategy.gross_losses,
        'profit_factor': profit_factor,
        'total_fees': strategy.total_fees,
        'net_pnl': strategy.closed_pnl,
        'max_drawdown': max_dd,
        'avg_pnl_per_trade': avg_pnl,
    }

def main():
    processed_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), 'processed'))
    files_to_test = [
        ('1000PEPEUSD_jul10.npz', '1000PEPEUSD'),
        ('1000PEPEUSD_jul16.npz', '1000PEPEUSD'),
        ('1000PEPEUSD.npz', '1000PEPEUSD'),
        ('WIFUSD_jul16.npz', 'WIFUSD'),
        ('XRPUSD_jul16.npz', 'XRPUSD'),
    ]

    thresholds = [0.005, 0.01, 0.02, 0.05, 0.15]
    results = []

    print("Running MACD Momentum Strategy Benchmarks...")
    for filename, symbol in files_to_test:
        path = os.path.join(processed_dir, filename)
        if not os.path.exists(path):
            continue
        for thresh in thresholds:
            res = run_single_benchmark(path, symbol, threshold_pct=thresh)
            results.append(res)
            print(f"[{res['dataset']}] Thresh: {thresh:.3f}% | Trades: {res['total_trades']:3d} | "
                  f"WinRate: {res['win_rate_pct']:5.1f}% | Net PnL: ${res['net_pnl']:8.2f} USD | "
                  f"PF: {res['profit_factor']:.2f}")

    df = pd.DataFrame(results)
    output_path = os.path.join(os.path.dirname(__file__), 'macd_benchmark_results.csv')
    df.to_csv(output_path, index=False)
    print(f"\nSaved benchmark results to {output_path}")

if __name__ == '__main__':
    main()
