import json
import urllib.request

def fetch_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode('utf-8'))

def investigate():
    print("==========================================================================================")
    print("INVESTIGATING WIF PRICE & FUNDING DISCREPANCY ACROSS SOURCES")
    print("==========================================================================================")

    # 1. Delta Product Specs & Ticker
    delta_prod = fetch_json("https://api.india.delta.exchange/v2/products/WIFUSD").get('result', {})
    delta_tickers = fetch_json("https://api.india.delta.exchange/v2/tickers").get('result', [])
    delta_wif = next((t for t in delta_tickers if t.get('symbol') == 'WIFUSD'), {})

    # 2. Binance Spot & Futures
    b_spot = fetch_json("https://api.binance.com/api/v3/ticker/price?symbol=WIFUSDT")
    b_fut = fetch_json("https://fapi.binance.com/fapi/v1/premiumIndex?symbol=WIFUSDT")

    # 3. CoinGecko WIF Price
    cg_price = {}
    try:
        cg_price = fetch_json("https://api.coingecko.com/api/v3/simple/price?ids=dogwifhat&vs_currencies=usd")
    except Exception as e:
        print(f"CoinGecko API call error: {e}")

    print("\n--- 1. DELTA EXCHANGE WIFUSD SPECS & TICKER ---")
    print(f"Product ID         : {delta_prod.get('id')}")
    print(f"Symbol             : {delta_prod.get('symbol')}")
    print(f"Description        : {delta_prod.get('description')}")
    print(f"Contract Type      : {delta_prod.get('contract_type')}")
    print(f"Contract Value     : {delta_prod.get('contract_value')}")
    print(f"Contract Unit Curr : {delta_prod.get('contract_unit_currency')}")
    print(f"Quoting Asset      : {delta_prod.get('quoting_asset', {}).get('symbol')}")
    print(f"Settling Asset     : {delta_prod.get('settling_asset', {}).get('symbol')}")
    print(f"Underlying Asset   : {delta_prod.get('underlying_asset', {}).get('symbol')}")
    print(f"Spot Index Symbol  : {delta_prod.get('spot_index', {}).get('symbol')}")
    print(f"Annualized Funding : {delta_prod.get('annualized_funding')}")
    print(f"Ticker raw funding : {delta_wif.get('funding_rate')}")
    print(f"Ticker Mark Price  : {delta_wif.get('mark_price')}")
    print(f"Ticker Spot Price  : {delta_wif.get('spot_price')}")

    print("\n--- 2. BINANCE WIFUSDT SPOT & FUTURES ---")
    print(f"Binance Spot Price : {b_spot.get('price')}")
    print(f"Binance Fut Mark   : {b_fut.get('markPrice')}")
    print(f"Binance Fut Index  : {b_fut.get('indexPrice')}")
    print(f"Binance Fut Funding: {b_fut.get('lastFundingRate')}")

    print("\n--- 3. COINGECKO SPOT BENCHMARK ---")
    print(f"CoinGecko dogwifhat: ${cg_price.get('dogwifhat', {}).get('usd')} USD")

    # Check all Delta tickers to see if there are other WIF contracts or if WIF is scaled
    print("\n--- 4. SEARCHING ALL DELTA TICKERS FOR 'WIF' ---")
    for t in delta_tickers:
        if 'WIF' in t.get('symbol', ''):
            print(f"Symbol: {t.get('symbol'):<15} | Mark: {t.get('mark_price'):<12} | Spot: {t.get('spot_price'):<12} | Funding: {t.get('funding_rate')}")

if __name__ == "__main__":
    investigate()
