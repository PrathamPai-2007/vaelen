import sys
import os
import struct
import numpy as np
import pandas as pd

def convert_csv(input_csv, output_bin):
    print(f"Reading raw CSV: {input_csv}...")
    
    first_row = pd.read_csv(input_csv, nrows=1, header=None)
    try:
        float(first_row.iloc[0, 0])
        has_header = False
    except (ValueError, TypeError):
        has_header = True

    if not has_header:
        print("Headerless CSV detected. Assuming Binance Trade CSV format...")
        df = pd.read_csv(
            input_csv, 
            header=None, 
            names=['id', 'price', 'qty', 'quote_qty', 'time', 'is_buyer_maker', 'is_best_match']
        )
    else:
        df = pd.read_csv(input_csv)
    
    if 'is_buyer_maker' in df.columns:
        print("Detected Binance format...")
        sample_time = df['time'].iloc[0]
        if sample_time > 1e15:
            exch_ts = df['time'].astype(np.uint64) * 1_000
        else:
            exch_ts = df['time'].astype(np.uint64) * 1_000_000
        is_buy = (df['is_buyer_maker'] == False).astype(np.uint8)
        price = df['price'].astype(np.float64)
        qty = df['qty'].astype(np.float64)
    elif 'local_timestamp' in df.columns:
        print("Detected Tardis / Generic format...")
        sample_ts = df['local_timestamp'].iloc[0]
        mult = 1_000 if sample_ts < 1e16 else 1
        exch_ts = (df['timestamp'].astype(np.uint64) * mult)
        is_buy = (df['side'] == 'buy').astype(np.uint8)
        price = df['price'].astype(np.float64)
        qty = df['amount'].astype(np.float64)
    else:
        raise ValueError("Unknown CSV format!")

    print(f"Total ticks: {len(df)}")
    print(f"Saving to flat binary struct format: {output_bin}...")
    
    dt = np.dtype([
        ('ts', np.uint64),
        ('px', np.float64),
        ('qty', np.float64),
        ('is_buy', np.uint8),
        ('padding', 'V7') # 7 bytes padding
    ])
    
    out_arr = np.empty(len(df), dtype=dt)
    out_arr['ts'] = exch_ts.values
    out_arr['px'] = price.values
    out_arr['qty'] = qty.values
    out_arr['is_buy'] = is_buy.values
    out_arr['padding'] = 0
    
    with open(output_bin, "wb") as f:
        f.write(out_arr.tobytes())

    print("Conversion complete!")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python convert_data.py <input_csv> <output_bin>")
        sys.exit(1)
    convert_csv(sys.argv[1], sys.argv[2])
