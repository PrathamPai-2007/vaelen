import json
import urllib.request

url = "https://api.india.delta.exchange/v2/tickers?page_size=1000"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode('utf-8'))
    result = data.get('result', [])
    tickers = {t['symbol']: t for t in result}
    
    print("XAUTUSD Ticker keys:", tickers.get('XAUTUSD', {}).keys())
    print("XAUTUSD funding fields:", {k: v for k, v in tickers.get('XAUTUSD', {}).items() if 'fund' in k or 'rate' in k})
    print("PAXGUSD funding fields:", {k: v for k, v in tickers.get('PAXGUSD', {}).items() if 'fund' in k or 'rate' in k})
