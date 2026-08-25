import json
import urllib.request

def check_pagination():
    url = "https://api.delta.exchange/v2/products?page_size=1000"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode('utf-8'))
        products = data.get('result', [])
        print(f"Total with page_size=1000: {len(products)}")
        meta = data.get('meta', {})
        print(f"Meta: {meta}")

if __name__ == "__main__":
    check_pagination()
