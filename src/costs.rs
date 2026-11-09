//! Unified fee / slippage / funding model (Delta India perps).
//!
//! Verified 2026: taker 0.05%, maker 0.02%, +18% GST on fees.
//! Notional = price * contract_size * contracts.

pub const TAKER_FEE_RATE: f64 = 0.0005;
pub const MAKER_FEE_RATE: f64 = 0.0002;
pub const GST_MULT: f64 = 1.18;

pub fn notional(price: f64, contract_size: f64, contracts: i64) -> f64 {
    price * contract_size * contracts as f64
}

/// Fee for one leg. `is_maker` selects 0.02% vs 0.05%, GST included.
pub fn leg_fee(notional: f64, is_maker: bool) -> f64 {
    let rate = if is_maker {
        MAKER_FEE_RATE
    } else {
        TAKER_FEE_RATE
    };
    notional * rate * GST_MULT
}

/// Slippage for one exit leg: max(half-spread from tick_size, config bps).
/// `tick_size` is a price increment; half-spread notional = tick/2 * contracts*contract_size.
pub fn leg_slippage(
    price: f64,
    contract_size: f64,
    contracts: i64,
    tick_size: f64,
    slippage_bps: f64,
) -> f64 {
    let half_spread = (tick_size / 2.0) * contract_size * contracts as f64;
    let bps = price * (slippage_bps / 10_000.0) * contract_size * contracts as f64;
    half_spread.max(bps).max(0.0)
}

/// Contracts for a fixed USD notional target. At least 1.
pub fn contracts_for_notional(price: f64, contract_size: f64, target_usd: f64) -> i64 {
    if price <= 0.0 || !price.is_finite() || contract_size <= 0.0 || target_usd <= 0.0 {
        return 1;
    }
    let per_contract = price * contract_size;
    if per_contract <= 0.0 {
        return 1;
    }
    ((target_usd / per_contract).floor() as i64).max(1)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn maker_cheaper_than_taker_with_gst() {
        let n = 2000.0;
        let maker = leg_fee(n, true);
        let taker = leg_fee(n, false);
        assert!((maker - 2000.0 * 0.0002 * 1.18).abs() < 1e-9);
        assert!((taker - 2000.0 * 0.0005 * 1.18).abs() < 1e-9);
        assert!(maker < taker);
    }

    #[test]
    fn sizing_btc_eth() {
        // BTC @100k, 0.001 lot -> $100/contract -> 20 contracts for $2k
        assert_eq!(contracts_for_notional(100_000.0, 0.001, 2000.0), 20);
        // ETH @3000, 0.01 lot -> $30/contract -> 66 contracts
        assert_eq!(contracts_for_notional(3000.0, 0.01, 2000.0), 66);
    }

    #[test]
    fn slippage_floor_is_half_spread() {
        let s = leg_slippage(100_000.0, 0.001, 20, 0.5, 0.0);
        assert!(s > 0.0);
    }

    #[test]
    fn slippage_uses_bps_when_larger_than_half_spread() {
        // 100k * 5bps * 0.001 * 20 = 1.0 ; half-spread = 0.25*0.001*20 = 0.005
        let s = leg_slippage(100_000.0, 0.001, 20, 0.5, 5.0);
        assert!((s - 1.0).abs() < 1e-9);
    }

    #[test]
    fn sizing_never_returns_zero_or_panics_on_bad_input() {
        assert_eq!(contracts_for_notional(0.0, 0.001, 2000.0), 1);
        assert_eq!(contracts_for_notional(f64::NAN, 0.001, 2000.0), 1);
        assert_eq!(contracts_for_notional(100.0, 0.0, 2000.0), 1);
        // notional smaller than one contract still trades one lot
        assert_eq!(contracts_for_notional(100_000.0, 0.001, 10.0), 1);
    }

    #[test]
    fn notional_is_price_times_size_times_contracts() {
        assert_eq!(notional(100.0, 0.5, 4), 200.0);
    }
}
