import json
import urllib.request

def test_delta_funding(symbol):
    print(f"\n--- Testing Delta Exchange API for {symbol} ---")
    urls = [
        f"https://api.india.delta.exchange/v2/products/{symbol}",
        f"https://api.india.delta.exchange/v2/funding_rate/history?symbol={symbol}",
        f"https://api.india.delta.exchange/v2/stats/funding_rate?symbol={symbol}",
        f"https://api.delta.exchange/v2/history/funding_rates?symbol={symbol}",
    ]
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                print(f"URL: {url} -> Status 200, Result keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
                if isinstance(data, dict) and 'result' in data:
                    res = data['result']
                    if isinstance(res, list) and len(res) > 0:
                        print(f"  Sample item: {res[0]}")
                    elif isinstance(res, dict):
                        print(f"  Result sample: {list(res.keys())}")
        except Exception as e:
            print(f"URL: {url} -> Error: {e}")

def test_binance_funding(symbol):
    print(f"\n--- Testing Binance Futures API for {symbol} ---")
    url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit=100"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            print(f"URL: {url} -> Count: {len(data)}, Sample: {data[0] if len(data) > 0 else 'empty'}")
    except Exception as e:
        print(f"URL: {url} -> Error: {e}")

if __name__ == "__main__":
    for sym in ["1000PEPEUSD", "WIFUSD", "DOGEUSD", "XRPUSD", "BTCUSD", "ETHUSD"]:
        test_delta_funding(sym)
    
    for sym in ["1000PEPEUSDT", "WIFUSDT", "DOGEUSDT", "XRPUSDT", "BTCUSDT", "ETHUSDT"]:
        test_binance_funding(sym)
