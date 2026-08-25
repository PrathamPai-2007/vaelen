import json
import urllib.request

def fetch_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode('utf-8'))

def compare_wif():
    print("==========================================================================================")
    print("RAW API INVESTIGATION: WIFUSD VENUE SIGN DIVERGENCE (DELTA vs BINANCE)")
    print("==========================================================================================")

    # 1. Delta Product Specs
    delta_prod = fetch_json("https://api.india.delta.exchange/v2/products/WIFUSD")['result']
    delta_ticker = fetch_json("https://api.india.delta.exchange/v2/tickers?symbol=WIFUSD")['result'][0]
    
    print("\n--- DELTA EXCHANGE WIFUSD SPECS & TICKER ---")
    print(f"Symbol            : {delta_prod.get('symbol')}")
    print(f"Underlying Asset  : {delta_prod.get('underlying_asset')}")
    print(f"Quoting Asset     : {delta_prod.get('quoting_asset')}")
    print(f"Settling Asset    : {delta_prod.get('settling_asset')}")
    print(f"Contract Type     : {delta_prod.get('contract_type')}")
    print(f"Contract Value    : {delta_prod.get('contract_value')}")
    print(f"Spot Index        : {delta_prod.get('spot_index')}")
    print(f"Annualized Funding: {delta_prod.get('annualized_funding')}")
    print(f"Ticker Raw Funding: {delta_ticker.get('funding_rate')}")
    print(f"Ticker Mark Price : {delta_ticker.get('mark_price')}")
    print(f"Ticker Spot Price : {delta_ticker.get('spot_price')}")
    print(f"Ticker Best Bid/Ask: {delta_ticker.get('quotes', {}).get('best_bid')} / {delta_ticker.get('quotes', {}).get('best_ask')}")

    # 2. Binance WIFUSDT Specs
    binance_prem = fetch_json("https://fapi.binance.com/fapi/v1/premiumIndex?symbol=WIFUSDT")
    print("\n--- BINANCE FUTURES WIFUSDT TICKER ---")
    print(f"Symbol            : {binance_prem.get('symbol')}")
    print(f"Last Funding Rate : {binance_prem.get('lastFundingRate')}")
    print(f"Mark Price        : {binance_prem.get('markPrice')}")
    print(f"Index Price       : {binance_prem.get('indexPrice')}")

    if 'mark_price' not in delta_ticker or 'spot_price' not in delta_ticker:
        raise KeyError(f"Delta missing mark_price or spot_price. Keys: {delta_ticker.keys()}")
    d_mark = float(delta_ticker['mark_price'])
    d_spot = float(delta_ticker['spot_price'])
    d_basis_bps = ((d_mark - d_spot) / d_spot) * 10000.0 if d_spot > 0 else 0.0

    if 'markPrice' not in binance_prem or 'indexPrice' not in binance_prem:
        raise KeyError(f"Binance missing markPrice or indexPrice. Keys: {binance_prem.keys()}")
    b_mark = float(binance_prem['markPrice'])
    b_spot = float(binance_prem['indexPrice'])
    b_basis_bps = ((b_mark - b_spot) / b_spot) * 10000.0 if b_spot > 0 else 0.0

    print(f"\n--- BASIS (MARK - SPOT) ANALYSIS ---")
    print(f"Delta WIFUSD Basis    : {d_basis_bps:+.2f} bps (Mark={d_mark}, Spot={d_spot})")
    print(f"Binance WIFUSDT Basis : {b_basis_bps:+.2f} bps (Mark={b_mark}, Spot={b_spot})")

if __name__ == "__main__":
    compare_wif()
