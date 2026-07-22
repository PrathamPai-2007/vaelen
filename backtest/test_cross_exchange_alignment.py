import json
import urllib.request
import time
import numpy as np

def fetch_binance_funding(symbol, limit=1000):
    url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit={limit}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode('utf-8'))

def fetch_bybit_funding(symbol, limit=200):
    all_records = []
    end_time = None
    for _ in range(5):
        url = f"https://api.bybit.com/v5/market/funding/history?category=linear&symbol={symbol}&limit=200"
        if end_time:
            url += f"&endTime={end_time}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            list_data = data.get('result', {}).get('list', [])
            if not list_data:
                break
            all_records.extend(list_data)
            last_ts = int(list_data[-1]['fundingRateTimestamp'])
            end_time = last_ts - 1
            time.sleep(0.1)
    return all_records

def test_alignment(symbol):
    b_data = fetch_binance_funding(symbol, limit=500)
    by_data = fetch_bybit_funding(symbol, limit=200)

    # Build map by timestamp (normalized to 8h boundary ms)
    b_map = {}
    for d in b_data:
        ts = int(d['fundingTime']) // 1000  # sec
        rate = float(d['fundingRate'])
        b_map[ts] = rate

    by_map = {}
    for d in by_data:
        ts = int(d['fundingRateTimestamp']) // 1000
        rate = float(d['fundingRate'])
        by_map[ts] = rate

    common_ts = sorted(list(set(b_map.keys()).intersection(set(by_map.keys()))))
    print(f"Symbol: {symbol} | Binance Count: {len(b_data)} | Bybit Count: {len(by_data)} | Common Timestamps: {len(common_ts)}")
    if common_ts:
        b_rates = np.array([b_map[t] for t in common_ts]) * 10000.0
        by_rates = np.array([by_map[t] for t in common_ts]) * 10000.0
        spread_bps = np.abs(b_rates - by_rates)
        ann_spread = spread_bps * 3 * 365.0
        print(f"  Mean Abs Spread 8h : {np.mean(spread_bps):.4f} bps | Mean Ann Spread: {np.mean(ann_spread):.2f} bps")
        print(f"  Max Abs Spread 8h  : {np.max(spread_bps):.4f} bps | Max Ann Spread : {np.max(ann_spread):.2f} bps")
        print(f"  Spread > 10 bps    : {np.mean(spread_bps > 10.0)*100:.2f}% | Spread > 20 bps: {np.mean(spread_bps > 20.0)*100:.2f}%")

if __name__ == "__main__":
    for sym in ["BTCUSDT", "ETHUSDT", "DOGEUSDT", "XRPUSDT", "1000PEPEUSDT", "WIFUSDT"]:
        test_alignment(sym)
