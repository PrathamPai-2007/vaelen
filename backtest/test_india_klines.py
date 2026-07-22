import json
import urllib.request

def test_klines_params():
    resolutions = ['60', '1d', '1D', 'D', '1m', '5m', '15m']
    symbols = ['AAPLXUSD', 'NVDAXUSD', 'TSLAXUSD', 'XAUTUSD', 'BTCUSD']
    
    for sym in symbols:
        for res in resolutions:
            url = f"https://api.india.delta.exchange/v2/klines?symbol={sym}&resolution={res}"
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                    result = data.get('result', [])
                    print(f"SUCCESS: {sym} resolution={res} returned {len(result)} candles")
                    if result:
                        print("  Sample:", result[0])
                    break
            except Exception as e:
                pass

if __name__ == "__main__":
    test_klines_params()
