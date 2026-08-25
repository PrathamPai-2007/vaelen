import json
import urllib.request

def check_endpoint(url):
    print(f"\n--- Checking {url} ---")
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            res = data.get('result', [])
            if isinstance(res, list):
                for p in res:
                    sym = p.get('symbol')
                    if sym in ['XAUTUSD', 'PAXGUSD']:
                        print(f"{sym}: {p}")
            elif isinstance(res, dict):
                print(res)
    except Exception as e:
        print(f"Error: {e}")

check_endpoint("https://api.india.delta.exchange/v2/products?page_size=1000")
check_endpoint("https://api.india.delta.exchange/v2/stats?page_size=1000")
