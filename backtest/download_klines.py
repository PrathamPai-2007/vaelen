"""Download Delta Exchange India 5m candles and funding history."""

import argparse
import json
import os
import struct
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone


PRODUCTS = {
    "BTCUSD": (27, 0.001),
    "ETHUSD": (3136, 0.01),
    "BNBUSD": (15042, 0.1),
    "XRPUSD": (14969, 1.0),
    "DOGEUSD": (14745, 100.0),
    "LINKUSD": (15041, 1.0),
}


def fetch(symbol, resolution, start, end):
    rows = []
    cursor = end
    while cursor > start:
        query = urllib.parse.urlencode({"resolution": resolution, "symbol": symbol, "start": start, "end": cursor})
        url = f"https://api.india.delta.exchange/v2/history/candles?{query}"
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "vaelen-backtest"}), timeout=120) as response:
            batch = json.load(response)["result"]
        if not batch:
            break
        rows.extend(batch)
        oldest = min(int(row["time"]) for row in batch)
        if oldest >= cursor:
            break
        cursor = oldest
    return rows


def unix_seconds(value):
    return int(datetime.combine(value, datetime.min.time(), timezone.utc).timestamp())


def write_candles(candles, funding, output, start, end):
    rates = {int(row["time"]): float(row["close"] or 0.0) / 100.0 for row in funding}
    candles = sorted((row for row in candles if start <= int(row["time"]) < end), key=lambda row: int(row["time"]))
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "wb") as target:
        for row in candles:
            timestamp = int(row["time"])
            values = [float(row[key]) for key in ("open", "high", "low", "close")]
            volume = float(row.get("volume") or 0.0)
            funding_rate = rates.get(timestamp, 0.0) if timestamp % (8 * 3600) == 0 else 0.0
            target.write(struct.pack("<Qdddddd", timestamp, *values, volume, funding_rate))
    return len(candles)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=os.path.join(os.path.dirname(__file__), "data"))
    parser.add_argument("--start", default="2026-02-25")
    parser.add_argument("--end", default="2026-08-25")
    parser.add_argument("symbols", nargs="+", choices=sorted(PRODUCTS))
    args = parser.parse_args()
    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end) + timedelta(days=1)
    start = unix_seconds(start_date)
    end = unix_seconds(end_date)

    for symbol in args.symbols:
        print(f"Downloading Delta {symbol} 5m candles and funding...", flush=True)
        candles = fetch(symbol, "5m", start, end)
        funding = fetch(f"FUNDING:{symbol}", "1h", start, end)
        output = os.path.join(args.output_dir, f"{symbol}_delta_5m_{start_date}_{end_date - timedelta(days=1)}.bin")
        print(f"Wrote {write_candles(candles, funding, output, start, end):,} candles to {output}", flush=True)


if __name__ == "__main__":
    main()
