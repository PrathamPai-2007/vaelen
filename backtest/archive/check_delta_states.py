import json
import urllib.request

def check_states():
    states = ["active", "expired", "settled", "all"]
    for s in states:
        url = f"https://api.delta.exchange/v2/products?states={s}&page_size=1000"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                products = data.get('result', [])
                types = set(p.get('contract_type') for p in products)
                print(f"State '{s}': Count = {len(products)} | Types = {types}")
        except Exception as e:
            print(f"State '{s}' failed: {e}")

if __name__ == "__main__":
    check_states()
