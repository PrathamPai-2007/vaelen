import json
import urllib.request

def dump_all():
    url = "https://api.delta.exchange/v2/products?page_size=1000"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode('utf-8'))
        products = data.get('result', [])
        
    print(f"Total products: {len(products)}")
    perps = [p for p in products if p.get('contract_type') == 'perpetual_futures']
    print(f"Total perpetual_futures: {len(perps)}")
    
    print("\nAll perpetual futures symbols:")
    for p in perps:
        print("  ", p.get('symbol'), "| Underlying:", p.get('underlying_asset', {}).get('symbol') if isinstance(p.get('underlying_asset'), dict) else p.get('underlying_asset'))

    # Check India API host or global testnet host if different
    url_india = "https://api.india.delta.exchange/v2/products?page_size=1000"
    try:
        req = urllib.request.Request(url_india, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data_in = json.loads(resp.read().decode('utf-8'))
            products_in = data_in.get('result', [])
            perps_in = [p for p in products_in if p.get('contract_type') == 'perpetual_futures']
            print(f"\nIndia API total perpetual_futures: {len(perps_in)}")
            for p in perps_in:
                if 'AAPL' in p.get('symbol', '') or 'TSLA' in p.get('symbol', '') or 'NVDA' in p.get('symbol', '') or 'QQQ' in p.get('symbol', ''):
                    print("  India:", p.get('symbol'))
    except Exception as e:
        print(f"India API check error: {e}")

if __name__ == "__main__":
    dump_all()
