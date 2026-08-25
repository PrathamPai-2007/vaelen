import os
import sys
import argparse
import subprocess
import json

def main():
    parser = argparse.ArgumentParser(description="Run native Rust historical backtest")
    parser.add_argument("--symbol", type=str, required=True, help="Symbol to backtest (e.g., 1000PEPEUSD)")
    parser.add_argument("--data", type=str, required=True, help="Path to flat binary data file (.bin)")
    parser.add_argument("--config", type=str, default="config.toml", help="Path to config.toml")
    args = parser.parse_args()
    
    exe_name = "backtest.exe" if os.name == "nt" else "backtest"
    exe_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "target", "release", exe_name))
    
    if not os.path.exists(exe_path):
        print(f"Rust binary not found at {exe_path}. Compiling...")
        subprocess.run(["cargo", "build", "--release", "--bin", "backtest"], cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..")), check=True)
        
    print(f"Running Native Rust Backtester for {args.symbol}...")
    cmd = [exe_path, args.config, args.symbol, args.data]
    res = subprocess.run(cmd, text=True)
    
    if res.returncode != 0:
        print(f"Backtest failed with code {res.returncode}")
        sys.exit(res.returncode)

if __name__ == "__main__":
    main()
