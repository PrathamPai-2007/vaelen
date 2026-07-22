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

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    valid, meta = validate_symbols(["PAXGUSD", "XAUTUSD", "AAPLXUSD", "NVDAXUSD"])
    print(f"Validation Result: {valid}")
    print(f"Validated Symbols: {list(meta.keys())}")
