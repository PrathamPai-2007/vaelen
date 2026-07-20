import os
import sys
import toml
import numpy as np
from strategy import CVDMomentumStrategy

def load_toml_config():
    config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../config.toml'))
    with open(config_path, 'r') as f:
        return toml.load(f)

def run_backtest(npz_file, target_symbol):
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

    # --- NO AUTO-SCALING: use config values directly ---
    # The 95th percentile volume filter and WFO guardrails handle regime adaptation.
    # Cooldown and hold are hard-coded to guardrail-validated values.
    symbol_config['entry_cooldown_ticks'] = 75
    symbol_config['hold_ticks'] = 600

    # min_cvd_notional_usd is already dollar-denominated and scale-invariant;
    # no per-asset unit conversion required.

    print(f"\n--- RAW PARAMETERS (no scaling) ---")
    print(f"lookback_ticks:       {symbol_config['lookback_ticks']}")
    print(f"entry_cooldown_ticks: {symbol_config['entry_cooldown_ticks']}")
    print(f"hold_ticks:           {symbol_config['hold_ticks']}")
    print(f"min_cvd_notional_usd: {symbol_config['min_cvd_notional_usd']:.4f} USD")
    print(f"stop_loss_bps:        {symbol_config['stop_loss_bps']}")
    print(f"take_profit_bps:      {symbol_config['take_profit_bps']}")
    print("------------------------------------\n")

    # Run strategy simulation
    strategy = CVDMomentumStrategy(None, symbol_config, config)
    print("Processing simulation...")

    for row in data:
        strategy.on_tick(row)

    print("\n=== BACKTEST RESULTS ===")
    print(f"Total Ticks Processed: {strategy.total_ticks:,}")
    print(f"Total Trades Executed:  {strategy.total_trades}")
    print(f"Net Closed PnL:         ${strategy.closed_pnl:.4f} USD")
    print("========================\n")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python run_backtest.py <npz_file> <symbol>")
        sys.exit(1)
    run_backtest(sys.argv[1], sys.argv[2])
