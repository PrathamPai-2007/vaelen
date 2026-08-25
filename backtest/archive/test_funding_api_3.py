import json
import urllib.request

endpoints = [
    "https://api.india.delta.exchange/v2/history/candles?resolution=8h&symbol=BTCUSD",
    "https://api.india.delta.exchange/v2/chart/history?symbol=BTCUSD&resolution=8h",
    "https://api.india.delta.exchange/v2/stats/funding_rates?symbol=BTCUSD",
    "https://api.india.delta.exchange/v2/funding_rate?symbol=BTCUSD",
    "https://api.india.delta.exchange/v2/products/BTCUSD/funding_rate",
]

for ep in endpoints:
    try:
        req = urllib.request.Request(ep, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            print(f"EP: {ep} -> Status 200, Keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
            if isinstance(data, dict) and 'result' in data:
                print("  Result type/sample:", type(data['result']), data['result'][:1] if isinstance(data['result'], list) else data['result'])
    except Exception as e:
        print(f"EP: {ep} -> Error: {e}")
