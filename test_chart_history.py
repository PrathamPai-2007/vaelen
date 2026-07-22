import json
import urllib.request
import time

def test_chart_history():
    now = int(time.time())
    start = now - 30 * 86400 # 30 days ago
    
    url = f"https://api.india.delta.exchange/v2/chart/history?symbol=AAPLXUSD&resolution=60&start={start}&end={now}"
    url2 = f"https://api.india.delta.exchange/v2/chart/history?symbol=AAPLXUSD&resolution=1d&start={start}&end={now}"
    url3 = f"https://api.india.delta.exchange/v2/chart/history?symbol=AAPLXUSD&resolution=1D&from={start}&to={now}"
    
    for u in [url, url2, url3]:
        try:
            req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                print(f"SUCCESS {u}:")
                print("  Keys:", list(data.keys()) if isinstance(data, dict) else len(data))
                if isinstance(data, dict) and 'result' in data:
                    print("  Result len:", len(data['result']))
                    if data['result']:
                        print("  Sample:", data['result'][0])
                elif isinstance(data, dict):
                    print("  Sample data:", data)
        except Exception as e:
            print(f"FAILED {u}: {e}")

if __name__ == "__main__":
    test_chart_history()
