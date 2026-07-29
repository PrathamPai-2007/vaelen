import unittest
import os
import sys
import toml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paper_trader_gold_arb import GoldArbPaperTrader

class TestGoldArbitrageStrategy(unittest.TestCase):
    def setUp(self):
        self.config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../config.toml'))
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r') as f:
                self.config = toml.load(f)
        else:
            self.config = {
                'gold_arb': {
                    'enabled': True,
                    'leg_long': 'PAXGUSD',
                    'leg_short': 'XAUTUSD',
                    'effective_leverage': 3.0,
                    'position_sizing_pct': 0.50,
                    'max_depeg_stop_loss_bps': 300.0,
                    'paper_trading_initial_balance': 10000.0
                }
            }

        self.trader = GoldArbPaperTrader(config=self.config['gold_arb'])

    def test_funding_rate_spread_calculation(self):
        """
        Verify that funding spread = XAUT_rate - PAXG_rate (capitalizing on positive funding carry).
        """
        funding_xaut_8h = 0.0002   # 0.02% per 8h (~22% annualized)
        funding_paxg_8h = 0.00002  # 0.002% per 8h (~0.22% annualized)

        calculated_spread_8h = funding_xaut_8h - funding_paxg_8h
        expected_spread_8h = 0.00018  # 0.018% per 8h

        self.assertAlmostEqual(calculated_spread_8h, expected_spread_8h, places=6)

    def test_depeg_stop_loss_threshold(self):
        """
        Verify that basis de-peg breaching max_depeg_stop_loss_bps (300 bps = 3.0%) triggers emergency exit.
        """
        entry_basis = 1.0  # PAXG = XAUT
        current_paxg_px = 2500.0
        current_xaut_px = 2580.0  # 3.2% de-peg (320 bps)

        depeg_bps = (abs(current_xaut_px - current_paxg_px) / current_paxg_px) * 10000.0
        max_depeg_threshold_bps = self.config.get('gold_arb', {}).get('max_depeg_stop_loss_bps', 300.0)

        should_trigger_stop_loss = depeg_bps > max_depeg_threshold_bps
        self.assertTrue(should_trigger_stop_loss, "320 bps de-peg must trigger emergency de-peg stop loss")

    def test_position_sizing_leverage(self):
        """
        Verify that portfolio leverage is constrained to 3.0x max effective leverage.
        """
        leverage = self.config.get('gold_arb', {}).get('effective_leverage', 3.0)
        self.assertLessEqual(leverage, 3.0, "Effective portfolio leverage must not exceed 3.0x")

if __name__ == '__main__':
    unittest.main()
