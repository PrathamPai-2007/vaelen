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

def run_symbol_backtest(npz_file, symbol, threshold_pct=0.02, sl_atr_mult=1.5, risk_reward_ratio=1.5):
    config = load_toml_config()
    
    symbol_config = None
    for sym_cfg in config['strategy']['symbols']:
        if sym_cfg['symbol'] == symbol:
            symbol_config = sym_cfg.copy()
            break

    if not symbol_config:
        # Default config scaling per asset class
        order_size = 1
        tick_size = 0.01
        contract_size = 1.0

        if "BTC" in symbol:
            order_size = 1
            tick_size = 0.1
            contract_size = 1.0
        elif "ETH" in symbol:
            order_size = 1
            tick_size = 0.05
            contract_size = 1.0
        elif "SOL" in symbol:
            order_size = 10
            tick_size = 0.01
            contract_size = 1.0
        elif "XRP" in symbol or "ADA" in symbol or "DOGE" in symbol:
            order_size = 1000
            tick_size = 0.0001
            contract_size = 1.0
        elif "1000" in symbol:
            order_size = 1000
            tick_size = 0.00001
            contract_size = 1.0

        symbol_config = {
            'symbol': symbol,
            'product_id': 9999,
            'contract_size': contract_size,
            'order_size': order_size,
            'tick_size': tick_size,
            'stop_loss_bps': 8.0,
            'take_profit_bps': 25.0,
            'hold_ticks': 600,
            'entry_cooldown_ticks': 100,
            'trailing_stop_atr_mult': 1.65,
            'min_trailing_stop_distance': tick_size * 2,
            'atr_period': 14,
            'lookback_ticks': 24,
            'volume_threshold': 0.36,
            'min_cvd_notional_usd': 10000.0,
            'max_capacity': 1000,
        }

    symbol_config['norm_threshold_pct'] = threshold_pct
    symbol_config['sl_atr_mult'] = sl_atr_mult
    symbol_config['risk_reward_ratio'] = risk_reward_ratio

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
        'Symbol': symbol,
        'Dataset': os.path.basename(npz_file),
        'Threshold': f"{threshold_pct}%",
        'Ticks': len(data),
        'Trades': total_trades,
        'Wins': wins,
        'Losses': losses,
        'Win Rate': win_rate,
        'Gross Wins ($)': strategy.gross_wins,
        'Gross Losses ($)': strategy.gross_losses,
        'Profit Factor': pf,
        'Total Fees ($)': strategy.total_fees,
        'Net PnL ($)': strategy.closed_pnl,
        'Max DD ($)': max_dd,
        'Avg PnL/Trade ($)': avg_pnl,
    }

def main():
    processed_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), 'processed'))
    benchmarks = [
        ('ETHUSD.npz', 'ETHUSD'),
        ('SOLUSD_jul16.npz', 'SOLUSD'),
        ('BTCUSD.npz', 'BTCUSD'),
        ('XRPUSD_jul16.npz', 'XRPUSD'),
        ('ADAUSD_jul16.npz', 'ADAUSD'),
        ('DOGEUSD_jul16.npz', 'DOGEUSD'),
        ('WIFUSD_jul16.npz', 'WIFUSD'),
        ('1000PEPEUSD_jul16.npz', '1000PEPEUSD'),
    ]

    results = []
    thresholds = [0.01, 0.02, 0.05]

    print("Running MACD Momentum Strategy Cross-Symbol Benchmarks...")
    for fn, sym in benchmarks:
        path = os.path.join(processed_dir, fn)
        if not os.path.exists(path):
            continue
        for th in thresholds:
            res = run_symbol_backtest(path, sym, threshold_pct=th)
            results.append(res)
            print(f"[{res['Symbol']:10s} | Thresh {th:.2f}%] Trades: {res['Trades']:4d} | WinRate: {res['Win Rate']:5.1f}% | Net PnL: ${res['Net PnL ($)']:10.2f} | PF: {res['Profit Factor']:.2f}")

    df = pd.DataFrame(results)
    output_path = os.path.join(os.path.dirname(__file__), 'macd_cross_symbol_results.csv')
    df.to_csv(output_path, index=False)
    print(f"\nSaved cross-symbol benchmark results to {output_path}")

if __name__ == '__main__':
    main()
