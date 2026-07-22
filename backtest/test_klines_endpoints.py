import json
import urllib.request

def test_endpoints():
    endpoints = [
        "https://api.india.delta.exchange/v2/history/klines?symbol=AAPLXUSD&resolution=1d",
        "https://api.india.delta.exchange/v2/klines?symbol=AAPLXUSD&resolution=1d",
        "https://api.india.delta.exchange/v2/chart/history?symbol=AAPLXUSD&resolution=1D",
        "https://api.india.delta.exchange/v2/history/candles?symbol=AAPLXUSD&resolution=1d",
        "https://api.india.delta.exchange/v2/sparklines?symbols=AAPLXUSD"
    ]
    for url in endpoints:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                print(f"URL: {url}")
                print("  Response keys:", list(data.keys()) if isinstance(data, dict) else type(data))
                if isinstance(data, dict) and 'result' in data:
                    res = data['result']
                    print("  Result count:", len(res) if isinstance(res, list) else 'dict')
                    if isinstance(res, list) and res:
                        print("  Sample item:", res[0])
        except Exception as e:
            print(f"URL: {url} FAILED: {e}")

if __name__ == "__main__":
    test_endpoints()
