import os
import sys
import toml
import numpy as np
from strategy import CVDMomentumStrategy, MACDMomentumStrategy
from symbol_validation import validate_symbol_config

def load_toml_config():
    config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../config.toml'))
    with open(config_path, 'r') as f:
        return toml.load(f)

def run_backtest(npz_file, target_symbol, strategy_name=None):
    config = load_toml_config()

    # Extract symbol config
    symbol_config = None
    for sym_cfg in config['strategy']['symbols']:
        if sym_cfg['symbol'] == target_symbol:
            symbol_config = sym_cfg.copy()
            break

    if not symbol_config:
        print(f"Error: Symbol {target_symbol} not found in config.toml")
        sys.exit(1)

    # Override 1000PEPEUSD Delta configuration into raw PEPE space for Binance tick data backtesting
    if target_symbol == "1000PEPEUSD":
        symbol_config['contract_size'] = 1000.0
        symbol_config['tick_size'] = 0.00000001

    # Override 1000SHIBUSD Delta configuration into raw SHIB space for Binance tick data backtesting
    if target_symbol == "1000SHIBUSD":
        symbol_config['contract_size'] = 1000.0
        symbol_config['tick_size'] = 0.00000001

    data = np.load(npz_file)['data']
    sample_price = float(data['px'][0]) if len(data) > 0 else None

    # Permanent assertion check prior to simulation
    validate_symbol_config(symbol_config, target_symbol=target_symbol, sample_price=sample_price)

    selected_strategy_type = strategy_name or symbol_config.get('strategy_type', 'cvd_iceberg')

    print(f"\n--- STRATEGY BACKTEST CONFIG ({selected_strategy_type.upper()}) ---")
    print(f"Symbol:               {target_symbol}")
    print(f"Contract Size:        {symbol_config['contract_size']}")
    print(f"Order Size:           {symbol_config['order_size']}")
    print("------------------------------------\n")

    # Run strategy simulation
    if selected_strategy_type == "macd_momentum" or selected_strategy_type == "macd":
        strategy = MACDMomentumStrategy(None, symbol_config, config)
    else:
        strategy = CVDMomentumStrategy(None, symbol_config, config)

    print(f"Processing simulation using {strategy.__class__.__name__}...")

    for row in data:
        strategy.on_tick(row)

    print("\n=== BACKTEST RESULTS ===")
    print(f"Total Ticks Processed: {strategy.total_ticks:,}")
    print(f"Total Trades Executed:  {strategy.total_trades}")
    print(f"Net Closed PnL:         ${strategy.closed_pnl:.4f} USD")
    print("========================\n")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python run_backtest.py <npz_file> <symbol> [strategy_name]")
        sys.exit(1)
    strat = sys.argv[3] if len(sys.argv) > 3 else None
    run_backtest(sys.argv[1], sys.argv[2], strat)
