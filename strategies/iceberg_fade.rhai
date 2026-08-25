fn on_tick(ctx) {
    let price = ctx.price;
    let delta_price = price - ctx.past_price;
    let cum_taker_volume = ctx.rolling_volume;
    
    let price_impact = 0.0;
    if cum_taker_volume > 0.0 {
        price_impact = delta_price.abs() / cum_taker_volume;
    }

    let volume_spike = ctx.size > ctx.p95_vol;
    let cooldown_elapsed = ctx.ticks_since_entry >= 5000;
    let min_cvd = 10000.0;
    let max_impact = 0.0001;

    let can_absorb = volume_spike && cum_taker_volume > min_cvd && price_impact < max_impact && cooldown_elapsed;

    if can_absorb {
        if ctx.current_cvd > ctx.past_cvd && delta_price <= 0.0 {
            ctx.sell();
        } else if ctx.current_cvd < ctx.past_cvd && delta_price >= 0.0 {
            ctx.buy();
        }
    }
}
