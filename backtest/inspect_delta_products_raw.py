import json
import urllib.request

def dump_delta_products():
    url = "https://api.delta.exchange/v2/products"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode('utf-8'))
        products = data.get('result', [])
        
    print(f"Total products returned: {len(products)}")
    
    contract_types = set()
    for p in products:
        contract_types.add(p.get('contract_type'))
        
    print(f"Unique contract_types found: {contract_types}")
    
    # Categorize products by contract_type
    by_type = {}
    for p in products:
        ct = p.get('contract_type')
        if ct not in by_type:
            by_type[ct] = []
        by_type[ct].append(p)
        
    for ct, prod_list in by_type.items():
        print(f"\n--- Contract Type: '{ct}' (Count: {len(prod_list)}) ---")
        # Print sample symbol and keys
        for p in prod_list[:5]:
            symbol = p.get('symbol')
            settle = p.get('settlement_time')
            underlying = p.get('underlying_asset', {}).get('symbol') if isinstance(p.get('underlying_asset'), dict) else p.get('underlying_asset')
            print(f"  Symbol: {symbol:<25} | Settle: {str(settle):<25} | Underlying: {underlying}")
            
    # Search for anything with an expiry or settlement time that is NOT option
    print("\n--- NON-OPTION PRODUCTS WITH A SETTLEMENT/EXPIRY TIME ---")
    non_option_dated = []
    for p in products:
        ct = p.get('contract_type', '')
        if 'option' not in ct and p.get('settlement_time') is not None:
            non_option_dated.append(p)
            
    print(f"Found {len(non_option_dated)} non-option dated products.")
    for p in non_option_dated:
        print(" ", p)

    # Search for MOVE contracts or calendar spreads or futures
    print("\n--- PRODUCTS CONTAINING 'MOVE', 'SPREAD', or 'FUTURE' IN SYMBOL/TYPE ---")
    special_prods = []
    for p in products:
        sym = p.get('symbol', '').upper()
        ct = p.get('contract_type', '').upper()
        if 'MOVE' in sym or 'SPREAD' in sym or 'FUTURE' in sym or 'MOVE' in ct or 'SPREAD' in ct or 'FUTURE' in ct:
            special_prods.append(p)
            
    print(f"Found {len(special_prods)} special products.")
    for p in special_prods:
        print(" ", p.get('symbol'), p.get('contract_type'), p.get('settlement_time'))

if __name__ == "__main__":
    dump_delta_products()
