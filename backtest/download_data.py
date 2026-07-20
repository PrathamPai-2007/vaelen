import os
import sys
import zipfile
import argparse
import urllib.request

def download_binance_daily_trades(symbol, date, market_type="spot"):
    """
    Downloads historical daily trades from Binance Public Data S3 archives.
    No API keys or anti-bot bypass needed.
    """
    base_url = "https://data.binance.vision/data"
    filename = f"{symbol}-trades-{date}.zip"
    url = f"{base_url}/{market_type}/daily/trades/{symbol}/{filename}"
    
    # Anchor destination to this script's directory so downloads land in
    # backtest/data/ regardless of the current working directory.
    script_dir = os.path.dirname(os.path.abspath(__file__))
    dest_dir = os.path.join(script_dir, "data")
    os.makedirs(dest_dir, exist_ok=True)
    zip_path = os.path.join(dest_dir, filename)
    
    print(f"Downloading historical data from: {url}")
    try:
        # Request with a standard User-Agent header
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response, open(zip_path, 'wb') as out_file:
            out_file.write(response.read())
        print(f"Successfully downloaded to {zip_path}")
    except Exception as e:
        print(f"Error downloading {symbol} data for {date}: {e}")
        print("Please check if the symbol is correct (e.g. ADAUSDT in uppercase) and the date exists in Binance historical records.")
        # Attempt fallback to Futures if Spot failed
        if market_type == "spot":
            print("Attempting fallback to USD-M Futures market data...")
            return download_binance_daily_trades(symbol, date, market_type="futures/um")
        return False

    # Extract ZIP
    try:
        print(f"Extracting {zip_path}...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(dest_dir)
        print("Extraction complete.")
    except Exception as e:
        print(f"Failed to extract ZIP file: {e}")
        return False
    finally:
        # Cleanup zip
        if os.path.exists(zip_path):
            os.remove(zip_path)
            
    return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download daily trade ticks from Binance public data archives.")
    parser.add_argument("symbol", type=str, help="Binance symbol in uppercase, e.g. SHIBUSDT or ADAUSDT")
    parser.add_argument("date", type=str, help="Date in YYYY-MM-DD format")
    parser.add_argument("--market", type=str, default="spot", choices=["spot", "futures/um"], 
                        help="Market type: spot (default) or futures/um")
    
    args = parser.parse_args()
    success = download_binance_daily_trades(args.symbol.upper(), args.date, args.market)
    sys.exit(0 if success else 1)
