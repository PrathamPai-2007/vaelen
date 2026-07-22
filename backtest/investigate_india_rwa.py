import json
import urllib.request
import datetime

def fetch_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

def investigate_india_rwa():
    print("=" * 120)
    print("CORRECTED DELTA EXCHANGE (INDIA REGION) RWA STOCK & COMMODITY TOKENS AUDIT")
    print("Host: api.india.delta.exchange | Correct Suffixes: USD / BUSD")
    print("=" * 120)

    # 1. Fetch all products from India API
    products_data = fetch_json("https://api.india.delta.exchange/v2/products?page_size=1000")
    if not products_data:
        print("Failed to fetch products from India API.")
        return

    products = products_data.get('result', [])
    print(f"Total Products in Delta India Database: {len(products)}")

    # Filter for perpetual futures
    perps = [p for p in products if p.get('contract_type') == 'perpetual_futures']
    print(f"Total Perpetual Futures: {len(perps)}")

    # Filter for RWA / Stock / Commodity / Leveraged ETF tokens
    rwa_list = []
    for p in perps:
        sym = p.get('symbol', '')
        # Check for known stock/RWA tokens
        if any(sym.startswith(prefix) for prefix in [
            'AAPLX', 'TSLAX', 'NVDAX', 'METAX', 'AMZNX', 'GOOGLX', 'QQQX', 
            'SNDK', 'SLV', 'CBRS', 'SPCX', 'NBIS', 'SOXL', 'XAUT', 'PAXG', 
            'COIN', 'MSTR', 'NFLX', 'MSFT', 'AMD'
        ]) or sym.endswith('XUSD') or sym.endswith('BUSD'):
            rwa_list.append(p)

    print(f"\n--- 1. Live RWA & Stock Token Products Found ({len(rwa_list)} active contracts) ---")
    hdr1 = f"{'Symbol':>16} | {'Contract Type':>18} | {'Contract Value':>14} | {'Tick Size':>10} | {'Underlying Asset':>16}"
    print(hdr1)
    print("-" * len(hdr1))

    for p in rwa_list:
        sym = p.get('symbol')
        ct = p.get('contract_type')
        c_val = p.get('contract_value')
        tick_sz = p.get('tick_size')
        underlying = p.get('underlying_asset', {}).get('symbol') if isinstance(p.get('underlying_asset'), dict) else p.get('underlying_asset')
        print(f"{sym:>16} | {ct:>18} | {str(c_val):>14} | {str(tick_sz):>10} | {str(underlying):>16}")

    # 2. Fetch Tickers from India API
    tickers_data = fetch_json("https://api.india.delta.exchange/v2/tickers?page_size=1000")
    tickers_map = {t['symbol']: t for t in tickers_data.get('result', [])} if tickers_data else {}

    print("\n--- 2. Live Liquidity, Order Book Spreads, Volume, OI, & Funding Rates ---")
    hdr2 = f"{'Symbol':>16} | {'Mark Price':>11} | {'Best Bid':>9} | {'Best Ask':>9} | {'Spread (bps)':>13} | {'24h Vol ($)':>14} | {'Open Int ($)':>13} | {'Ann. Funding':>13}"
    print(hdr2)
    print("-" * len(hdr2))

    for p in rwa_list:
        sym = p.get('symbol')
        t = tickers_map.get(sym, {})
        
        mark = float(t.get('mark_price') or 0)
        quotes = t.get('quotes', {})
        bid = float(quotes.get('best_bid') or 0)
        ask = float(quotes.get('best_ask') or 0)
        vol_usd = float(t.get('turnover_usd') or 0)
        oi_usd = float(t.get('oi_value_usd') or 0)
        
        funding_rate = float(p.get('annualized_funding') or 0) if p.get('annualized_funding') is not None else 0.0
        
        if mark > 0 and bid > 0 and ask > 0:
            spread_bps = (ask - bid) / mark * 10000.0
        else:
            spread_bps = 0.0

        print(f"{sym:>16} | ${mark:10.2f} | ${bid:8.2f} | ${ask:8.2f} | {spread_bps:12.2f} bps | ${vol_usd:13.2f} | ${oi_usd:12.2f} | {funding_rate:12.2f}%")

    # 3. Historical Klines Retrieval from India API
    print("\n--- 3. Historical Kline Retrieval (/v2/klines from India API) ---")
    for p in rwa_list[:8]:
        sym = p.get('symbol')
        kline_data = fetch_json(f"https://api.india.delta.exchange/v2/klines?symbol={sym}&resolution=1d")
        if kline_data and 'result' in kline_data:
            klines = kline_data['result']
            print(f"  {sym:<16}: {len(klines)} daily klines available")
            if klines:
                first_ts = int(klines[-1]['time'])
                last_ts = int(klines[0]['time'])
                first_dt = datetime.datetime.fromtimestamp(first_ts, tz=datetime.timezone.utc).strftime('%Y-%m-%d')
                last_dt = datetime.datetime.fromtimestamp(last_ts, tz=datetime.timezone.utc).strftime('%Y-%m-%d')
                print(f"    Date Range: {first_dt} to {last_dt} | Latest Close: ${klines[0]['close']} | Latest Vol: {klines[0]['volume']}")

if __name__ == "__main__":
    investigate_india_rwa()
