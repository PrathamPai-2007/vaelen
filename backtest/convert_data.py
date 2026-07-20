import sys
import os
import numpy as np
import pandas as pd
from hftbacktest import event_dtype, TRADE_EVENT, BUY_EVENT, SELL_EVENT

def convert_csv(input_csv, output_npz):
    print(f"Reading raw CSV: {input_csv}...")
    
    # Read the first line to check if it's headerless
    first_row = pd.read_csv(input_csv, nrows=1, header=None)
    try:
        # If the first column is a number, it's headerless
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
    
    # Auto-detect format: Binance Data Vision vs Tardis vs Generic
    if 'is_buyer_maker' in df.columns:
        print("Detected Binance Data Vision format (Free Source)...")
        # Time is in microseconds or milliseconds. Let's check scale.
        sample_time = df['time'].iloc[0]
        # If timestamp is > 1e15, it's in microseconds. If > 1e12, it's milliseconds.
        if sample_time > 1e15:
            local_ts = df['time'].astype(np.int64) * 1_000
        else:
            local_ts = df['time'].astype(np.int64) * 1_000_000
        exch_ts = local_ts
        # is_buyer_maker == True means Taker was Sell
        is_buy = df['is_buyer_maker'] == False
        price = df['price'].astype(np.float64)
        qty = df['qty'].astype(np.float64)
    elif 'local_timestamp' in df.columns:
        print("Detected Tardis / Generic format...")
        sample_ts = df['local_timestamp'].iloc[0]
        mult = 1_000 if sample_ts < 1e16 else 1
        local_ts = df['local_timestamp'].astype(np.int64) * mult
        exch_ts = df['timestamp'].astype(np.int64) * mult
        is_buy = df['side'] == 'buy'
        price = df['price'].astype(np.float64)
        qty = df['amount'].astype(np.float64)
    else:
        raise ValueError("Unknown CSV format! Expected Tardis or Binance Data Vision columns.")

    hft_data = np.zeros(len(df), dtype=event_dtype)
    hft_data['ev'] = np.where(is_buy, TRADE_EVENT | BUY_EVENT, TRADE_EVENT | SELL_EVENT)
    hft_data['local_ts'] = local_ts
    hft_data['exch_ts'] = exch_ts
    hft_data['px'] = price
    hft_data['qty'] = qty

    print(f"Saving to binary HftBacktest format: {output_npz}...")
    np.savez(output_npz, data=hft_data)
    print("Conversion complete!")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python convert_data.py <input_csv> <output_npz>")
        sys.exit(1)
    convert_csv(sys.argv[1], sys.argv[2])
