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

def investigate_rwa_corrected():
    print("=" * 120)
    print("CORRECTED DELTA EXCHANGE US STOCK & RWA TOKENS AUDIT")
    print("Root Cause Analysis: Previous audit queried non-existent USDT suffixes. Correct live symbols use USD/BUSD.")
    print("=" * 120)

    # 1. Unfiltered Products Query
    products_data = fetch_json("https://api.delta.exchange/v2/products?page_size=1000")
    if not products_data:
        print("Failed to fetch products.")
        return

    products = products_data.get('result', [])
    print(f"Total Products in Delta Database: {len(products)}")

    # Search for all stock/RWA/commodity token perpetuals
    # Symbols like AAPLXUSD, TSLAXUSD, NVDAXUSD, QQQXUSD, SNDKBUSD, SLVONUSD, CBRSBUSD, SPCXXUSD, NBISBUSD, SOXLBUSD, XAUTUSD, PAXGUSD, etc.
    rwa_symbols = []
    for p in products:
        sym = p.get('symbol', '')
        ct = p.get('contract_type', '')
        # Filter for perpetual futures that are stock / index / RWA / gold tokens
        if ct == 'perpetual_futures':
            # Check if symbol ends with XUSD, BUSD, USD, or is a known RWA
            if any(sym.startswith(prefix) for prefix in ['AAPLX', 'TSLAX', 'NVDAX', 'METAX', 'AMZNX', 'GOOGLX', 'QQQX', 'SNDK', 'SLV', 'CBRS', 'SPCX', 'NBIS', 'SOXL', 'XAUT', 'PAXG', 'COIN', 'MSTR']):
                rwa_symbols.append(p)
                
    print(f"\n--- 1. Live RWA & Stock Token Products Found ({len(rwa_symbols)} active contracts) ---")
    hdr1 = f"{'Symbol':>14} | {'Contract Type':>18} | {'Contract Value':>14} | {'Tick Size':>10} | {'Underlying Asset':>16}"
    print(hdr1)
    print("-" * len(hdr1))

    for p in rwa_symbols:
        sym = p.get('symbol')
        ct = p.get('contract_type')
        c_val = p.get('contract_value')
        tick_sz = p.get('tick_size')
        underlying = p.get('underlying_asset', {}).get('symbol') if isinstance(p.get('underlying_asset'), dict) else p.get('underlying_asset')
        print(f"{sym:>14} | {ct:>18} | {str(c_val):>14} | {str(tick_sz):>10} | {str(underlying):>16}")

    # 2. Live Tickers Audit (Correct Symbols)
    tickers_data = fetch_json("https://api.delta.exchange/v2/tickers?page_size=1000")
    tickers_map = {t['symbol']: t for t in tickers_data.get('result', [])} if tickers_data else {}

    print("\n--- 2. Live Order Book Liquidity, Spreads, OI, & Funding Rates ---")
    hdr2 = f"{'Symbol':>14} | {'Mark Price':>11} | {'Best Bid':>9} | {'Best Ask':>9} | {'Spread (bps)':>13} | {'24h Vol ($)':>14} | {'Open Int ($)':>13} | {'Ann. Funding':>13}"
    print(hdr2)
    print("-" * len(hdr2))

    for p in rwa_symbols:
        sym = p.get('symbol')
        t = tickers_map.get(sym, {})
        
        mark = float(t.get('mark_price') or 0)
        quotes = t.get('quotes', {})
        bid = float(quotes.get('best_bid') or 0)
        ask = float(quotes.get('best_ask') or 0)
        vol_usd = float(t.get('turnover_usd') or 0)
        oi_usd = float(t.get('oi_value_usd') or 0)
        
        # Funding rate (annualized % or 8h/4h)
        ann_funding = float(t.get('mark_vol') or 0) # check annualized funding or mark_iv
        funding_rate = float(p.get('annualized_funding') or 0) if p.get('annualized_funding') is not None else 0.0
        
        if mark > 0 and bid > 0 and ask > 0:
            spread_bps = (ask - bid) / mark * 10000.0
        else:
            spread_bps = 0.0

        print(f"{sym:>14} | ${mark:10.2f} | ${bid:8.2f} | ${ask:8.2f} | {spread_bps:12.2f} bps | ${vol_usd:13.2f} | ${oi_usd:12.2f} | {funding_rate:12.2f}%")

    # 3. Historical Klines Retrieval for Correct Symbols
    print("\n--- 3. Historical Kline Retrieval (/v2/klines for USD symbols) ---")
    for p in rwa_symbols[:8]:
        sym = p.get('symbol')
        kline_data = fetch_json(f"https://api.delta.exchange/v2/klines?symbol={sym}&resolution=1d")
        if kline_data and 'result' in kline_data:
            klines = kline_data['result']
            print(f"  {sym:<14}: {len(klines)} daily klines available")
            if klines:
                first_ts = int(klines[-1]['time'])
                last_ts = int(klines[0]['time'])
                first_dt = datetime.datetime.fromtimestamp(first_ts, tz=datetime.timezone.utc).strftime('%Y-%m-%d')
                last_dt = datetime.datetime.fromtimestamp(last_ts, tz=datetime.timezone.utc).strftime('%Y-%m-%d')
                print(f"    Date Range: {first_dt} to {last_dt} | Latest Close: ${klines[0]['close']} | Latest Vol: {klines[0]['volume']}")

if __name__ == "__main__":
    investigate_rwa_corrected()
