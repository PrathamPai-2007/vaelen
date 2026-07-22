import json
import urllib.request
import time
import datetime

def fetch_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

def investigate_rwa():
    print("=" * 120)
    print("DELTA EXCHANGE US STOCK RWA TOKENS MECHANICS & SCOPING AUDIT")
    print("=" * 120)

    # 1. Product Mechanism Query
    products_data = fetch_json("https://api.delta.exchange/v2/products?page_size=1000")
    if not products_data:
        print("Failed to fetch products.")
        return

    products = products_data.get('result', [])
    rwa_products = [p for p in products if 'USDT' in p.get('symbol', '') and any(stock in p.get('symbol', '') for stock in ['AAPL', 'TSLA', 'NVDA', 'AMZN', 'META', 'COIN', 'MSTR', 'GOOGL', 'SNDK', 'MSFT', 'NFLX'])]
    
    # Also find all perpetuals ending with XUSDT
    rwa_xusdt = [p for p in products if p.get('symbol', '').endswith('XUSDT')]
    
    print(f"\n--- 1. Identified RWA Stock Token Products ({len(rwa_xusdt)} found) ---")
    for p in rwa_xusdt:
        sym = p.get('symbol')
        ct = p.get('contract_type')
        tick_sz = p.get('tick_size')
        c_val = p.get('contract_value')
        underlying = p.get('underlying_asset', {})
        settle_asset = p.get('settling_asset', {})
        print(f"  Symbol: {sym:<12} | Type: {ct:<18} | Contract Val: {str(c_val):<8} | Tick Size: {str(tick_sz):<8} | Underlying: {underlying.get('symbol')}")

    # 2. Tickers & Order Book Spread Audit
    tickers_data = fetch_json("https://api.delta.exchange/v2/tickers?page_size=1000")
    tickers = tickers_data.get('result', []) if tickers_data else []
    
    print("\n--- 2. Live Order Book & Spread Depth Audit ---")
    hdr2 = f"{'Symbol':>12} | {'Mark Price':>12} | {'Best Bid':>10} | {'Best Ask':>10} | {'Spread (bps)':>14} | {'Bid Size':>10} | {'Ask Size':>10} | {'24h Volume ($)':>15}"
    print(hdr2)
    print("-" * len(hdr2))
    
    for t in tickers:
        sym = t.get('symbol', '')
        if sym.endswith('XUSDT'):
            mark = float(t.get('mark_price') or 0)
            quotes = t.get('quotes', {})
            bid = float(quotes.get('best_bid') or 0)
            ask = float(quotes.get('best_ask') or 0)
            bid_sz = quotes.get('bid_size', '0')
            ask_sz = quotes.get('ask_size', '0')
            volume_usd = float(t.get('turnover_usd') or 0)
            
            if mark > 0 and bid > 0 and ask > 0:
                spread_bps = (ask - bid) / mark * 10000.0
            else:
                spread_bps = 0.0
                
            print(f"{sym:>12} | ${mark:11.2f} | ${bid:9.2f} | ${ask:9.2f} | {spread_bps:13.2f} bps | {str(bid_sz):>10} | {str(ask_sz):>10} | ${volume_usd:14.2f}")

    # 3. Orderbook L2 Depth for Top Symbols
    print("\n--- 3. L2 Orderbook Depth (AAPL, TSLA, NVDA) ---")
    for target_sym in ['AAPLXUSDT', 'TSLAXUSDT', 'NVDAXUSDT', 'METAXUSDT']:
        l2_data = fetch_json(f"https://api.delta.exchange/v2/l2orderbook/{target_sym}")
        if l2_data and 'result' in l2_data:
            bids = l2_data['result'].get('bids', [])
            asks = l2_data['result'].get('asks', [])
            print(f"\nOrderbook L2 for {target_sym}:")
            print("  Top Bids:", bids[:3])
            print("  Top Asks:", asks[:3])

    # 4. Historical Data Depth & Weekend Gap Check
    print("\n--- 4. Historical Data Depth & Weekend/Gap Inspection ---")
    for target_sym in ['AAPLXUSDT', 'TSLAXUSDT', 'NVDAXUSDT']:
        kline_data = fetch_json(f"https://api.delta.exchange/v2/klines?symbol={target_sym}&resolution=1d")
        if kline_data and 'result' in kline_data:
            klines = kline_data['result']
            print(f"\n{target_sym} Klines: Total {len(klines)} daily candles available.")
            if klines:
                # Convert timestamps
                first_ts = int(klines[-1]['time'])
                last_ts = int(klines[0]['time'])
                first_dt = datetime.datetime.fromtimestamp(first_ts, tz=datetime.timezone.utc).strftime('%Y-%m-%d')
                last_dt = datetime.datetime.fromtimestamp(last_ts, tz=datetime.timezone.utc).strftime('%Y-%m-%d')
                print(f"  Date Range: {first_dt} to {last_dt}")
                
                # Inspect recent 10 candles for weekend gap behavior
                print("  Recent 5 daily candles:")
                for k in klines[:5]:
                    dt_str = datetime.datetime.fromtimestamp(int(k['time']), tz=datetime.timezone.utc).strftime('%Y-%m-%d (%a)')
                    print(f"    {dt_str}: Open=${k['open']} High=${k['high']} Low=${k['low']} Close=${k['close']} Vol={k['volume']}")

if __name__ == "__main__":
    investigate_rwa()
