import argparse
import time
import logging
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backtest.paper_trader_gold_arb import GoldArbPaperTrader

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )

def main():
    parser = argparse.ArgumentParser(description="Run Same-Venue Gold Funding Arbitrage Paper Trader")
    parser.add_argument('--test-run', action='store_true', help="Run a single cycle test and exit")
    parser.add_argument('--cycles', type=int, default=1, help="Number of cycles to run in test-run mode")
    parser.add_argument('--interval', type=int, default=10, help="Loop interval in seconds")
    args = parser.parse_args()

    setup_logging()
    logger = logging.getLogger("RunGoldArbPaper")

    logger.info("Initializing Same-Venue Gold Funding Arbitrage Paper Trader...")

    # Read configuration from config.toml if available
    config = {
        'api_host': 'https://api.india.delta.exchange',
        'leg_long': 'PAXGUSD',
        'leg_short': 'XAUTUSD',
        'effective_leverage': 3.0,
        'position_sizing_pct': 0.50,
        'target_contracts': 9,
        'max_depeg_stop_loss_bps': 300.0,
        'paper_trading_initial_balance': 50.0
    }

    trader = GoldArbPaperTrader(config)
    trader.validate_environment()

    if args.test_run:
        logger.info(f"Running test-run mode for {args.cycles} cycle(s)...")
        for c in range(args.cycles):
            res = trader.evaluate_cycle()
            logger.info(f"Cycle {c+1}/{args.cycles} Result: Status={res['status']} | Equity=${res.get('current_equity_usd', 0):.2f} | De-peg={res.get('depeg_bps', 0):.2f} bps | PAXG Spread={res.get('paxg_spread_bps', 0):.2f} bps | XAUT Spread={res.get('xaut_spread_bps', 0):.2f} bps")
            if c < args.cycles - 1:
                time.sleep(1)
        logger.info("Test-run complete successfully.")
    else:
        logger.info(f"Starting continuous paper-trading loop (interval: {args.interval}s)... Press Ctrl+C to stop.")
        try:
            while True:
                res = trader.evaluate_cycle()
                logger.info(f"Live Snapshot: Equity=${res.get('current_equity_usd', 0):.2f} | De-peg={res.get('depeg_bps', 0):.2f} bps | PAXG Spread={res.get('paxg_spread_bps', 0):.2f} bps | XAUT Spread={res.get('xaut_spread_bps', 0):.2f} bps | Margin Status={res.get('margin_health')}")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            logger.info("Paper-trader stopped by user.")

if __name__ == "__main__":
    main()
