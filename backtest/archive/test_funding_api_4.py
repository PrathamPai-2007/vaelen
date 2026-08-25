import json
import urllib.request
import time

resolutions = ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "1d"]
now = int(time.time())
start = now - 30 * 86400

for res in resolutions:
    url = f"https://api.india.delta.exchange/v2/history/candles?resolution={res}&symbol=BTCUSD&start={start}&end={now}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if isinstance(data, dict) and 'result' in data and data['result']:
                print(f"Res {res} SUCCESS! Count: {len(data['result'])}, Sample keys: {list(data['result'][0].keys()) if isinstance(data['result'][0], dict) else 'not dict'}")
                print("Sample:", data['result'][0])
                break
            else:
                print(f"Res {res}: Success but empty result")
    except Exception as e:
        print(f"Res {res}: Error: {e}")
