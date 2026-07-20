import sys
import os
import re
import glob
import contextlib
import numpy as np
import toml
import optuna
from strategy import CVDMomentumStrategy

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


def run_simulation(data, symbol_config, general_config):
    # verbose=False suppresses per-tick entry/exit prints -> clean output and
    # thread-safe under n_jobs parallelism (no process-global stdout redirect).
    strategy = CVDMomentumStrategy(None, symbol_config, general_config, verbose=False)
    for row in data:
        strategy.on_tick(row)
    # gross_wins, gross_losses, total_fees (for Fee-Adjusted Profit Factor),
    # net_pnl, total_trades
    return (strategy.gross_wins, strategy.gross_losses, strategy.total_fees,
            strategy.closed_pnl, strategy.total_trades)


# ---------------------------------------------------------------------------
# PROCESS-ISOLATED OPTUNA WORKERS (ask/tell driver)
# ---------------------------------------------------------------------------
# These module-level helpers execute inside fork/spawn worker processes, so they
# must not depend on closure state. The (large) In-Sample array is memmapped
# read-only once per worker via _init_worker to avoid re-pickling it per trial.
_TRAIN_MMAP = None


def _init_worker(train_path):
    global _TRAIN_MMAP
    _TRAIN_MMAP = np.load(train_path, mmap_mode="r")


def _suggest_params(trial):
    # Institutional Iceberg-Absorption Fade search space. Entries are PASSIVE
    # MAKER limits (low fee, zero slippage) that coat the tail of the hidden
    # iceberg, so stops sit tight behind the wall (SL up to 1.0x TP). The key
    # feature is max_price_impact_threshold (log-scaled): the Volume-Weighted
    # Price Impact below which a block is judged to be absorbed by a hidden wall.
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
    # Pure fee-adjusted Profit Factor evaluation; no I/O, no shared stdout.
    trial_config = symbol_config.copy()
    trial_config.update(params)
    gross_wins, gross_losses, total_fees, _net_pnl, trades = run_simulation(
        _TRAIN_MMAP, trial_config, config
    )
    if trades < 3 or gross_wins <= 0.0:
        return 0.0
    denom = max(gross_losses + total_fees, 1e-9)
    profit_factor = gross_wins / denom
    if trades > 80:
        profit_factor /= (1.0 + (trades - 80) * 0.02)
    return profit_factor


# ---------------------------------------------------------------------------
# MULTI-DAY HISTORICAL ROLL ENGINE
# ---------------------------------------------------------------------------
# Daily archives known to be Binance USD-M FUTURES pulls (price scale / venue
# incompatible with the raw spot ingestion used by this WFO). Excluded from the
# multi-day roll so they cannot corrupt the continuous timeline. The prior
# Earlier session flagged 1000PEPEUSD_jul18 / 1000SHIBUSD_jul18 as futures data
# "invalid for raw spot scale". Audited: Binance SPOT does not list 1000PEPEUSDT
# for these dates (404 -> USD-M futures fallback), so EVERY 1000PEPEUSD archive
# is in fact 1000PEPEUSDT USD-M futures trade print data. All files share the
# identical ~0.008-0.013 price scale, so the prior exclusion was a false alarm.
# jul18 is now INCLUDED in the roll to maximize OOS runway. Tuple left empty.
EXCLUDED_DAILY_SUFFIXES = ()


def discover_symbol_files(symbol, directory=PROCESSED_DIR):
    """
    Auto-discover the sequential daily '.npz' files for a given asset inside a
    processed directory. Only files carrying a daily suffix (e.g. *_jul10.npz)
    are matched so that a bare combined blob (e.g. 1000PEPEUSD.npz) is not
    double-counted when building the continuous timeline. Futures archives
    listed in EXCLUDED_DAILY_SUFFIXES are skipped.
    """
    files = sorted(glob.glob(os.path.join(directory, f"{symbol}_*.npz")))
    kept = [
        f for f in files
        if not any(suffix in os.path.basename(f) for suffix in EXCLUDED_DAILY_SUFFIXES)
    ]
    return kept


def load_and_concat(data_files):
    """
    Load multiple daily '.npz' trade archives and concatenate their tick arrays
    sequentially along the time-axis to build a single continuous multi-day
    market timeline. Files are ordered chronologically by their earliest
    exchange timestamp so the resulting array is strictly time-ordered before
    the 70/30 In-Sample / Out-of-Sample split is applied.
    """
    if not data_files:
        raise FileNotFoundError("No daily .npz data files supplied for ingestion.")

    loaded = []
    for fp in data_files:
        arr = np.load(fp)['data']
        earliest_ts = int(arr['exch_ts'].min()) if len(arr) else 0
        loaded.append((fp, arr, earliest_ts))

    # Chronological ordering by earliest exchange timestamp (robust to filenames)
    loaded.sort(key=lambda x: x[2])

    arrays = [x[1] for x in loaded]
    data = np.concatenate(arrays, axis=0)
    manifest = [(os.path.basename(x[0]), len(x[1])) for x in loaded]
    return data, manifest


def walk_forward_optimization(data, target_symbol, manifest=None):
    config = load_toml_config()
    symbol_config = next(
        (s for s in config['strategy']['symbols'] if s['symbol'] == target_symbol),
        None
    )
    if symbol_config is None:
        raise ValueError(
            f"{target_symbol} is not present in config.toml [strategy.symbols]; "
            f"cannot run WFO."
        )

    # Override 1000PEPEUSD Delta configuration into raw PEPE space for Binance tick data WFO
    if target_symbol == "1000PEPEUSD":
        symbol_config = symbol_config.copy()
        symbol_config['contract_size'] = 1000.0
        symbol_config['tick_size'] = 0.00000001

    # WIFUSD is ingested from Binance spot WIFUSDT (raw per-WIF space), matching the
    # Delta standard linear layout: 1 contract = 1 WIF, tick = 0.001.
    if target_symbol == "WIFUSD":
        symbol_config = symbol_config.copy()
        symbol_config['contract_size'] = 1.0
        symbol_config['tick_size'] = 0.001

    total_ticks = len(data)

    # Split: 70% In-Sample (Train), 30% Out-of-Sample (Test). Because the input is
    # now a continuous multi-day timeline, the 30% OOS window spans several
    # consecutive days, giving the highly selective [10000.0, 50000.0] cumulative-volume
    # iceberg-absorption gate sufficient chronological room to capture true edge events.
    split_idx = int(total_ticks * 0.7)
    train_data = data[:split_idx]
    test_data = data[split_idx:]

    if manifest:
        print("Multi-Day Continuous Timeline assembled from (chronological):")
        for name, n in manifest:
            print(f"   - {name}: {n:,} ticks")
    print(f"Total Continuous Ticks:      {total_ticks:,}")
    print(f"Training split (In-Sample):  {len(train_data):,} ticks")
    print(f"Testing split (Out-of-Sample): {len(test_data):,} ticks")

    # -------------------------------------------------------------------------
    # EXECUTE STUDY (maximize Fee-Adjusted Profit Factor)
    #
    # PROCESS-ISOLATED PARALLELISM (bypass the GIL): Optuna is driven via the
    # ask()/tell() API under a ProcessPoolExecutor of n_jobs=8 worker processes.
    # Each worker is a separate OS process, so the pure-Python per-tick hot loop
    # in CVDMomentumStrategy.on_tick runs without GIL contention and scales
    # across the 16-core host -- unlike the prior n_jobs=-1 *threading* backend,
    # which serialized the CPU work on the GIL (near-zero real speedup).
    #
    # DATA TRANSFER: the 70% In-Sample split is a ~750MB numpy array. To avoid
    # re-pickling that giant array per task (and per spawn), we memmap it to a
    # temp file ONCE and have each worker mmap it read-only in its initializer
    # (zero-copy, shared OS page cache). Only the tiny param dict crosses the
    # process boundary per trial -> minimal IPC overhead.
    #
    # THREAD-SAFETY: run_simulation builds a fresh strategy per call with
    # verbose=False, so there is no shared stdout and no redirect_stdout race.
    n_jobs = int(os.environ.get("WFO_JOBS", "8"))
    n_trials = int(os.environ.get("WFO_TRIALS", "20"))
    print(f"\n--- Running Bayesian Optimization on Training Split "
          f"({n_trials} trials, process-isolated n_jobs={n_jobs}) ---")

    import tempfile as _tf
    _fd, train_path = _tf.mkstemp(suffix=".npy", prefix="wfo_train_")
    os.close(_fd)
    np.save(train_path, train_data)

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=42),
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
                if done_count % 5 == 0 or done_count == n_trials:
                    bv = study.best_value if study.best_value is not None else float("nan")
                    print(f"Trial {done_count:2d}/{n_trials} | "
                          f"Best In-Sample Profit Factor: {bv:.4f}")
    finally:
        executor.shutdown(wait=True)
        try:
            os.remove(train_path)
        except OSError:
            pass

    best_params = study.best_params

    print(f"\n==========================================")
    print(f"*** OPTIMAL CONFIG PARAMETERS (Raw Space) ***")
    print(f"==========================================")
    for k, v in best_params.items():
        if isinstance(v, float):
            print(f"  {k:27s} = {v:.6f}")
        else:
            print(f"  {k:27s} = {v}")
    print(f"  Best In-Sample Profit Factor = {study.best_value:.4f}")
    print(f"==========================================\n")

    # Validate winner parameters on testing split (Out-of-Sample)
    final_test_config = symbol_config.copy()
    final_test_config.update(best_params)

    print("--- Running Out-of-Sample Forward Test ---")
    f_gw, f_gl, f_fees, f_net, f_trades = run_simulation(test_data, final_test_config, config)
    f_pf = (f_gw / (f_gl + f_fees)) if (f_trades >= 3 and f_gw > 0.0) else 0.0
    print(f"Out-of-Sample Forward Test Results:")
    print(f"  Trades Executed:            {f_trades}")
    print(f"  Net Closed PnL:             ${f_net:.4f} USD")
    print(f"  Fee-Adjusted Profit Factor: {f_pf:.4f}")
    print("------------------------------------------\n")

    return {
        'symbol': target_symbol,
        'total_ticks': total_ticks,
        'train_ticks': len(train_data),
        'test_ticks': len(test_data),
        'best_params': best_params,
        'is_profit_factor': study.best_value,
        'oos_trades': f_trades,
        'oos_net_pnl': f_net,
        'oos_profit_factor': f_pf,
    }


# ---------------------------------------------------------------------------
# CODESWITCH: AUTOMATED UNDERPERFORMANCE TERMINATION
# ---------------------------------------------------------------------------
def emergency_drop_wifusd():
    """
    Programmatically strip the candidate token (WIFUSD) out of the active
    config.toml strategy symbol blocks AND the websocket subscription list,
    reverting the system to a clean, isolated 1000PEPEUSD production
    configuration. Performed textually to preserve the rest of the file and
    its comments.

    ORDERING MATTERS: the strategy block is removed FIRST (while its
    `symbol = "WIFUSD"` anchor is still intact). The generic websocket string
    removal runs afterwards so it cannot prematurely corrupt the block's
    `symbol = "WIFUSD", ...` line before the block regex can match it.
    """
    with open(CONFIG_PATH, 'r') as f:
        text = f.read()

    # 1. Remove the WIFUSD strategy block (single brace pair, no nesting).
    #    Anchored on `symbol = "WIFUSD"` so it is removed as a whole unit,
    #    including any trailing comma and surrounding whitespace/newlines.
    text = re.sub(
        r'\s*,?\s*\{ symbol = "WIFUSD"[^\}]*\}\s*,?\s*',
        '\n',
        text,
        flags=re.DOTALL,
    )

    # 2. Remove WIFUSD from the websocket symbols array (any positional form)
    text = re.sub(r'"WIFUSD"\s*,\s*', '', text)
    text = re.sub(r',\s*"WIFUSD"', '', text)
    text = re.sub(r'"WIFUSD"', '', text)

    with open(CONFIG_PATH, 'w') as f:
        f.write(text)


def apply_codeswitch(res):
    """
    Post-optimization assessment clause for the candidate asset (WIFUSD).

    Condition: If WIFUSD yields a Fee-Adjusted Profit Factor < 1.0, or if zero
    trades are executed across the expanded OOS window, automatically trigger an
    emergency drop: log a detailed rejection alert and programmatically strip
    WIFUSD from config.toml, reverting to the isolated 1000PEPEUSD production
    configuration.
    """
    if res['symbol'] != CANDIDATE_SYMBOL:
        return False

    failed = (res['oos_profit_factor'] < 1.0) or (res['oos_trades'] == 0)

    if not failed:
        print(f"\n[CODESWITCH] WIFUSD ACCEPTED :: "
              f"OOS PF={res['oos_profit_factor']:.4f} (>= 1.0), "
              f"OOS trades={res['oos_trades']}. Candidate retained in config.toml.")
        return False

    print(f"\n{'='*72}")
    print(f"[CODESWITCH] !!! UNDERPERFORMANCE TERMINATION TRIGGERED :: {CANDIDATE_SYMBOL} !!!")
    print(f"{'='*72}")
    print(f"  Rejection Alert — Performance Leakage detected on candidate {CANDIDATE_SYMBOL}:")
    if res['oos_trades'] == 0:
        print(f"    * ZERO-TRADE GUARD: 0 trades executed across the expanded multi-day OOS window.")
        print(f"      The [5000.0, 30000.0] USD institutional CVD gate fired no valid edge events;")
        print(f"      forward-test is non-informative -> candidate rejected.")
    if res['oos_profit_factor'] < 1.0:
        print(f"    * EDGE GUARD: OOS Fee-Adjusted Profit Factor = {res['oos_profit_factor']:.4f} (< 1.0 constraint).")
        print(f"      Candidate fails to demonstrate a positive, fee-aware edge.")
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
    print(f"MULTI-DAY HISTORICAL ROLL :: WFO SUMMARY METRICS")
    print(f"{'='*72}")
    header = f"{'Symbol':<14}{'TotalTicks':>13}{'IS_PF':>10}{'OOS_Trades':>12}{'OOS_PF':>10}"
    print(header)
    print("-" * len(header))
    for r in results:
        print(f"{r['symbol']:<14}{r['total_ticks']:>13,}"
              f"{r['is_profit_factor']:>10.4f}{r['oos_trades']:>12}"
              f"{r['oos_profit_factor']:>10.4f}")
    print(f"{'='*72}")


def run_sweep(symbols):
    results = []
    dropped = False
    for sym in symbols:
        # Guard: if a candidate was already terminated by a prior codeswitch run
        # it is no longer present in config.toml. Skip gracefully instead of
        # crashing in walk_forward_optimization (which looks the symbol up in
        # the active config).
        cfg = load_toml_config()
        if not any(s['symbol'] == sym for s in cfg['strategy']['symbols']):
            print(f"\n[SKIP] {sym} not present in config.toml [strategy.symbols]; "
                  f"assumed already terminated by codeswitch. Skipping evaluation.")
            continue
        files = discover_symbol_files(sym)
        if not files:
            print(f"\n[SKIP] No daily .npz files found for {sym} in {PROCESSED_DIR}.")
            continue
        print(f"\n{'='*72}")
        print(f"=== MULTI-DAY WFO :: {sym} :: {len(files)} daily file(s) ===")
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
        print("      Run multi-day WFO across [1000PEPEUSD, WIFUSD] with codeswitch.")
        print("  python walk_forward.py <symbol> [file1.npz file2.npz ...]")
        print("      Run multi-day WFO for <symbol> over explicit daily .npz files.")
        print("  python walk_forward.py <symbol> [directory]")
        print("      Run multi-day WFO for <symbol>, auto-discovering *_<symbol>_*.npz")
        print("      in the given directory (or processed/ if omitted).")
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
        print(f"No daily .npz files resolved for symbol '{symbol}'.")
        sys.exit(1)

    res = run_symbol_wfo(symbol, data_files)
    apply_codeswitch(res)
    print_sweep_summary([res])
