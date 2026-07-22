import json
import urllib.request
import numpy as np

def fetch_binance_funding_history(symbol, limit=1000):
    url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit={limit}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode('utf-8'))

def get_cycles(rates_bps_8h, min_hold_days, round_trip_cost=35.06):
    min_hold_periods = min_hold_days * 3
    n = len(rates_bps_8h)
    cycles = []
    in_position = False
    pos_direction = 0
    entry_period = 0
    cycle_gross_bps = 0.0
    trailing_window = 9
    
    for i in range(n):
        start_idx = max(0, i - trailing_window + 1)
        trail_avg = np.mean(rates_bps_8h[start_idx:i+1])
        
        if not in_position:
            if abs(trail_avg) > 0.2:
                in_position = True
                pos_direction = 1 if trail_avg > 0 else -1
                entry_period = i
                cycle_gross_bps = rates_bps_8h[i] * pos_direction
        else:
            period_gain = rates_bps_8h[i] * pos_direction
            cycle_gross_bps += period_gain
            held_periods = i - entry_period + 1
            funding_flipped = (trail_avg * pos_direction < 0)
            is_last = (i == n - 1)
            
            if (held_periods >= min_hold_periods and funding_flipped) or is_last:
                net_cycle_bps = cycle_gross_bps - round_trip_cost
                cycles.append({
                    'cycle_id': len(cycles) + 1,
                    'entry_idx': entry_period,
                    'exit_idx': i,
                    'duration_periods': held_periods,
                    'duration_days': held_periods / 3.0,
                    'gross_bps': cycle_gross_bps,
                    'net_bps': net_cycle_bps,
                    'direction': pos_direction
                })
                in_position = False
                cycle_gross_bps = 0.0

    return cycles

def investigate_wif():
    raw_data = fetch_binance_funding_history("WIFUSDT", limit=1000)
    rates = np.array([float(d['fundingRate']) for d in raw_data])
    times = np.array([int(d['fundingTime']) for d in raw_data])
    rates_bps_8h = rates * 10000.0

    print("==========================================================================================")
    print("DETAILED CYCLE INVESTIGATION: WIFUSD NON-MONOTONIC EV ACROSS COMMITMENT WINDOWS")
    print("==========================================================================================")

    for w_days in [3, 7, 14]:
        cycles = get_cycles(rates_bps_8h, min_hold_days=w_days)
        net_bps_list = [c['net_bps'] for c in cycles]
        gross_bps_list = [c['gross_bps'] for c in cycles]
        durations = [c['duration_days'] for c in cycles]
        
        print(f"\n--- Commitment Window: {w_days} Days (Min Hold = {w_days*3} periods) ---")
        print(f"Total Cycles: {len(cycles)} | Total Net PnL: {sum(net_bps_list):.2f} bps | Mean Net EV/Cycle: {np.mean(net_bps_list):.2f} bps | Mean Duration: {np.mean(durations):.1f} days")
        print(f"Mean Gross Carry / Cycle: {np.mean(gross_bps_list):.2f} bps | Fixed Entry/Exit Cost: -35.06 bps")
        print("-" * 90)
        print(f"{'Cycle #':>7} | {'Entry Idx':>9} | {'Exit Idx':>8} | {'Days':>6} | {'Direction':>9} | {'Gross Carry':>11} | {'Net EV':>9}")
        print("-" * 90)
        for c in cycles:
            dir_str = "LONG" if c['direction'] == 1 else "SHORT"
            print(f"{c['cycle_id']:7d} | {c['entry_idx']:9d} | {c['exit_idx']:8d} | {c['duration_days']:5.1f}d | {dir_str:>9} | {c['gross_bps']:10.2f} bps | {c['net_bps']:8.2f} bps")

if __name__ == "__main__":
    investigate_wif()
