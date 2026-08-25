import subprocess
import json
import os
import tempfile
import numpy as np

def run_simulation(data_fp, trial_config, base_config):
    # Depending on OS, the executable has .exe or not
    exe_name = "backtest.exe" if os.name == "nt" else "backtest"
    exe_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "target", "release", exe_name))
    
    # Dump trial config to a temporary toml
    import toml
    trial_full_config = base_config.copy()
    
    # We update the specific symbol in the copy
    for idx, sym_cfg in enumerate(trial_full_config['strategy']['symbols']):
        if sym_cfg['symbol'] == trial_config['symbol']:
            trial_full_config['strategy']['symbols'][idx] = trial_config
            break

    fd, config_path = tempfile.mkstemp(suffix=".toml")
    with os.fdopen(fd, 'w') as f:
        toml.dump(trial_full_config, f)

    try:
        cmd = [exe_path, config_path, trial_config['symbol'], data_fp]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0:
            print(f"Rust execution failed! {res.stderr}")
            return 0.0, 0.0, 0.0, 0.0, 0, []
        
        try:
            output = json.loads(res.stdout.strip().split("\n")[-1])
        except Exception as e:
            print(f"Failed to parse rust output: {e} | stdout: {res.stdout}")
            return 0.0, 0.0, 0.0, 0.0, 0, []
            
        trades = output.get('trades', [])
        
        gross_wins = 0.0
        gross_losses = 0.0
        for t in trades:
            if t['gross_pnl'] > 0:
                gross_wins += t['gross_pnl']
            else:
                gross_losses += abs(t['gross_pnl'])
                
        # (gross_wins, gross_losses, total_fees, net_pnl, trades_count, trade_records)
        return (
            gross_wins,
            gross_losses,
            output.get('fees', 0.0),
            output.get('net_pnl', 0.0),
            output.get('total_trades', 0),
            [(t['gross_pnl'], t['fees']) for t in trades]
        )
    finally:
        try:
            os.remove(config_path)
        except:
            pass
