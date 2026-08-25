import os
import zipfile
import urllib.request
import numpy as np
import pandas as pd
from hftbacktest import event_dtype, TRADE_EVENT, BUY_EVENT, SELL_EVENT
import toml

def load_toml_config():
    config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../config.toml'))
    with open(config_path, 'r') as f:
        return toml.load(f)

def download_and_convert_symbol_date(symbol_raw, date_str):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(script_dir, "data")
    processed_dir = os.path.join(script_dir, "processed")
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(processed_dir, exist_ok=True)

    sym_clean = symbol_raw.replace("USDT", "USD")
    npz_filename = f"{sym_clean}_{date_str}.npz"
    npz_path = os.path.join(processed_dir, npz_filename)

    if os.path.exists(npz_path):
        return npz_path

    csv_filename = f"{symbol_raw}-trades-{date_str}.csv"
    csv_path = os.path.join(data_dir, csv_filename)

    if not os.path.exists(csv_path):
        zip_filename = f"{symbol_raw}-trades-{date_str}.zip"
        url = f"https://data.binance.vision/data/spot/daily/trades/{symbol_raw}/{zip_filename}"
        zip_path = os.path.join(data_dir, zip_filename)

        print(f"Downloading {symbol_raw} trades for {date_str}...")
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response, open(zip_path, 'wb') as out_file:
                out_file.write(response.read())
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(data_dir)
            if os.path.exists(zip_path):
                os.remove(zip_path)
        except Exception as e:
            url_fut = f"https://data.binance.vision/data/futures/um/daily/trades/{symbol_raw}/{zip_filename}"
            try:
                req = urllib.request.Request(url_fut, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req) as response, open(zip_path, 'wb') as out_file:
                    out_file.write(response.read())
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    zip_ref.extractall(data_dir)
                if os.path.exists(zip_path):
                    os.remove(zip_path)
            except Exception as e2:
                print(f"Failed to download {symbol_raw} for {date_str}: {e2}")
                return None

    if not os.path.exists(csv_path):
        return None

    try:
        df = pd.read_csv(
            csv_path,
            header=None,
            names=['id', 'price', 'qty', 'quote_qty', 'time', 'is_buyer_maker', 'is_best_match']
        )
    except Exception as e:
        print(f"Error reading {csv_filename}: {e}")
        return None

    sample_time = df['time'].iloc[0]
    mult = 1_000 if sample_time > 1e15 else 1_000_000
    local_ts = df['time'].astype(np.int64) * mult
    exch_ts = local_ts
    is_buy = df['is_buyer_maker'] == False
    price = df['price'].astype(np.float64)
    qty = df['qty'].astype(np.float64)

    hft_data = np.zeros(len(df), dtype=event_dtype)
    hft_data['ev'] = np.where(is_buy, TRADE_EVENT | BUY_EVENT, TRADE_EVENT | SELL_EVENT)
    hft_data['local_ts'] = local_ts
    hft_data['exch_ts'] = exch_ts
    hft_data['px'] = price
    hft_data['qty'] = qty

    np.savez(npz_path, data=hft_data)
    print(f"Saved {npz_filename} ({len(df):,} ticks).")
    return npz_path

