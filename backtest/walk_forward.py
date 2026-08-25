import sys
import os
import re
import glob
import contextlib
import numpy as np
import toml
import optuna
from symbol_validation import validate_symbol_config

# Suppress Optuna progress bar and diagnostic prints to keep terminal clean
optuna.logging.set_verbosity(optuna.logging.WARNING)

# Resolve project paths
CONFIG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '../config.toml'))
PROCESSED_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), 'processed'))

# The candidate token subject to the automated underperformance termination clause.
CANDIDATE_SYMBOL = "WIFUSD"
# The isolated production configuration we revert to if the candidate fails.
PRODUCTION_SYMBOL = "1000PEPEUSD"


def load_toml_config():
    with open(CONFIG_PATH, 'r') as f:
        return toml.load(f)


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
            [(max(0.0, t.get('gross_pnl', 0.0)), abs(min(0.0, t.get('gross_pnl', 0.0))), t.get('fees', 0.0) + t.get('slippage', 0.0)) for t in trades]
        )
    finally:
        try:
            os.remove(config_path)
        except:
            pass

def calculate_bootstrap_pf_lcb(trade_records, percentile=5.0, n_resamples=200, seed=42):
    """
    Compute lower-confidence-bound (percentile-th) estimate of Profit Factor
    by bootstrapping the trade record sequence (gross_win, gross_loss, fee_and_slippage).
    Fix #5: penalizes small-sample luck and rewards consistent edge across more trades.
    """
    if not trade_records or len(trade_records) < 3:
        return 0.0
    arr = np.array(trade_records, dtype=np.float64)  # shape (N, 3)
    wins = arr[:, 0]
    losses = arr[:, 1]
    fees = arr[:, 2]

    if np.sum(wins) <= 0.0:
        return 0.0

    n_trades = len(arr)
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, n_trades, size=(n_resamples, n_trades))

    boot_wins = np.sum(wins[indices], axis=1)
    boot_losses = np.sum(losses[indices], axis=1)
    boot_fees = np.sum(fees[indices], axis=1)

    boot_pfs = boot_wins / np.maximum(boot_losses + boot_fees, 1e-9)
    return float(np.percentile(boot_pfs, percentile))


# ---------------------------------------------------------------------------
# PROCESS-ISOLATED OPTUNA WORKERS (ask/tell driver)
# ---------------------------------------------------------------------------
_TRAIN_MMAP = None


def _init_worker(train_path):
    global _TRAIN_MMAP
    _TRAIN_MMAP = np.load(train_path, mmap_mode="r")


def _suggest_params(trial):
    take_profit_bps = trial.suggest_float("take_profit_bps", 10.0, 50.0)
    raw_sl_bps = trial.suggest_float("stop_loss_bps", 5.0, 50.0)
    stop_loss_bps = min(raw_sl_bps, take_profit_bps * 1.0)
    return {
        "lookback_ticks": trial.suggest_int("lookback_ticks", 10, 40),
        "volume_threshold": trial.suggest_float("volume_threshold", 0.02, 0.30),
        "max_price_impact_threshold": trial.suggest_float("max_price_impact_threshold", 1e-8, 1e-4, log=True),
        "take_profit_bps": take_profit_bps,
        "stop_loss_bps": stop_loss_bps,
        "min_cvd_notional_usd": trial.suggest_float("min_cvd_notional_usd", 10000.0, 50000.0),
        "trailing_stop_atr_mult": trial.suggest_float("trailing_stop_atr_mult", 0.5, 2.5),
        "min_trailing_stop_distance": trial.suggest_float("min_trailing_stop_distance", 0.00005, 0.005),
        "entry_cooldown_ticks": trial.suggest_int("entry_cooldown_ticks", 1000, 5000),
        "hold_ticks": trial.suggest_int("hold_ticks", 15, 60),
    }


def objective_worker(params, symbol_config, config):
    # Statistical significance-aware objective evaluation (Fix #5)
    trial_config = symbol_config.copy()
    trial_config.update(params)
    gross_wins, gross_losses, total_fees, _net_pnl, trades, trade_records = run_simulation(
        _TRAIN_MMAP, trial_config, config
    )
    if trades < 3 or gross_wins <= 0.0:
        return 0.0

    # Return bootstrap 5th percentile Profit Factor lower confidence bound
    return calculate_bootstrap_pf_lcb(trade_records, percentile=5.0, n_resamples=200)


# ---------------------------------------------------------------------------
# DATA PROVENANCE & INSTRUMENT INTEGRITY (Fix #6)
# ---------------------------------------------------------------------------
# Daily archives known to be Binance USD-M FUTURES pulls for 1000PEPEUSD / 1000SHIBUSD
# carry price scales (~0.009 per 1000 PEPE) and volume profiles incompatible with Delta's
# actual instrument (~0.0000028 per PEPE). They are excluded to guarantee venue and
# data provenance integrity.
EXCLUDED_DAILY_SUFFIXES = (
    "1000PEPEUSD_jul10", "1000PEPEUSD_jul11", "1000PEPEUSD_jul12", "1000PEPEUSD_jul13",
    "1000PEPEUSD_jul14", "1000PEPEUSD_jul15", "1000PEPEUSD_jul16", "1000PEPEUSD_jul18",
    "1000SHIBUSD_jul18", "DOGEUSD_jul18",
)


def discover_symbol_files(symbol, directory=PROCESSED_DIR):
    """
    Auto-discover the sequential '.npz' files for a given asset inside a processed
    directory. Exclude mismatched venue/instrument daily files listed in
    EXCLUDED_DAILY_SUFFIXES.
    """
    files = sorted(glob.glob(os.path.join(directory, f"{symbol}*.npz")))
    kept = [
        f for f in files
        if not any(suffix in os.path.basename(f) for suffix in EXCLUDED_DAILY_SUFFIXES)
    ]
    return kept


def load_and_concat(data_files):
    if not data_files:
        raise FileNotFoundError("No daily .npz data files supplied for ingestion.")

    loaded = []
    for fp in data_files:
        arr = np.load(fp)['data']
        earliest_ts = int(arr['exch_ts'].min()) if len(arr) else 0
        loaded.append((fp, arr, earliest_ts))

    loaded.sort(key=lambda x: x[2])
    arrays = [x[1] for x in loaded]
    data = np.concatenate(arrays, axis=0)
    manifest = [(os.path.basename(x[0]), len(x[1])) for x in loaded]
    return data, manifest


# ---------------------------------------------------------------------------
# ROLLING WALK-FORWARD OPTIMIZATION (Fix #4)
# ---------------------------------------------------------------------------
def walk_forward_optimization(data, target_symbol, manifest=None):
    config = load_toml_config()
    symbol_config = next(
        (s for s in config['strategy']['symbols'] if s['symbol'] == target_symbol),
        None
    )
    if symbol_config is None:
        raise ValueError(
            f"{target_symbol} is not present in config.toml [strategy.symbols]; cannot run WFO."
        )

    if target_symbol == "1000PEPEUSD":
        symbol_config = symbol_config.copy()
        symbol_config['contract_size'] = 1000.0
        symbol_config['tick_size'] = 0.00000001

        symbol_config = symbol_config.copy()
        symbol_config['contract_size'] = 1.0
        symbol_config['tick_size'] = 0.001

    sample_price = float(data['px'][0]) if len(data) > 0 else None
    validate_symbol_config(symbol_config, target_symbol=target_symbol, sample_price=sample_price)

    total_ticks = len(data)
    n_folds = int(os.environ.get("WFO_FOLDS", "5"))
    n_jobs = int(os.environ.get("WFO_JOBS", "8"))
    n_trials = int(os.environ.get("WFO_TRIALS", "100"))  # Fix #4: at least 100-200 per fold

    print(f"\n==========================================================================")
    print(f"=== ROLLING WALK-FORWARD OPTIMIZATION ({n_folds} Folds, {n_trials} trials/fold) ===")
    print(f"==========================================================================")
    if manifest:
        print("Data files ingested:")
        for name, n in manifest:
            print(f"   - {name}: {n:,} ticks")
    print(f"Total Continuous Ticks: {total_ticks:,}")

    # Build sequential train/test folds (Fix #4)
    chunk_size = total_ticks // (n_folds + 1)
    if chunk_size < 100:
        chunk_size = total_ticks // 2

    fold_results = []
    all_oos_trade_records = []

    for fold_idx in range(n_folds):
        train_start = 0
        train_end = (fold_idx + 1) * chunk_size
        test_start = train_end
        test_end = min((fold_idx + 2) * chunk_size, total_ticks) if fold_idx == n_folds - 1 else (fold_idx + 2) * chunk_size

        train_data = data[train_start:train_end]
        test_data = data[test_start:test_end]

        print(f"\n--- FOLD {fold_idx + 1}/{n_folds} ---")
        print(f"  Train Window: {len(train_data):,} ticks [{train_start}:{train_end}]")
        print(f"  Test Window:  {len(test_data):,} ticks [{test_start}:{test_end}]")

        import tempfile as _tf
        _fd, train_path = _tf.mkstemp(suffix=".bin", prefix=f"wfo_train_f{fold_idx}_")
        os.close(_fd)
        dt = np.dtype([('ts', np.uint64), ('px', np.float64), ('qty', np.float64), ('is_buy', np.uint8), ('padding', 'V7')])
        out_arr = np.empty(len(train_data), dtype=dt)
        out_arr['ts'] = train_data['exch_ts']
        out_arr['px'] = train_data['px']
        out_arr['qty'] = train_data['qty']
        out_arr['is_buy'] = (train_data['ev'] & 128) != 0
        out_arr['padding'] = 0
        with open(train_path, 'wb') as f:
            f.write(out_arr.tobytes())

        study = optuna.create_study(
            direction="maximize",
            sampler=optuna.samplers.TPESampler(seed=42 + fold_idx),
        )

        from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
        executor = ProcessPoolExecutor(
            max_workers=n_jobs, initializer=_init_worker, initargs=(train_path,)
        )

        running = {}
        done_count = 0
        try:
            while done_count < n_trials:
                while len(running) < n_jobs and (done_count + len(running)) < n_trials:
                    trial = study.ask()
                    params = _suggest_params(trial)
                    fut = executor.submit(objective_worker, params, symbol_config, config)
                    running[fut] = trial
                done, _ = wait(list(running.keys()), return_when=FIRST_COMPLETED)
                for fut in done:
                    trial = running.pop(fut)
                    try:
                        value = fut.result()
                    except Exception:
                        value = 0.0
                    study.tell(trial, value)
                    done_count += 1
        finally:
            executor.shutdown(wait=True)
            try:
                os.remove(train_path)
            except OSError:
                pass

        best_params = study.best_params
        best_is_lcb = study.best_value

        # Test on OOS window
        final_test_config = symbol_config.copy()
        final_test_config.update(best_params)
        f_gw, f_gl, f_fees, f_net, f_trades, f_records = run_simulation(test_data, final_test_config, config)
        f_denom = max(f_gl + f_fees, 1e-9)
        f_pf = (f_gw / f_denom) if (f_trades >= 1 and f_gw > 0.0) else 0.0

        print(f"  Fold {fold_idx + 1} Results:")
        print(f"    IS Best LCB PF: {best_is_lcb:.4f}")
        print(f"    OOS Trades:     {f_trades}")
        print(f"    OOS Net PnL:    ${f_net:.4f} USD")
        print(f"    OOS PF:         {f_pf:.4f}")

        fold_results.append({
            'fold': fold_idx + 1,
            'train_ticks': len(train_data),
            'test_ticks': len(test_data),
            'best_params': best_params,
            'is_lcb_pf': best_is_lcb,
            'oos_trades': f_trades,
            'oos_net_pnl': f_net,
            'oos_pf': f_pf,
            'trade_records': f_records,
        })
        all_oos_trade_records.extend(f_records)

    # Compute multi-fold aggregate metrics (Fix #4)
    fold_pfs = [f['oos_pf'] for f in fold_results]
    mean_oos_pf = float(np.mean(fold_pfs))
    std_oos_pf = float(np.std(fold_pfs, ddof=1 if len(fold_pfs) > 1 else 0))
    total_oos_trades = sum(f['oos_trades'] for f in fold_results)
    total_oos_pnl = sum(f['oos_net_pnl'] for f in fold_results)
    
    # Aggregated OOS Bootstrap 5th Percentile PF (Fix #7)
    agg_oos_lcb_pf = calculate_bootstrap_pf_lcb(all_oos_trade_records, percentile=5.0, n_resamples=500) if total_oos_trades >= 3 else 0.0

    print(f"\n==========================================================================")
    print(f"=== ROLLING WALK-FORWARD OVERALL SUMMARY :: {target_symbol} ===")
    print(f"==========================================================================")
    print(f"  Total Folds Evaluated:    {n_folds}")
    print(f"  Total OOS Trades:         {total_oos_trades}")
    print(f"  Total OOS Net PnL:        ${total_oos_pnl:.4f} USD")
    print(f"  Mean OOS Profit Factor:   {mean_oos_pf:.4f}")
    print(f"  Std Dev OOS Profit Factor:{std_oos_pf:.4f}")
    print(f"  Aggregated OOS LCB PF:    {agg_oos_lcb_pf:.4f}")
    print(f"==========================================================================\n")

    return {
        'symbol': target_symbol,
        'total_ticks': total_ticks,
        'n_folds': n_folds,
        'fold_results': fold_results,
        'total_oos_trades': total_oos_trades,
        'total_oos_net_pnl': total_oos_pnl,
        'mean_oos_pf': mean_oos_pf,
        'std_oos_pf': std_oos_pf,
        'agg_oos_lcb_pf': agg_oos_lcb_pf,
        'is_profit_factor': fold_results[-1]['is_lcb_pf'],
        'oos_trades': total_oos_trades,
        'oos_net_pnl': total_oos_pnl,
        'oos_profit_factor': mean_oos_pf,
    }


# ---------------------------------------------------------------------------
# CODESWITCH: AUTOMATED UNDERPERFORMANCE TERMINATION (Fix #7)
# ---------------------------------------------------------------------------
def emergency_drop_wifusd():
    with open(CONFIG_PATH, 'r') as f:
        text = f.read()

    text = re.sub(
        r'\s*,?\s*\{ symbol = "WIFUSD"[^\}]*\}\s*,?\s*',
        '\n',
        text,
        flags=re.DOTALL,
    )
    text = re.sub(r'"WIFUSD"\s*,\s*', '', text)
    text = re.sub(r',\s*"WIFUSD"', '', text)
    text = re.sub(r'"WIFUSD"', '', text)

    with open(CONFIG_PATH, 'w') as f:
        f.write(text)


def apply_codeswitch(res):
    """
    Post-optimization assessment clause for the candidate asset (WIFUSD).

    Condition (Fix #7):
    Require both:
    (a) Aggregated OOS Bootstrap 5th-percentile PF > 1.0 (or Mean OOS PF > 1.0), AND
    (b) OOS performance consistent (PF > 1.0) across a majority of walk-forward folds.
    """
    if res['symbol'] != CANDIDATE_SYMBOL:
        return False

    fold_results = res.get('fold_results', [])
    active_folds = [f for f in fold_results if f['oos_trades'] > 0]
    winning_folds = [f for f in active_folds if f['oos_pf'] > 1.0]

    majority_win = len(winning_folds) >= max(1, len(fold_results) // 2 + 1)
    lcb_pass = res.get('agg_oos_lcb_pf', 0.0) > 1.0 or res.get('mean_oos_pf', 0.0) > 1.0
    trades_pass = res['total_oos_trades'] > 0

    failed = not (majority_win and lcb_pass and trades_pass)

    if not failed:
        print(f"\n[CODESWITCH] {CANDIDATE_SYMBOL} ACCEPTED :: "
              f"Mean OOS PF={res['mean_oos_pf']:.4f}, LCB PF={res['agg_oos_lcb_pf']:.4f} (> 1.0), "
              f"Winning Folds={len(winning_folds)}/{len(fold_results)}. Candidate retained in config.toml.")
        return False

    print(f"\n{'='*72}")
    print(f"[CODESWITCH] !!! UNDERPERFORMANCE TERMINATION TRIGGERED :: {CANDIDATE_SYMBOL} !!!")
    print(f"{'='*72}")
    print(f"  Rejection Alert — Performance Leakage / Underperformance detected on {CANDIDATE_SYMBOL}:")
    if not trades_pass:
        print(f"    * ZERO-TRADE GUARD: 0 trades executed across OOS folds.")
    if not lcb_pass:
        print(f"    * LCB EDGE GUARD: Aggregated OOS LCB PF = {res.get('agg_oos_lcb_pf', 0.0):.4f} (<= 1.0 constraint).")
    if not majority_win:
        print(f"    * FOLD CONSISTENCY GUARD: Only {len(winning_folds)}/{len(fold_results)} folds achieved OOS PF > 1.0.")
    print(f"  ACTION: Emergency strip of {CANDIDATE_SYMBOL} from config.toml strategy + websocket blocks.")
    print(f"  REVERT: Isolated {PRODUCTION_SYMBOL}-only production configuration restored.")
    print(f"{'='*72}\n")

    emergency_drop_wifusd()
    return True


# ---------------------------------------------------------------------------
# ORCHESTRATION / SWEEP
# ---------------------------------------------------------------------------
def run_symbol_wfo(symbol, data_files):
    data, manifest = load_and_concat(data_files)
    return walk_forward_optimization(data, symbol, manifest)


def print_sweep_summary(results):
    print(f"\n{'='*72}")
    print(f"MULTI-FOLD HISTORICAL ROLL :: WFO SUMMARY METRICS")
    print(f"{'='*72}")
    header = f"{'Symbol':<12}{'TotalTicks':>12}{'OOS_Trades':>12}{'OOS_PnL':>12}{'Mean_OOS_PF':>13}{'Std_OOS_PF':>12}{'LCB_OOS_PF':>12}"
    print(header)
    print("-" * len(header))
    for r in results:
        print(f"{r['symbol']:<12}{r['total_ticks']:>12,}"
              f"{r['total_oos_trades']:>12}"
              f"  ${r['total_oos_net_pnl']:>9.4f}"
              f"{r['mean_oos_pf']:>13.4f}"
              f"{r['std_oos_pf']:>12.4f}"
              f"{r['agg_oos_lcb_pf']:>12.4f}")
    print(f"{'='*72}")


def run_sweep(symbols):
    results = []
    dropped = False
    for sym in symbols:
        cfg = load_toml_config()
        if not any(s['symbol'] == sym for s in cfg['strategy']['symbols']):
            print(f"\n[SKIP] {sym} not present in config.toml [strategy.symbols]; skipping evaluation.")
            continue
        files = discover_symbol_files(sym)
        if not files:
            print(f"\n[SKIP] No valid daily .npz files found for {sym} in {PROCESSED_DIR}.")
            continue
        print(f"\n{'='*72}")
        print(f"=== MULTI-FOLD WFO :: {sym} :: {len(files)} data file(s) ===")
        print(f"{'='*72}")
        res = run_symbol_wfo(sym, files)
        results.append(res)
        if apply_codeswitch(res):
            dropped = True
    print_sweep_summary(results)
    if dropped:
        print(f"[CODESWITCH] {CANDIDATE_SYMBOL} terminated; config.toml reverted to "
              f"{PRODUCTION_SYMBOL}-only production configuration.\n")
    return results


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in ('-h', '--help'):
        print("Usage:")
        print("  python walk_forward.py --sweep")
        print("      Run multi-fold WFO across [1000PEPEUSD, WIFUSD] with codeswitch.")
        print("  python walk_forward.py <symbol> [file1.npz file2.npz ...]")
        print("      Run multi-fold WFO for <symbol> over explicit daily .npz files.")
        print("  python walk_forward.py <symbol> [directory]")
        print("      Run multi-fold WFO for <symbol>, auto-discovering files in directory.")
        sys.exit(1)

    if args[0] == "--sweep":
        run_sweep([PRODUCTION_SYMBOL, CANDIDATE_SYMBOL])
        sys.exit(0)

    symbol = args[0]
    rest = args[1:]
    if rest:
        if os.path.isdir(rest[0]):
            data_files = discover_symbol_files(symbol, rest[0])
        else:
            data_files = rest
    else:
        data_files = discover_symbol_files(symbol)

    if not data_files:
        print(f"No valid daily .npz files resolved for symbol '{symbol}'.")
        sys.exit(1)

    res = run_symbol_wfo(symbol, data_files)
    apply_codeswitch(res)
    print_sweep_summary([res])
