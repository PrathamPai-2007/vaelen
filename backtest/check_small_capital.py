import json
import urllib.request

DELTA_INDIA_API = "https://api.india.delta.exchange"

def fetch_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        print(f"Error: {e}")
        return None

def check_small_capital():
    print("=" * 100)
    print("SMALL CAPITAL SCALING AUDIT ($10 USD to $100 USD)")
    print("=" * 100)

    # 1. Fetch product info
    prods = fetch_json(f"{DELTA_INDIA_API}/v2/products?page_size=1000")
    prod_map = {p['symbol']: p for p in prods.get('result', [])} if prods else {}

    x_val = float(prod_map.get('XAUTUSD', {}).get('contract_value') or 0.001)
    p_val = float(prod_map.get('PAXGUSD', {}).get('contract_value') or 0.001)

    gold_price = 4105.0 # Approx USD / oz
    one_contract_usd = x_val * gold_price # ~$4.105 USD

    print(f"1. Delta Exchange Minimum Contract Granularity:")
    print(f"   - Contract Value: 0.001 troy oz")
    print(f"   - Gold Price: ~${gold_price:.2f} USD")
    print(f"   - Min Trade Unit (1 contract): ${one_contract_usd:.3f} USD notional")

    print("\n2. PnL & Contract Sizing Matrix for Small Capital:")
    print("-" * 100)
    print(f"{'Account Capital':>15} | {'Notional ($)':>15} | {'Contracts per Leg':>18} | {'30-Day Net PnL ($)':>20} | {'30-Day Net Return (%)':>22}")
    print("-" * 100)

    # Net 30-day yield = 1.6126% (+161.26 bps) net of friction
    for cap in [10.0, 25.0, 50.0, 100.0, 250.0, 500.0, 1000.0]:
        allocated_margin = cap * 0.50 # 50% margin
        notional = allocated_margin * 3.0 # 3x leverage
        leg_notional = notional / 2.0
        
        num_contracts = int(leg_notional / one_contract_usd)
        actual_leg_notional = num_contracts * one_contract_usd
        actual_total_notional = actual_leg_notional * 2.0
        
        # Net 30-day PnL: 161.26 bps on actual notional
        net_30d_pnl_usd = actual_total_notional * (0.016126 / 3.0) # Yield on capital
        net_30d_return_pct = (net_30d_pnl_usd / cap) * 100.0 if cap > 0 else 0
        
        print(f"${cap:14.2f} | ${actual_total_notional:14.2f} | {num_contracts:18d} | ${net_30d_pnl_usd:19.2f} | {net_30d_return_pct:21.2f}%")

    print("=" * 100)

if __name__ == "__main__":
    check_small_capital()
