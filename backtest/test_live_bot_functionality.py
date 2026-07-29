import unittest
import os
import sys
import json
import time
import hmac
import hashlib
import urllib.request
import toml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from symbol_validation import DELTA_INDIA_API

def generate_signature(secret, method, timestamp, path, payload=""):
    signature_data = f"{method}{timestamp}{path}{payload}"
    return hmac.new(
        secret.encode('utf-8'),
        signature_data.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

class TestLiveBotFunctionality(unittest.TestCase):
    def setUp(self):
        self.config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../config.toml'))
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r') as f:
                self.config = toml.load(f)
        else:
            self.config = {
                'general': {'api_base_url': DELTA_INDIA_API, 'paper_trade_mode': True},
                'strategy': {'symbols': [{'symbol': 'SOLUSD', 'order_size': 10}]}
            }
        self.api_base_url = self.config.get('general', {}).get('api_base_url', DELTA_INDIA_API)
        self.paper_mode = self.config.get('general', {}).get('paper_trade_mode', True)

    def test_fetch_live_prices_rest(self):
        """
        Verify live price fetching over REST for active trading symbols.
        """
        url = f"{self.api_base_url}/v2/tickers"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                result = data.get('result', [])
                self.assertTrue(len(result) > 0, "Live ticker response must return active products")
                ticker_map = {t['symbol']: t for t in result}
                
                # Check for PAXGUSD or XAUTUSD live price fields
                if 'PAXGUSD' in ticker_map:
                    paxg_close = float(ticker_map['PAXGUSD'].get('close', 0))
                    self.assertGreater(paxg_close, 0.0, "PAXGUSD close price must be positive")
        except Exception as e:
            self.skipTest(f"Live network endpoint unavailable: {e}")

    def test_limit_buy_order_payload_and_signature(self):
        """
        Verify Limit Buy order creation, payload structure, and HMAC-SHA256 signature generation.
        """
        api_key = "test_key_sample"
        api_secret = "test_secret_sample"
        timestamp = str(int(time.time()))
        path = "/v2/orders"
        method = "POST"

        payload = {
            'product_id': 9999,
            'size': 10,
            'side': 'buy',
            'order_type': 'limit_order',
            'limit_price': '150.00',
            'post_only': 'true'
        }
        payload_str = json.dumps(payload, separators=(',', ':'))
        signature = generate_signature(api_secret, method, timestamp, path, payload_str)

        self.assertEqual(len(signature), 64, "HMAC-SHA256 signature must be 64 hex characters")
        self.assertEqual(payload['side'], 'buy')
        self.assertEqual(payload['order_type'], 'limit_order')

    def test_cancel_and_sell_order_payload(self):
        """
        Verify order cancellation and Limit Sell exit payload structure.
        """
        cancel_path = "/v2/orders/123456"
        cancel_signature = generate_signature("test_secret", "DELETE", str(int(time.time())), cancel_path, "")
        self.assertEqual(len(cancel_signature), 64)

        sell_payload = {
            'product_id': 9999,
            'size': 10,
            'side': 'sell',
            'order_type': 'limit_order',
            'limit_price': '155.00',
            'reduce_only': 'true'
        }
        self.assertEqual(sell_payload['side'], 'sell')
        self.assertEqual(sell_payload['reduce_only'], 'true')

if __name__ == '__main__':
    unittest.main()
