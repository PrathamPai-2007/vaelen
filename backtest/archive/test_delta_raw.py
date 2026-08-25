import json
import urllib.request

url = "https://api.india.delta.exchange/v2/tickers"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req, timeout=10) as resp:
    data = json.loads(resp.read().decode('utf-8'))
    results = data.get('result', [])
    for item in results:
        sym = item.get('symbol')
        if sym in ["1000PEPEUSD", "WIFUSD", "DOGEUSD", "XRPUSD", "BTCUSD", "ETHUSD"]:
            print(f"Symbol: {sym} | raw funding_rate: {item.get('funding_rate')} | mark_price: {item.get('mark_price')} | spot_price: {item.get('spot_price')}")
