import json
import urllib.request
import time

def fetch_delta_tickers():
    url = "https://api.india.delta.exchange/v2/tickers"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode('utf-8'))
        results = data.get('result', [])
        ticker_map = {}
        for item in results:
            sym = item.get('symbol')
            if sym:
                ticker_map[sym] = item
        return ticker_map

def fetch_binance_premium_index():
    url = "https://fapi.binance.com/fapi/v1/premiumIndex"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode('utf-8'))
        return {item['symbol']: item for item in data}

def compare_current_funding():
    delta_tickers = fetch_delta_tickers()
    binance_tickers = fetch_binance_premium_index()

    pairs = [
        ("1000PEPEUSD", "1000PEPEUSDT"),
        ("WIFUSD", "WIFUSDT"),
        ("DOGEUSD", "DOGEUSDT"),
        ("XRPUSD", "XRPUSDT"),
        ("BTCUSD", "BTCUSDT"),
        ("ETHUSD", "ETHUSDT"),
    ]

    print("=" * 100)
    print("VENUE FUNDING COMPARISON: DELTA EXCHANGE vs BINANCE FUTURES (LIVE CURRENT SNAPSHOT)")
    print("=" * 100)
    header = f"{'Symbol (Delta)':>15} | {'Delta Ann. Rate':>17} | {'Binance Ann. Rate':>19} | {'Diff (bps)':>12} | {'Alignment':>10}"
    print(header)
    print("-" * len(header))

    for delta_sym, binance_sym in pairs:
        d_info = delta_tickers.get(delta_sym, {})
        b_info = binance_tickers.get(binance_sym, {})

        # Delta funding_rate in JSON is 8h rate string
        d_rate_str = d_info.get('funding_rate', '0.0')
        d_rate_8h = float(d_rate_str) if d_rate_str else 0.0
        d_ann_bps = d_rate_8h * 3 * 365 * 10000.0

        b_rate_str = b_info.get('lastFundingRate', '0.0')
        b_rate_8h = float(b_rate_str) if b_rate_str else 0.0
        b_ann_bps = b_rate_8h * 3 * 365 * 10000.0

        diff_ann_bps = d_ann_bps - b_ann_bps
        same_sign = (d_ann_bps * b_ann_bps >= 0)

        print(f"{delta_sym:>15} | {d_ann_bps:+16.2f} bps | {b_ann_bps:+18.2f} bps | {diff_ann_bps:+11.2f} bps | {'SAME' if same_sign else 'DIFFERENT':>10}")

if __name__ == "__main__":
    compare_current_funding()
