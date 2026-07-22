import json
import urllib.request
import time
import datetime

def fetch_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

def check_depth():
    print("=" * 120)
    print("DELTA EXCHANGE RWA TOKENS HISTORICAL DEPTH AUDIT (/v2/chart/history)")
    print("=" * 120)

    now = int(time.time())
    start = now - 730 * 86400 # 2 years ago

    symbols = [
        'AAPLXUSD', 'NVDAXUSD', 'TSLAXUSD', 'QQQXUSD', 'METAXUSD', 'AMZNXUSD', 'GOOGLXUSD',
        'SNDKBUSD', 'SLVONUSD', 'CBRSBUSD', 'SPCXXUSD', 'NBISBUSD', 'SOXLBUSD', 'XAUTUSD', 'PAXGUSD'
    ]

    for sym in symbols:
        url = f"https://api.india.delta.exchange/v2/chart/history?symbol={sym}&resolution=D&from={start}&to={now}"
        data = fetch_json(url)
        if data and 'result' in data and isinstance(data['result'], dict):
            res = data['result']
            timestamps = res.get('t', [])
            closes = res.get('c', [])
            volumes = res.get('v', [])
            
            if timestamps:
                first_ts = timestamps[0]
                last_ts = timestamps[-1]
                first_dt = datetime.datetime.fromtimestamp(first_ts, tz=datetime.timezone.utc).strftime('%Y-%m-%d')
                last_dt = datetime.datetime.fromtimestamp(last_ts, tz=datetime.timezone.utc).strftime('%Y-%m-%d')
                
                print(f"{sym:<12}: {len(timestamps):4d} daily candles | Date Range: {first_dt} to {last_dt} | Latest Close: ${closes[-1]:.2f} | Latest Vol: {volumes[-1]:.2f}")
            else:
                print(f"{sym:<12}: 0 candles found.")

if __name__ == "__main__":
    check_depth()
