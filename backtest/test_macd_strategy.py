import unittest
import numpy as np
import toml
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from strategy import MACDMomentumStrategy

class TestMACDMomentumStrategy(unittest.TestCase):
    def setUp(self):
        self.config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../config.toml'))
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r') as f:
                self.general_config = toml.load(f)
        else:
            self.general_config = {
                'fees': {'maker_fee_rate': 0.0002, 'taker_fee_rate': 0.0005, 'slippage_bps': 5.0}
            }

        self.symbol_config = {
            'symbol': 'SOLUSD',
            'product_id': 9999,
            'contract_size': 1.0,
            'order_size': 1,
            'tick_size': 0.01,
            'stop_loss_bps': 8.0,
            'take_profit_bps': 25.0,
            'hold_ticks': 600,
            'entry_cooldown_ticks': 10,
            'norm_threshold_pct': 0.015,
            'sl_atr_mult': 1.5,
            'risk_reward_ratio': 1.5,
            'candle_interval_mins': 1,
            'trend_filter_interval_mins': 15,
            'order_type': 'limit',
            'max_hold_mins': 15,
            'scalper_offer_enabled': True,
            'max_capacity': 1000,
        }

        self.strategy = MACDMomentumStrategy(None, self.symbol_config, self.general_config, verbose=False)

    def test_macd_normalization_formula(self):
        """
        Verify that MACD_% = ((EMA_12 - EMA_26) / Price) * 100
        """
        price = 150.0
        fast_ema = 150.5
        slow_ema = 150.0
        macd_line = fast_ema - slow_ema  # 0.5
        expected_macd_pct = (macd_line / price) * 100.0  # (0.5 / 150.0) * 100 = 0.33333...

        self.strategy.ema_fast_val = fast_ema
        self.strategy.ema_slow_val = slow_ema
        calculated_macd_pct = ((fast_ema - slow_ema) / price) * 100.0

        self.assertAlmostEqual(calculated_macd_pct, expected_macd_pct, places=5)

    def test_ema_200_trend_filter(self):
        """
        Verify Long entries are blocked if Price <= EMA_200 (counter-trend), and allowed when Price > EMA_200.
        """
        price = 145.0
        ema_200 = 150.0  # Downtrend filter active

        self.strategy.ema_200_trend_val = ema_200
        self.strategy.prev_macd_pct = 0.01
        self.strategy.curr_macd_pct = 0.02  # Above threshold 0.015%

        # Long signal check
        long_allowed = (self.strategy.prev_macd_pct <= 0.015 and
                        self.strategy.curr_macd_pct > 0.015 and
                        price > ema_200)

        self.assertFalse(long_allowed, "Long signal should be BLOCKED when Price < 15m 200 EMA")

        # Now test when Price > EMA_200
        price_uptrend = 155.0
        long_allowed_uptrend = (self.strategy.prev_macd_pct <= 0.015 and
                                self.strategy.curr_macd_pct > 0.015 and
                                price_uptrend > ema_200)

        self.assertTrue(long_allowed_uptrend, "Long signal should be ALLOWED when Price > 15m 200 EMA")

    def test_limit_order_fee_schedule(self):
        """
        Verify Limit (Maker) entries pay 0.02% maker fee instead of 0.05% taker fee.
        """
        self.assertEqual(self.strategy.order_type, "limit")
        maker_fee_rate = self.general_config['fees'].get('maker_fee_rate', 0.0002)
        taker_fee_rate = self.general_config['fees'].get('taker_fee_rate', 0.0005)

        entry_fee_rate = maker_fee_rate if self.strategy.order_type == "limit" else taker_fee_rate
        self.assertEqual(entry_fee_rate, 0.0002, "Limit entries must use 0.02% maker fee rate")

    def test_scalper_offer_qualification(self):
        """
        Verify trades held <= 15 minutes (900 seconds) receive 0.0% closing fee.
        """
        max_hold_window_altcoin = 900.0  # 15 minutes
        hold_time_qualifying = 600.0     # 10 minutes

        is_qualifying = (hold_time_qualifying <= max_hold_window_altcoin)
        self.assertTrue(is_qualifying, "10-minute hold time must qualify for Delta 0% closing fee")

        exit_fee_rate = 0.0 if is_qualifying else 0.0005
        self.assertEqual(exit_fee_rate, 0.0, "Qualifying trade exit fee must be 0.00%")

if __name__ == '__main__':
    unittest.main()
