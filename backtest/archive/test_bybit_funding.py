import json
import urllib.request

def test_bybit_funding(symbol):
    url = f"https://api.bybit.com/v5/market/funding/history?category=linear&symbol={symbol}&limit=200"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            list_data = data.get('result', {}).get('list', [])
            print(f"Bybit {symbol}: Fetched {len(list_data)} records. Sample:", list_data[0] if list_data else "empty")
            return list_data
    except Exception as e:
        print(f"Error fetching Bybit funding for {symbol}: {e}")
        return []

if __name__ == "__main__":
    for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT", "1000PEPEUSDT"]:
        test_bybit_funding(sym)
