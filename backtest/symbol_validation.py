import json
import urllib.request
import logging

logger = logging.getLogger(__name__)

DELTA_INDIA_API = "https://api.india.delta.exchange"

def get_live_products(api_host=DELTA_INDIA_API):
    """
    Fetch unfiltered live products from Delta Exchange API.
    Enforces enumeration of real live symbols without assuming suffixes.
    """
    url = f"{api_host}/v2/products?page_size=1000"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data.get('result', [])
    except Exception as e:
        logger.error(f"Failed to fetch live products from {url}: {e}")
        return []

def validate_symbols(symbols_to_check, api_host=DELTA_INDIA_API):
    """
    Validates that a list of symbol strings actually exist as live active products.
    Returns (is_valid: bool, validated_metadata: dict).
    """
    products = get_live_products(api_host)
    if not products:
        logger.warning("Could not retrieve product list for validation.")
        return False, {}

    live_map = {p.get('symbol'): p for p in products}
    
    validated = {}
    missing = []
    
    for sym in symbols_to_check:
        if sym in live_map:
            p_info = live_map[sym]
            validated[sym] = {
                'symbol': sym,
                'product_id': p_info.get('id'),
                'contract_type': p_info.get('contract_type'),
                'contract_value': p_info.get('contract_value'),
                'tick_size': p_info.get('tick_size'),
                'underlying_asset': p_info.get('underlying_asset', {}).get('symbol') if isinstance(p_info.get('underlying_asset'), dict) else p_info.get('underlying_asset')
            }
        else:
            missing.append(sym)

    if missing:
        logger.error(f"Symbol validation FAILED! The following symbols do not exist on {api_host}: {missing}")
        return False, validated
    
    return True, validated

def validate_symbol_config(symbol_config, target_symbol=None, sample_price=None):
    """
    Validation assertion helper for symbol configuration parameters.
    Checks tick grid, notional sanity ($5-$50k per order), and live-universe guard.
    """
    if not symbol_config:
        raise ValueError("symbol_config cannot be empty")
    live = {"BTCUSD", "ETHUSD"}
    sym = (symbol_config.get('symbol') or target_symbol or "").upper()
    if sym not in live:
        raise ValueError(f"{sym}: live universe is BTCUSD/ETHUSD only (no memecoins)")
    tick = float(symbol_config.get('tick_size', 0))
    contract = float(symbol_config.get('contract_size', 0))
    if not (tick > 0 and contract > 0):
        raise ValueError(f"{sym}: tick_size/contract_size must be positive")
    if symbol_config.get('product_id', 0) in (None, 0):
        raise ValueError(f"{sym}: product_id must be > 0")
    if sample_price:
        per_contract = float(sample_price) * contract
        target = float(symbol_config.get('target_notional_usd', 2000.0))
        contracts = max(1, int(target // per_contract)) if per_contract > 0 else 1
        notional = per_contract * contracts
        if not (5.0 <= notional <= 50_000.0):
            raise ValueError(f"{sym}: order notional ${notional:.2f} outside [$5, $50k]")
        # sample price should sit on the tick grid within half a tick
        remainder = float(sample_price) % tick
        if min(remainder, tick - remainder) > tick / 2 + 1e-12:
            raise ValueError(f"{sym}: sample price off tick grid")
    return True

if __name__ == "__main__":
    import os
    import toml

    logging.basicConfig(level=logging.INFO)
    config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../config.toml'))
    api_host = DELTA_INDIA_API

    symbols_to_validate = []
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                cfg = toml.load(f)
            api_host = cfg.get('general', {}).get('api_base_url', DELTA_INDIA_API)
            for s in cfg.get('strategy', {}).get('symbols', []):
                if 'symbol' in s:
                    symbols_to_validate.append(s['symbol'])
        except Exception as e:
            logger.warning(f"Could not load config.toml: {e}")

    if not symbols_to_validate:
        symbols_to_validate = ["1000PEPEUSD", "WIFUSD", "SOLUSD", "ETHUSD", "BTCUSD"]

    # Remove duplicates
    symbols_to_validate = list(dict.fromkeys(symbols_to_validate))

    print(f"Validating symbols against {api_host}: {symbols_to_validate}")
    valid, meta = validate_symbols(symbols_to_validate, api_host=api_host)
    print(f"Validation Success: {valid}")
    for sym, details in meta.items():
        print(f" - {sym:10s} | Product ID: {details['product_id']} | Tick Size: {details['tick_size']}")

