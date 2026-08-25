import json
import urllib.request

for sym in ["1000PEPEUSD", "WIFUSD", "DOGEUSD", "XRPUSD", "BTCUSD", "ETHUSD"]:
    url = f"https://api.india.delta.exchange/v2/products/{sym}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            res = data.get('result', {})
            print(f"Product {sym} | annualized_funding: {res.get('annualized_funding')}")
    except Exception as e:
        print(f"Error {sym}: {e}")
