import json
import urllib.request
import urllib.error

def fetch_delta_products():
    url = "https://api.delta.exchange/v2/products"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            products = data.get('result', [])
            futures = [p for p in products if p.get('contract_type') == 'futures']
            print(f"Delta: Found {len(futures)} dated futures contracts.")
            if futures:
                for f in futures[:10]:
                    print("  ", f['symbol'], f['contract_type'], f.get('settlement_time'))
            return futures
    except Exception as e:
        print(f"Error fetching Delta products: {e}")
        return []

def fetch_binance_usdm_delivery():
    url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            symbols = data.get('symbols', [])
            futures = [s for s in symbols if s.get('contractType') != 'PERPETUAL']
            print(f"Binance USD-M Delivery: Found {len(futures)} dated futures.")
            if futures:
                for f in futures[:10]:
                    print("  ", f['symbol'], f['contractType'], f.get('deliveryDate'))
    except Exception as e:
        print(f"Error fetching Binance USD-M Delivery: {e}")

if __name__ == "__main__":
    fetch_delta_products()
    fetch_binance_usdm_delivery()
