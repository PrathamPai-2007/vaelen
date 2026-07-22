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

def check_capacity():
    print("=" * 120)
    print("CAPITAL SCALABILITY & MARKET IMPACT ANALYSIS (XAUTUSD vs PAXGUSD)")
    print("=" * 120)

    # 1. Fetch products & tickers
    prods = fetch_json(f"{DELTA_INDIA_API}/v2/products?page_size=1000")
    tickers = fetch_json(f"{DELTA_INDIA_API}/v2/tickers?page_size=1000")

    prod_map = {p['symbol']: p for p in prods.get('result', [])} if prods else {}
    tick_map = {t['symbol']: t for t in tickers.get('result', [])} if tickers else {}

    x_p = prod_map.get('XAUTUSD', {})
    p_p = prod_map.get('PAXGUSD', {})
    x_t = tick_map.get('XAUTUSD', {})
    p_t = tick_map.get('PAXGUSD', {})

    x_mark = float(x_t.get('mark_price') or 4105.0)
    p_mark = float(p_t.get('mark_price') or 4104.0)

    x_vol_24h = float(x_t.get('turnover_usd') or 0.0)
    p_vol_24h = float(p_t.get('turnover_usd') or 0.0)

    x_oi_usd = float(x_t.get('oi_value_usd') or 0.0)
    p_oi_usd = float(p_t.get('oi_value_usd') or 0.0)

    # Max contract limits from products
    x_max_contracts = float(x_p.get('position_size_limit') or 52000)
    p_max_contracts = float(p_p.get('position_size_limit') or 200000)

    x_max_notional_usd = x_max_contracts * 0.001 * x_mark
    p_max_notional_usd = p_max_contracts * 0.001 * p_mark

    print(f"1. Venue Liquidity & Capacity Specs:")
    print(f"   - XAUTUSD 24h Volume : ${x_vol_24h:,.2f} USD | Open Interest: ${x_oi_usd:,.2f} USD | Max Exchange Limit: ${x_max_notional_usd:,.2f} USD")
    print(f"   - PAXGUSD 24h Volume : ${p_vol_24h:,.2f} USD | Open Interest: ${p_oi_usd:,.2f} USD | Max Exchange Limit: ${p_max_notional_usd:,.2f} USD")
    print(f"   - Combined 24h Volume: ${x_vol_24h + p_vol_24h:,.2f} USD | Combined Open Interest: ${x_oi_usd + p_oi_usd:,.2f} USD")

    # 2. Capital Sizing Simulation
    print("\n2. Capital Sizing & Market Impact Matrix (3.0x Leverage, 50% Equity Sizing):")
    print("-" * 120)
    print(f"{'Account Capital':>15} | {'Position Notional':>18} | {'% of Open Interest':>20} | {'Est. Market Impact':>20} | {'30-Day Net Yield ($)':>20} | {'Capacity Status':>15}")
    print("-" * 120)

    for capital in [10000, 25000, 50000, 100000, 250000, 500000, 1000000, 2500000]:
        notional = (capital * 0.50) * 3.0 # Notional position
        leg_notional = notional / 2.0
        
        # Open interest impact
        oi_pct = (leg_notional / min(x_oi_usd, p_oi_usd)) * 100.0 if min(x_oi_usd, p_oi_usd) > 0 else 0
        
        # Estimate market impact (0.1 bps per $50k order slice)
        impact_bps = 0.59 + (notional / 50000.0) * 0.10
        
        # Net 30-day yield: 180.7 bps funding yield minus scaled entry/exit friction
        # Funding yield = +178.20 bps (1.782%)
        # Friction = entry (12.22 bps + impact) + exit (4.72 bps)
        total_friction_bps = 16.94 + impact_bps
        net_30d_yield_bps = 178.20 - total_friction_bps
        net_30d_yield_usd = capital * (net_30d_yield_bps / 10000.0)
        
        status = "OPTIMAL" if oi_pct < 5.0 else "FEASIBLE" if oi_pct < 15.0 else "CAPACITY_CAP"
        
        print(f"${capital:14,d} | ${notional:17,.2f} | {oi_pct:19.2f}% | +{impact_bps:18.2f} bps | ${net_30d_yield_usd:19,.2f} | {status:>15}")

    print("=" * 120)

if __name__ == "__main__":
    check_capacity()
