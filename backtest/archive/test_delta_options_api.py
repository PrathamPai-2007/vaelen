import json
import urllib.request

def test_delta_options():
    # 1. Fetch products
    url_prod = "https://api.delta.exchange/v2/products?page_size=1000"
    req = urllib.request.Request(url_prod, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        products = json.loads(resp.read().decode('utf-8')).get('result', [])
        
    btc_options = [p for p in products if p.get('contract_type') in ['call_options', 'put_options'] and 'BTC' in p.get('symbol')]
    print(f"Total BTC Options: {len(btc_options)}")
    if btc_options:
        sample = btc_options[0]
        print("Sample BTC Option Product:", sample['symbol'], sample['contract_type'], sample.get('strike_price'), sample.get('settlement_time'))

    # 2. Fetch tickers
    url_tick = "https://api.delta.exchange/v2/tickers?page_size=1000"
    req = urllib.request.Request(url_tick, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        tickers = json.loads(resp.read().decode('utf-8')).get('result', [])

    opt_tickers = [t for t in tickers if t.get('symbol', '').startswith('C-BTC') or t.get('symbol', '').startswith('P-BTC')]
    print(f"Total BTC Option Tickers: {len(opt_tickers)}")
    if opt_tickers:
        print("Sample Ticker Data:", opt_tickers[0])

    # 3. Test klines for a sample option
    if btc_options:
        sym = btc_options[0]['symbol']
        url_kline = f"https://api.delta.exchange/v2/klines?symbol={sym}&resolution=1d"
        try:
            req = urllib.request.Request(url_kline, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                klines = json.loads(resp.read().decode('utf-8')).get('result', [])
                print(f"Klines for {sym}: {len(klines)} records")
        except Exception as e:
            print(f"Error fetching klines for {sym}: {e}")

if __name__ == "__main__":
    test_delta_options()
