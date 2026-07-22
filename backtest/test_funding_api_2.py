import json
import urllib.request
import time

def test_delta_endpoints():
    endpoints = [
        "https://api.india.delta.exchange/v2/history/funding_rate?symbol=BTCUSD",
        "https://api.india.delta.exchange/v2/history/funding_rates?symbol=BTCUSD",
        "https://api.india.delta.exchange/v2/funding_rates?symbol=BTCUSD",
        "https://api.india.delta.exchange/v2/stats/funding?symbol=BTCUSD",
        "https://api.india.delta.exchange/v2/tickers?symbol=BTCUSD",
        "https://api.india.delta.exchange/v2/sparklines?symbol=BTCUSD",
    ]
    for ep in endpoints:
        try:
            req = urllib.request.Request(ep, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                print(f"EP: {ep} -> Status 200")
                if isinstance(data, dict):
                    print("  Keys:", list(data.keys()))
                    if 'result' in data and isinstance(data['result'], list) and len(data['result']) > 0:
                        print("  Result sample[0]:", data['result'][0])
        except Exception as e:
            print(f"EP: {ep} -> Error: {e}")

def fetch_binance_funding_history(symbol, limit=1000):
    url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit={limit}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode('utf-8'))
        return data

if __name__ == "__main__":
    print("Testing Delta endpoints...")
    test_delta_endpoints()
    
    print("\nTesting Binance 1000 funding history...")
    data = fetch_binance_funding_history("BTCUSDT", limit=1000)
    print(f"Fetched {len(data)} funding records for BTCUSDT")
    if data:
        print("Oldest record:", data[0])
        print("Newest record:", data[-1])
