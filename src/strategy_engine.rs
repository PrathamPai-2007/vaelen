use parking_lot::RwLock;
use std::collections::{HashMap, VecDeque};
use std::fs::OpenOptions;
use std::io::{BufWriter, Write};
use std::sync::Arc;
use tokio::sync::mpsc;
use tracing::{debug, error, info, warn};

use crate::config::{AppConfig, SymbolConfig};
use crate::costs::{contracts_for_notional, leg_fee, leg_slippage, notional};
use crate::orders::{OrderManager, OrderResult};
use crate::session::{SessionTracker, TradeRecord};
use crate::websocket::EngineMessage;

const HEARTBEAT_TICK_INTERVAL: u64 = 200;
const BASIS_POINTS_DENOMINATOR: f64 = 10000.0;
const SHUTDOWN_POLL_INTERVAL_MS: u64 = 500;

#[derive(Clone, Debug)]
pub struct ActiveOrder {
    pub id: String,
    pub price: f64,
    pub side: String,
    pub size: i64,
    pub created_at: u64,
    pub kind: String, // "Entry", "TP", "SL"
}

#[derive(Clone, Debug)]
pub struct PaperTrade {
    pub entry_time: u64,
    pub entry_price: f64,
    pub side: String,
    pub size: i64,
    pub ticks_elapsed: usize,
    pub highest_price: f64,
    pub lowest_price: f64,
    pub stop_loss_price: f64,
    pub take_profit_price: f64,
}

#[derive(Debug, Clone)]
pub struct PendingPaperOrder {
    pub entry_time: u64,
    pub limit_price: f64,
    pub side: String,
    pub size: i64,
    pub stop_loss_price: f64,
    pub take_profit_price: f64,
    pub ticks_held: usize,
    /// Base-asset quantity ahead of us in the maker queue (decays as volume trades).
    pub queue_ahead_base: f64,
}

#[derive(Clone, Debug)]
pub struct LiveTrade {
    pub product_id: u64,
    pub entry_time: u64,
    pub entry_price: f64,
    pub side: String,
    pub size: i64,
    pub ticks_elapsed: usize,
    pub highest_price: f64,
    pub lowest_price: f64,
    pub order_id: Option<String>,
    pub fill_price: Option<f64>,
    pub stop_loss_price: f64,
    pub take_profit_price: f64,
}

pub struct SymbolState {
    pub price_queue: VecDeque<f64>,
    pub cvd_queue: VecDeque<f64>,
    pub size_queue: VecDeque<f64>,
    pub side_queue: VecDeque<f64>,
    pub volume_buffer: VecDeque<f64>,
    pub cached_p95_volume: f64,
    pub p95_counter: usize,
    pub current_cvd: f64,
    pub total_ticks: u64,
    pub last_entry_tick: u64,
    pub active_paper_trades: Vec<PaperTrade>,
    pub pending_paper_orders: Vec<PendingPaperOrder>,
    pub active_live_trades: Vec<LiveTrade>,
    pub active_orders: Vec<ActiveOrder>,
    pub rolling_volume_sum: f64,
    pub rolling_volume_sq_sum: f64,
    pub rolling_buy_volume: f64,
    pub rolling_sell_volume: f64,

    // Candle ATR state fields
    pub candle_tr_buffer: VecDeque<f64>,
    pub curr_candle_high: f64,
    pub curr_candle_low: f64,
    pub curr_candle_close: f64,
    pub curr_candle_start_ts: Option<u64>,
    pub prev_candle_close: Option<f64>,
    pub curr_atr: f64,

    // 1m Candle Volume P95 state fields
    pub current_1m_start_ts: Option<u64>,
    pub current_1m_vol: f64,
}

impl SymbolState {
    pub fn new(max_capacity: usize) -> Self {
        Self {
            price_queue: VecDeque::with_capacity(max_capacity),
            cvd_queue: VecDeque::with_capacity(max_capacity),
            size_queue: VecDeque::with_capacity(max_capacity),
            side_queue: VecDeque::with_capacity(max_capacity),
            volume_buffer: VecDeque::with_capacity(1000),
            cached_p95_volume: f64::INFINITY,
            p95_counter: 0,
            current_cvd: 0.0,
            total_ticks: 0,
            last_entry_tick: 0,
            active_paper_trades: Vec::new(),
            pending_paper_orders: Vec::new(),
            active_live_trades: Vec::new(),
            active_orders: Vec::new(),
            rolling_volume_sum: 0.0,
            rolling_volume_sq_sum: 0.0,
            rolling_buy_volume: 0.0,
            rolling_sell_volume: 0.0,
            candle_tr_buffer: VecDeque::with_capacity(1000),
            curr_candle_high: 0.0,
            curr_candle_low: 0.0,
            curr_candle_close: 0.0,
            curr_candle_start_ts: None,
            prev_candle_close: None,
            curr_atr: 0.0,
            current_1m_start_ts: None,
            current_1m_vol: 0.0,
        }
    }
}

#[cfg(test)]
#[allow(clippy::items_after_test_module)]
mod tests {
    use super::*;

    #[test]
    fn test_candle_atr_known_sequence_and_warmup() {
        let mut state = SymbolState::new(100);
        let candle_mins = 5;
        let atr_period = 2;
        let tick_size = 0.01;

        // Candle 1 (ts 0..300): High 105, Low 98, Close 98
        update_candle_atr(&mut state, 100.0, 0, candle_mins, atr_period, tick_size);
        update_candle_atr(&mut state, 105.0, 100, candle_mins, atr_period, tick_size);
        update_candle_atr(&mut state, 98.0, 200, candle_mins, atr_period, tick_size);

        assert_eq!(state.curr_atr, 0.0, "ATR must be 0.0 during warm-up");

        // Candle 2 (ts 300..600): Starts at ts 300. Candle 1 completes (H=105, L=98, C=98, TR1=7.0)
        update_candle_atr(&mut state, 102.0, 300, candle_mins, atr_period, tick_size);
        update_candle_atr(&mut state, 110.0, 400, candle_mins, atr_period, tick_size);

        assert_eq!(
            state.curr_atr, 0.0,
            "ATR must be 0.0 until atr_period candles complete"
        );

        // Candle 3 (ts 600..900): Starts at ts 600. Candle 2 completes (H=110, L=102, C=110, prev_close=98)
        // TR2 = max(110-102, |110-98|, |102-98|) = max(8, 12, 4) = 12.0
        // candle_tr_buffer = [7.0, 12.0]. Mean TR = 9.5
        update_candle_atr(&mut state, 108.0, 600, candle_mins, atr_period, tick_size);

        assert!(
            (state.curr_atr - 9.5).abs() < 1e-4,
            "Candle ATR must equal 9.5"
        );
    }

    #[test]
    fn test_zero_atr_floor() {
        let mut state = SymbolState::new(100);
        let candle_mins = 5;
        let atr_period = 2;
        let tick_size = 0.1;

        // Completely flat market: all ticks at 100.0
        update_candle_atr(&mut state, 100.0, 0, candle_mins, atr_period, tick_size);
        update_candle_atr(&mut state, 100.0, 300, candle_mins, atr_period, tick_size);
        update_candle_atr(&mut state, 100.0, 600, candle_mins, atr_period, tick_size);

        // Mean TR is 0.0. Floor = max(0.1 * 5.0, 100.0 * 0.0005) = max(0.5, 0.05) = 0.5
        assert!(
            (state.curr_atr - 0.5).abs() < 1e-4,
            "ATR floor must prevent 0.0 ATR"
        );
    }

    #[test]
    fn test_paper_trade_limit_order_realism() {
        let mut state = SymbolState::new(100);
        state.pending_paper_orders.push(PendingPaperOrder {
            entry_time: 1000,
            limit_price: 100.0,
            side: "buy".to_string(),
            size: 1,
            stop_loss_price: 95.0,
            take_profit_price: 105.0,
            ticks_held: 0,
            queue_ahead_base: 0.0,
        });

        // Tick 1: Price touches 100.0 (not traded through yet) -> Should NOT fill
        update_pending_paper_orders(&mut state, 100.0, 1001, 15, false, 1.0, 1.0);
        assert_eq!(
            state.pending_paper_orders.len(),
            1,
            "Pending order must remain when price only touches limit price"
        );
        assert_eq!(
            state.active_paper_trades.len(),
            0,
            "Trade must NOT fill when price only touches limit price"
        );

        // Tick 2: Price stays at 100.0 -> Should NOT fill
        update_pending_paper_orders(&mut state, 100.0, 1002, 15, false, 1.0, 1.0);
        assert_eq!(
            state.pending_paper_orders.len(),
            1,
            "Pending order must remain"
        );
        assert_eq!(state.active_paper_trades.len(), 0, "Trade must NOT fill");

        // Tick 3: sell aggressor trades through long limit -> Should FILL!
        update_pending_paper_orders(&mut state, 99.5, 1003, 15, false, 1.0, 1.0);
        assert_eq!(
            state.pending_paper_orders.len(),
            0,
            "Pending order should be removed after fill"
        );
        assert_eq!(
            state.active_paper_trades.len(),
            1,
            "Active paper trade must be created upon trade-through fill"
        );
        assert_eq!(
            state.active_paper_trades[0].entry_price, 100.0,
            "Entry price must equal limit price"
        );
    }

    #[test]
    fn test_maker_queue_needs_opposite_side_and_clears_queue() {
        let mut state = SymbolState::new(100);
        state.pending_paper_orders.push(PendingPaperOrder {
            entry_time: 1,
            limit_price: 100.0,
            side: "buy".to_string(),
            size: 2,
            stop_loss_price: 95.0,
            take_profit_price: 105.0,
            ticks_held: 0,
            queue_ahead_base: 5.0,
        });
        // Same-side buy tick through price must NOT fill and must NOT consume queue.
        update_pending_paper_orders(&mut state, 99.0, 2, 15, true, 10.0, 1.0);
        assert_eq!(state.active_paper_trades.len(), 0);
        assert!((state.pending_paper_orders[0].queue_ahead_base - 5.0).abs() < 1e-9);
        // Opposite-side sell tick consumes 1.0; 4.0 ahead remains.
        update_pending_paper_orders(&mut state, 99.0, 3, 15, false, 1.0, 1.0);
        assert_eq!(state.active_paper_trades.len(), 0);
        assert!((state.pending_paper_orders[0].queue_ahead_base - 4.0).abs() < 1e-9);
        // Next sell tick clears queue and has size -> fills.
        update_pending_paper_orders(&mut state, 99.0, 4, 15, false, 4.0, 1.0);
        assert_eq!(state.active_paper_trades.len(), 1);
    }
}

pub fn update_candle_atr(
    state: &mut SymbolState,
    price: f64,
    trade_ts_sec: u64,
    candle_mins: usize,
    atr_period: usize,
    tick_size: f64,
) {
    if price <= 0.0 || !price.is_finite() {
        return;
    }

    let interval_sec = (candle_mins as u64) * 60;
    let bucket = (trade_ts_sec / interval_sec) * interval_sec;

    match state.curr_candle_start_ts {
        None => {
            state.curr_candle_start_ts = Some(bucket);
            state.curr_candle_high = price;
            state.curr_candle_low = price;
            state.curr_candle_close = price;
        }
        Some(start_ts) if bucket > start_ts => {
            // Emit one TR per skipped candle (cap 10) so gaps don't collapse ATR.
            let interval = interval_sec.max(1);
            let mut skipped = ((bucket - start_ts) / interval) as usize;
            skipped = skipped.clamp(1, 10);
            for _ in 0..skipped {
                let high = state.curr_candle_high;
                let low = state.curr_candle_low;

                let tr = match state.prev_candle_close {
                    Some(prev_close) => {
                        let tr1 = high - low;
                        let tr2 = (high - prev_close).abs();
                        let tr3 = (low - prev_close).abs();
                        tr1.max(tr2).max(tr3)
                    }
                    None => high - low,
                };

                state.candle_tr_buffer.push_back(tr);
                if state.candle_tr_buffer.len() > 1000 {
                    state.candle_tr_buffer.pop_front();
                }

                state.prev_candle_close = Some(state.curr_candle_close);
            }

            state.curr_candle_start_ts = Some(bucket);
            state.curr_candle_high = price;
            state.curr_candle_low = price;
            state.curr_candle_close = price;
        }
        Some(_) => {
            state.curr_candle_high = state.curr_candle_high.max(price);
            state.curr_candle_low = state.curr_candle_low.min(price);
            state.curr_candle_close = price;
        }
    }

    if state.candle_tr_buffer.len() >= atr_period {
        let len = state.candle_tr_buffer.len();
        let sum: f64 = state.candle_tr_buffer.iter().skip(len - atr_period).sum();
        let mean_tr = sum / (atr_period as f64);

        let floor = (tick_size * 5.0).max(price * 0.0005);
        state.curr_atr = mean_tr.max(floor);
    } else {
        state.curr_atr = 0.0;
    }
}

pub fn compute_rolling_p95(buffer: &VecDeque<f64>) -> f64 {
    if buffer.len() < 10 {
        return f64::INFINITY;
    }
    let mut sorted: Vec<f64> = buffer.iter().cloned().filter(|v| v.is_finite()).collect();
    if sorted.len() < 10 {
        return f64::INFINITY;
    }
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    // Nearest-rank: ceil(0.95*n)-1
    let idx = ((sorted.len() as f64 * 0.95).ceil() as usize)
        .saturating_sub(1)
        .min(sorted.len() - 1);
    sorted[idx]
}

pub fn update_1m_volume_p95(state: &mut SymbolState, size: f64, trade_ts_sec: u64) {
    let bucket_1m = (trade_ts_sec / 60) * 60;
    match state.current_1m_start_ts {
        None => {
            state.current_1m_start_ts = Some(bucket_1m);
            state.current_1m_vol = size;
        }
        Some(start_ts) if bucket_1m > start_ts => {
            // Account for every skipped minute (cap 10) so volume isn't under-counted.
            let mut skipped = ((bucket_1m - start_ts) / 60) as usize;
            skipped = skipped.clamp(1, 10);
            for i in 0..skipped {
                let v = if i == 0 { state.current_1m_vol } else { 0.0 };
                state.volume_buffer.push_back(v);
                if state.volume_buffer.len() > 1000 {
                    state.volume_buffer.pop_front();
                }
            }

            state.p95_counter += 1;
            if state.p95_counter >= 5 || state.cached_p95_volume.is_infinite() {
                if state.volume_buffer.len() >= 10 {
                    state.cached_p95_volume = compute_rolling_p95(&state.volume_buffer);
                } else {
                    state.cached_p95_volume = f64::INFINITY;
                }
                state.p95_counter = 0;
            }

            state.current_1m_start_ts = Some(bucket_1m);
            state.current_1m_vol = size;
        }
        Some(_) => {
            state.current_1m_vol += size;
        }
    }
}

/// Restored maker queue: a pending buy (long entry) only fills when a SELL
/// aggressor trades *through* the limit AND consumes the queue ahead of us.
/// Same mirror for sells. This replaces the old instant-fill-at-touch model.
pub fn update_pending_paper_orders(
    state: &mut SymbolState,
    price: f64,
    now_secs: u64,
    maker_timeout_ticks: usize,
    tick_is_buy: bool,
    tick_qty_base: f64,
    contract_size: f64,
) {
    if state.pending_paper_orders.is_empty() {
        return;
    }

    let mut i = 0;
    while i < state.pending_paper_orders.len() {
        let p = &mut state.pending_paper_orders[i];
        p.ticks_held += 1;

        let is_buy_order = p.side.eq_ignore_ascii_case("buy");
        // Opposite-side aggressor required for a maker fill.
        let opposite_side = tick_is_buy != is_buy_order;
        let through = if is_buy_order {
            price < p.limit_price
        } else {
            price > p.limit_price
        };
        // Consume queue with opposite-side volume only.
        if opposite_side && tick_qty_base > 0.0 {
            p.queue_ahead_base = (p.queue_ahead_base - tick_qty_base).max(0.0);
        }
        let order_base = p.size as f64 * contract_size;
        // Fill needs trade-through + queue cleared + enough size on this tick
        // (at least half the order printed through us).
        let filled = through
            && opposite_side
            && p.queue_ahead_base <= 0.0
            && tick_qty_base >= order_base * 0.5;

        if filled {
            let order = state.pending_paper_orders.remove(i);
            state.active_paper_trades.push(PaperTrade {
                entry_time: now_secs,
                entry_price: order.limit_price,
                side: order.side,
                size: order.size,
                ticks_elapsed: 0,
                highest_price: price,
                lowest_price: price,
                stop_loss_price: order.stop_loss_price,
                take_profit_price: order.take_profit_price,
            });
            state.last_entry_tick = state.total_ticks;
        } else if p.ticks_held >= maker_timeout_ticks {
            state.pending_paper_orders.remove(i);
        } else {
            i += 1;
        }
    }
}

pub async fn run_strategy_engine(
    mut trade_rx: mpsc::Receiver<EngineMessage>,
    order_manager: Arc<OrderManager>,
    session_tracker: Arc<RwLock<SessionTracker>>,
    config: Arc<AppConfig>,
    mut shutdown_rx: tokio::sync::broadcast::Receiver<()>,
    telegram_tx: Option<tokio::sync::mpsc::Sender<String>>,
) {
    let config_map: HashMap<String, SymbolConfig> = config
        .strategy
        .symbols
        .iter()
        .map(|c| (c.symbol.clone(), c.clone()))
        .collect();
    let mut states: HashMap<String, SymbolState> = HashMap::new();
    let mut scripts: HashMap<String, Option<crate::scripting::VaelenScriptEngine>> = HashMap::new();
    for sym_cfg in config.strategy.symbols.iter() {
        if let Some(script_path) = &sym_cfg.script_path {
            match crate::scripting::VaelenScriptEngine::new(script_path) {
                Ok(engine) => {
                    info!(
                        "Loaded script for symbol {}: {}",
                        sym_cfg.symbol, script_path
                    );
                    scripts.insert(sym_cfg.symbol.clone(), Some(engine));
                }
                Err(e) => {
                    error!("Failed to load script {}: {}", script_path, e);
                    scripts.insert(sym_cfg.symbol.clone(), None);
                }
            }
        } else {
            scripts.insert(sym_cfg.symbol.clone(), None);
        }
    }

    let paper_trade_mode = config.general.paper_trade_mode;
    let slippage_bps = config.fees.slippage_bps;
    let max_concurrent_positions = config.general.max_concurrent_positions;

    if let Err(e) = std::fs::create_dir_all(&config.general.trades_dir) {
        error!(
            "failed to create trades_dir {}: {}",
            config.general.trades_dir, e
        );
    }
    let session_filename = format!(
        "{}/{}.csv",
        config.general.trades_dir,
        chrono::Utc::now().format("%Y-%m-%d_%H-%M-%S")
    );
    let mut csv_writer = OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(true)
        .open(&session_filename)
        .map(BufWriter::new)
        .ok();
    if let Some(ref mut writer) = csv_writer {
        let _ = writer.write_all(b"symbol,entry_time,side,entry_price,exit_price,exit_reason,gross_pnl,fees,slippage,net_pnl\n");
    }

    info!(
        "Strategy engine initialized. Paper Trading: {}. Symbols: {:?}",
        paper_trade_mode,
        config
            .strategy
            .symbols
            .iter()
            .map(|c| &c.symbol)
            .collect::<Vec<_>>()
    );

    let mut feed_is_stale = false;

    loop {
        let msg = tokio::select! {
            msg_result = trade_rx.recv() => {
                match msg_result {
                    Some(m) => m,
                    None => break,
                }
            }
            _ = shutdown_rx.recv() => {
                info!("Strategy engine received shutdown signal.");
                break;
            }
        };

        let trade = match msg {
            EngineMessage::Trade(t) => t,
            EngineMessage::FeedStale => {
                if !feed_is_stale {
                    feed_is_stale = true;
                    if !paper_trade_mode {
                        warn!("Strategy engine: Feed is stale. Halting new order entries.");
                    } else {
                        warn!("Strategy engine: Feed is stale.");
                    }
                }
                continue;
            }
            EngineMessage::FeedResumed => {
                if feed_is_stale {
                    feed_is_stale = false;
                    info!("Strategy engine: Feed resumed. Allowing new order entries.");
                }
                continue;
            }
        };

        let price = match trade.price.parse::<f64>() {
            Ok(p) => p,
            Err(e) => {
                error!("Failed to parse price {}: {}", trade.price, e);
                continue;
            }
        };
        let size = match trade.size.parse::<f64>() {
            Ok(s) => s,
            Err(e) => {
                error!("Failed to parse size {}: {}", trade.size, e);
                continue;
            }
        };

        let symbol = trade.symbol.clone();
        let symbol_config = match config_map.get(&symbol) {
            Some(c) => c.clone(),
            None => {
                warn!(
                    "unknown symbol {} from feed; skipping (check WS vs strategy symbols)",
                    symbol
                );
                continue;
            }
        };

        // Global cap: count other symbols before taking the mutable borrow.
        let others_count: usize = states
            .iter()
            .filter(|(k, _)| *k != &symbol)
            .map(|(_, s)| {
                if paper_trade_mode {
                    s.active_paper_trades.len() + s.pending_paper_orders.len()
                } else {
                    s.active_live_trades.len()
                }
            })
            .sum();
        let state = states
            .entry(symbol.clone())
            .or_insert_with(|| SymbolState::new(symbol_config.max_capacity));

        if trade.side.eq_ignore_ascii_case("buy") {
            state.current_cvd += size;
        } else if trade.side.eq_ignore_ascii_case("sell") {
            state.current_cvd -= size;
        } else {
            continue;
        }

        state.total_ticks += 1;
        if state.total_ticks % HEARTBEAT_TICK_INTERVAL == 0 {
            let active_count = if paper_trade_mode {
                state.active_paper_trades.len()
            } else {
                state.active_live_trades.len()
            };
            debug!(
                "[{}] Tick #{} | Price: {} | CVD: {:.5} | Queue: {}/{} | Active: {}",
                symbol,
                state.total_ticks,
                price,
                state.current_cvd,
                state.price_queue.len(),
                symbol_config.max_capacity,
                active_count
            );
        }

        let now_secs = trade.timestamp_ns / 1_000_000_000;

        update_candle_atr(
            state,
            price,
            now_secs,
            symbol_config.candle_mins as usize,
            symbol_config.atr_period,
            symbol_config.tick_size,
        );
        update_1m_volume_p95(state, size, now_secs);
        let atr = state.curr_atr;
        let mut newly_filled_entries = Vec::new();
        let mut filled_tp_orders: Vec<ActiveOrder> = Vec::new();
        if !paper_trade_mode {
            let mut i = 0;
            while i < state.active_orders.len() {
                let order = &state.active_orders[i];
                if now_secs.saturating_sub(order.created_at) > 5 && order.kind == "Entry" {
                    warn!(
                        "[{}] Entry order {} timed out after 5s. Cancelling.",
                        symbol, order.id
                    );
                    let _ = order_manager
                        .cancel_order(symbol_config.product_id, &order.id)
                        .await;
                    state.active_orders.remove(i);
                    continue;
                }

                // Poll status
                if let Ok(status) = order_manager.get_order_status(&order.id).await {
                    match status {
                        OrderResult::Filled(fill) => {
                            info!(
                                "[{}] Order {} ({}) filled at {}",
                                symbol, order.id, order.kind, fill.price
                            );
                            if order.kind == "Entry" {
                                newly_filled_entries.push((order.clone(), fill));
                            } else if order.kind == "TP" {
                                filled_tp_orders.push(order.clone());
                            }
                            state.active_orders.remove(i);
                            continue;
                        }
                        OrderResult::Failed(_) => {
                            if order.kind == "TP" || order.kind == "SL" {
                                warn!(
                                    "[{}] {} order {} was cancelled/rejected - position left without {} protection!",
                                    symbol, order.kind, order.id, order.kind
                                );
                            }
                            state.active_orders.remove(i);
                            continue;
                        }
                        _ => {}
                    }
                }
                i += 1;
            }
            // TP filled -> close one linked live trade as TAKE-PROFIT (FIFO).
            for tp in filled_tp_orders.drain(..) {
                if let Some(pos) = state
                    .active_live_trades
                    .iter()
                    .position(|t| t.size == tp.size)
                {
                    let t = state.active_live_trades.remove(pos);
                    let ntl = notional(tp.price, symbol_config.contract_size, t.size);
                    let fees = leg_fee(
                        notional(t.entry_price, symbol_config.contract_size, t.size),
                        false,
                    ) + leg_fee(ntl, true);
                    let slip = leg_slippage(
                        tp.price,
                        symbol_config.contract_size,
                        t.size,
                        symbol_config.tick_size,
                        0.0,
                    );
                    let gross = if t.side == "buy" {
                        (tp.price - t.entry_price) * t.size as f64 * symbol_config.contract_size
                    } else {
                        (t.entry_price - tp.price) * t.size as f64 * symbol_config.contract_size
                    };
                    session_tracker.write().record_trade(TradeRecord {
                        symbol: symbol.clone(),
                        entry_time: t.entry_time,
                        side: t.side,
                        entry_price: t.entry_price,
                        exit_price: tp.price,
                        exit_reason: "TAKE-PROFIT".to_string(),
                        gross_pnl: gross,
                        fees,
                        slippage: slip,
                        net_pnl: gross - fees - slip,
                        size: t.size,
                        contract_size: symbol_config.contract_size,
                    });
                    info!("[{}] TP filled; closed linked position", symbol);
                }
            }
        }

        for (order, fill) in newly_filled_entries {
            let tp_mult = symbol_config.take_profit_bps / BASIS_POINTS_DENOMINATOR;
            let sl_mult = symbol_config.stop_loss_bps / BASIS_POINTS_DENOMINATOR;
            let (take_profit_price, stop_loss_price) = if order.side == "buy" {
                (fill.price * (1.0 + tp_mult), fill.price * (1.0 - sl_mult))
            } else {
                (fill.price * (1.0 - tp_mult), fill.price * (1.0 + sl_mult))
            };

            state.active_live_trades.push(LiveTrade {
                product_id: symbol_config.product_id,
                entry_time: order.created_at,
                entry_price: fill.price,
                side: order.side.clone(),
                size: order.size,
                ticks_elapsed: 0,
                highest_price: fill.price,
                lowest_price: fill.price,
                order_id: Some(fill.order_id.clone()),
                fill_price: Some(fill.price),
                stop_loss_price,
                take_profit_price,
            });
            state.last_entry_tick = state.total_ticks;
            session_tracker.write().record_trade_entry();

            // Place Take Profit passive limit order at the basis-point target price
            let tp_price = take_profit_price;

            let close_side = if order.side == "buy" { "sell" } else { "buy" };
            match order_manager
                .place_order(
                    symbol_config.product_id,
                    Some(tp_price),
                    order.size,
                    close_side,
                    "limit",
                    None,
                )
                .await
            {
                Ok(
                    OrderResult::Open(tp_fill)
                    | OrderResult::Partial(tp_fill)
                    | OrderResult::Filled(tp_fill),
                ) => {
                    state.active_orders.push(ActiveOrder {
                        id: tp_fill.order_id,
                        price: tp_price,
                        side: close_side.to_string(),
                        size: order.size,
                        created_at: now_secs,
                        kind: "TP".to_string(),
                    });
                }
                Ok(OrderResult::Failed(e)) => {
                    warn!(
                        "[{}] Failed to place TP limit order: {}. Position will rely on trailing stop/timeout.",
                        symbol, e
                    );
                }
                Err(e) => {
                    warn!(
                        "[{}] Failed to place TP limit order: {}. Position will rely on trailing stop/timeout.",
                        symbol, e
                    );
                }
            }
        }

        if paper_trade_mode {
            let mut completed = Vec::new();
            state.active_paper_trades.retain_mut(|t| {
                t.ticks_elapsed += 1;
                t.highest_price = t.highest_price.max(price);
                t.lowest_price = t.lowest_price.min(price);

                let trailing_stop_distance = (symbol_config.trailing_stop_atr_mult * atr)
                    .max(symbol_config.min_trailing_stop_distance);
                let trailing_stop_activated = if t.side == "buy" {
                    t.highest_price - t.entry_price > trailing_stop_distance
                } else {
                    t.entry_price - t.lowest_price > trailing_stop_distance
                };
                let trailing_stop_price = if t.side == "buy" {
                    t.highest_price - trailing_stop_distance
                } else {
                    t.lowest_price + trailing_stop_distance
                };

                // Passive TP / SL limit orders checked at price level (basis points at entry)
                let hit_tp = if t.side == "buy" {
                    price >= t.take_profit_price
                } else {
                    price <= t.take_profit_price
                };
                let hit_sl = if t.side == "buy" {
                    price <= t.stop_loss_price
                } else {
                    price >= t.stop_loss_price
                };

                let should_exit = hit_sl
                    || hit_tp
                    || (trailing_stop_activated
                        && ((t.side == "buy" && price <= trailing_stop_price)
                            || (t.side == "sell" && price >= trailing_stop_price)))
                    || t.ticks_elapsed >= symbol_config.hold_ticks;

                if should_exit {
                    completed.push(t.clone());
                    false
                } else {
                    true
                }
            });

            for t in completed {
                let entry_ntl = notional(t.entry_price, symbol_config.contract_size, t.size);
                let exit_ntl = notional(price, symbol_config.contract_size, t.size);
                // Maker entry + maker TP assumed; SL/timeout legs pay taker in live.
                // Paper uses maker/maker as optimistic bound now that queue is modeled.
                let trade_fees = leg_fee(entry_ntl, true) + leg_fee(exit_ntl, true);
                let trade_slippage = leg_slippage(
                    price,
                    symbol_config.contract_size,
                    t.size,
                    symbol_config.tick_size,
                    slippage_bps,
                );
                let raw_gross_pnl = if t.side == "buy" {
                    (price - t.entry_price) * t.size as f64 * symbol_config.contract_size
                } else {
                    (t.entry_price - price) * t.size as f64 * symbol_config.contract_size
                };
                let net_pnl = raw_gross_pnl - trade_fees - trade_slippage;

                let ts_dist = (symbol_config.trailing_stop_atr_mult * state.curr_atr)
                    .max(symbol_config.min_trailing_stop_distance);
                let ts_activated = if t.side == "buy" {
                    t.highest_price - t.entry_price > ts_dist
                } else {
                    t.entry_price - t.lowest_price > ts_dist
                };
                let ts_price = if t.side == "buy" {
                    t.highest_price - ts_dist
                } else {
                    t.lowest_price + ts_dist
                };
                let hit_tp = if t.side == "buy" {
                    price >= t.take_profit_price
                } else {
                    price <= t.take_profit_price
                };
                let hit_sl = if t.side == "buy" {
                    price <= t.stop_loss_price
                } else {
                    price >= t.stop_loss_price
                };
                // Priority: STOP-LOSS > TAKE-PROFIT > TRAILING-STOP > TIMEOUT
                let exit_reason = if hit_sl {
                    "STOP-LOSS"
                } else if hit_tp {
                    "TAKE-PROFIT"
                } else if ts_activated
                    && ((t.side == "buy" && price <= ts_price)
                        || (t.side == "sell" && price >= ts_price))
                {
                    "TRAILING-STOP"
                } else {
                    "TIMEOUT"
                };

                let record = TradeRecord {
                    symbol: symbol.clone(),
                    entry_time: t.entry_time,
                    side: t.side.clone(),
                    entry_price: t.entry_price,
                    exit_price: price,
                    exit_reason: exit_reason.to_string(),
                    gross_pnl: raw_gross_pnl,
                    fees: trade_fees,
                    slippage: trade_slippage,
                    net_pnl,
                    size: t.size,
                    contract_size: symbol_config.contract_size,
                };
                if let Some(tx) = &telegram_tx {
                    let icon = if record.net_pnl > 0.0 { "+" } else { "-" };
                    let msg = format!("{} <b>POSITION CLOSED</b> | {}\nExit Reason: {}\nEntry: {:.5}\nExit: {:.5}\nNet PnL: {:.5} USD", icon, record.symbol, record.exit_reason, record.entry_price, record.exit_price, record.net_pnl);
                    let _ = tx.try_send(msg);
                }
                session_tracker.write().record_trade(record);

                info!(
                    "[{}] Paper trade closed [{}]. Entry: {:.5}, Exit: {:.5}, Net PnL: {:.5} USD",
                    symbol, exit_reason, t.entry_price, price, net_pnl
                );

                let log_line = format!(
                    "{},{},{},{},{},{},{:.5},{:.5},{:.5},{:.5}\n",
                    symbol,
                    t.entry_time,
                    t.side,
                    t.entry_price,
                    price,
                    exit_reason,
                    raw_gross_pnl,
                    trade_fees,
                    trade_slippage,
                    net_pnl
                );
                if let Some(ref mut writer) = csv_writer {
                    if writer.write_all(log_line.as_bytes()).is_err() {
                        error!("failed to journal paper trade");
                    } else if state.total_ticks % 50 == 0 {
                        let _ = writer.flush();
                    }
                }
            }
        } else {
            let mut completed = Vec::new();
            state.active_live_trades.retain_mut(|t| {
                t.ticks_elapsed += 1;
                t.highest_price = t.highest_price.max(price);
                t.lowest_price = t.lowest_price.min(price);
                let trailing_stop_distance = (symbol_config.trailing_stop_atr_mult * atr)
                    .max(symbol_config.min_trailing_stop_distance);
                let trailing_stop_activated = if t.side == "buy" {
                    t.highest_price - t.entry_price > trailing_stop_distance
                } else {
                    t.entry_price - t.lowest_price > trailing_stop_distance
                };
                let trailing_stop_price = if t.side == "buy" {
                    t.highest_price - trailing_stop_distance
                } else {
                    t.lowest_price + trailing_stop_distance
                };
                // We no longer check Take Profit here because it is a passive Limit Order
                let hit_sl = if t.side == "buy" {
                    price <= t.stop_loss_price
                } else {
                    price >= t.stop_loss_price
                };
                let should_exit = hit_sl
                    || (trailing_stop_activated
                        && ((t.side == "buy" && price <= trailing_stop_price)
                            || (t.side == "sell" && price >= trailing_stop_price)))
                    || t.ticks_elapsed >= symbol_config.hold_ticks;
                if should_exit {
                    completed.push(t.clone());
                    false
                } else {
                    true
                }
            });

            for mut t in completed {
                // Cancel any resting TP for this symbol so we don't double-close.
                let tp_ids: Vec<String> = state
                    .active_orders
                    .iter()
                    .filter(|o| o.kind == "TP")
                    .map(|o| o.id.clone())
                    .collect();
                if !tp_ids.is_empty() {
                    order_manager
                        .cancel_all_for_product(symbol_config.product_id, &tp_ids)
                        .await;
                    state.active_orders.retain(|o| o.kind != "TP");
                }
                let close_side = if t.side == "buy" { "sell" } else { "buy" };

                let hit_sl = if t.side == "buy" {
                    price <= t.stop_loss_price
                } else {
                    price >= t.stop_loss_price
                };
                let (order_type, order_price) = if hit_sl {
                    ("limit", Some(price)) // Stop Loss as Taker Limit
                } else {
                    ("market", None) // Trailing Stop / Timeout as Market
                };

                let order_result = if config.order_manager.track_fills {
                    order_manager
                        .place_order_with_retry(
                            symbol_config.product_id,
                            order_price,
                            t.size,
                            close_side,
                            order_type,
                            config.order_manager.max_retries,
                            config.order_manager.retry_base_delay_secs,
                            config.order_manager.retry_max_delay_secs,
                            &config.order_manager.retry_on_status,
                        )
                        .await
                } else {
                    match order_manager
                        .place_order(
                            symbol_config.product_id,
                            order_price,
                            t.size,
                            close_side,
                            order_type,
                            None,
                        )
                        .await
                    {
                        Ok(res) => res,
                        Err(e) => OrderResult::Failed(e),
                    }
                };

                let fill_price = match order_result {
                    OrderResult::Filled(fill) => {
                        if fill.filled_size != 0 && fill.filled_size != t.size {
                            warn!("[{}] close fill size {} != position {}; resizing TP already cancelled", symbol, fill.filled_size, t.size);
                            t.size = fill.filled_size;
                        }
                        t.fill_price = Some(fill.price);
                        fill.price
                    }
                    OrderResult::Partial(fill) => {
                        warn!("[{}] Partial fill for close order: {:?}", symbol, fill);
                        if fill.filled_size != 0 {
                            t.size = fill.filled_size;
                        }
                        t.fill_price = Some(fill.price);
                        fill.price
                    }
                    OrderResult::Open(fill) => {
                        error!(
                            "[{}] Close order unexpectedly returned Open state: {:?}",
                            symbol, fill
                        );
                        price // Fallback
                    }
                    OrderResult::Failed(e) => {
                        error!(
                            "[{}] CRITICAL: Failed to close position: {}. Exchange position may still be open!",
                            symbol, e
                        );
                        price
                    }
                };

                let entry_price = t.fill_price.unwrap_or(t.entry_price);
                let trade_fees = leg_fee(
                    notional(entry_price, symbol_config.contract_size, t.size),
                    false,
                ) + leg_fee(
                    notional(fill_price, symbol_config.contract_size, t.size),
                    false,
                );
                let trade_slippage = leg_slippage(
                    fill_price,
                    symbol_config.contract_size,
                    t.size,
                    symbol_config.tick_size,
                    slippage_bps,
                );
                let raw_gross_pnl = if t.side == "buy" {
                    (fill_price - entry_price) * t.size as f64 * symbol_config.contract_size
                } else {
                    (entry_price - fill_price) * t.size as f64 * symbol_config.contract_size
                };
                let net_pnl = raw_gross_pnl - trade_fees - trade_slippage;

                let ts_dist = (symbol_config.trailing_stop_atr_mult * state.curr_atr)
                    .max(symbol_config.min_trailing_stop_distance);
                let ts_activated = if t.side == "buy" {
                    t.highest_price - t.entry_price > ts_dist
                } else {
                    t.entry_price - t.lowest_price > ts_dist
                };
                let ts_price = if t.side == "buy" {
                    t.highest_price - ts_dist
                } else {
                    t.lowest_price + ts_dist
                };
                let hit_sl = if t.side == "buy" {
                    fill_price <= t.stop_loss_price
                } else {
                    fill_price >= t.stop_loss_price
                };
                let exit_reason = if hit_sl {
                    "STOP-LOSS"
                } else if ts_activated
                    && ((t.side == "buy" && fill_price <= ts_price)
                        || (t.side == "sell" && fill_price >= ts_price))
                {
                    "TRAILING-STOP"
                } else {
                    "TIMEOUT"
                };

                let record = TradeRecord {
                    symbol: symbol.clone(),
                    entry_time: t.entry_time,
                    side: t.side.clone(),
                    entry_price,
                    exit_price: fill_price,
                    exit_reason: exit_reason.to_string(),
                    gross_pnl: raw_gross_pnl,
                    fees: trade_fees,
                    slippage: trade_slippage,
                    net_pnl,
                    size: t.size,
                    contract_size: symbol_config.contract_size,
                };
                if let Some(tx) = &telegram_tx {
                    let icon = if record.net_pnl > 0.0 { "+" } else { "-" };
                    let msg = format!("{} <b>POSITION CLOSED</b> | {}\nExit Reason: {}\nEntry: {:.5}\nExit: {:.5}\nNet PnL: {:.5} USD", icon, record.symbol, record.exit_reason, record.entry_price, record.exit_price, record.net_pnl);
                    let _ = tx.try_send(msg);
                }
                session_tracker.write().record_trade(record);

                info!(
                    "[{}] Live trade closed [{}]. Entry: {:.5}, Exit: {:.5}, Est. Net PnL: {:.5} USD",
                    symbol, exit_reason, entry_price, fill_price, net_pnl
                );
                let log_line = format!(
                    "{},{},{},{},{},{},{:.5},{:.5},{:.5},{:.5}\n",
                    symbol,
                    t.entry_time,
                    t.side,
                    entry_price,
                    fill_price,
                    exit_reason,
                    raw_gross_pnl,
                    trade_fees,
                    trade_slippage,
                    net_pnl
                );
                if let Some(ref mut writer) = csv_writer {
                    let _ = writer.write_all(log_line.as_bytes());
                }
            }
        }

        let lookback = symbol_config.lookback_ticks;
        let mut outgoing_size = 0.0;
        let mut outgoing_size_sq = 0.0;
        if state.size_queue.len() >= lookback {
            let outgoing_idx = state.size_queue.len() - lookback;
            outgoing_size = state.size_queue[outgoing_idx];
            outgoing_size_sq = outgoing_size * outgoing_size;
            if outgoing_idx < state.side_queue.len() {
                let outgoing_side = state.side_queue[outgoing_idx];
                if outgoing_side > 0.0 {
                    state.rolling_buy_volume -= outgoing_size;
                } else {
                    state.rolling_sell_volume -= outgoing_size;
                }
            }
        }
        state.rolling_volume_sum = (state.rolling_volume_sum + size - outgoing_size).max(0.0);
        state.rolling_volume_sq_sum =
            (state.rolling_volume_sq_sum + size * size - outgoing_size_sq).max(0.0);
        state.rolling_buy_volume = state.rolling_buy_volume.max(0.0);
        state.rolling_sell_volume = state.rolling_sell_volume.max(0.0);

        let side_val = if trade.side.eq_ignore_ascii_case("buy") {
            1.0
        } else {
            -1.0
        };
        if side_val > 0.0 {
            state.rolling_buy_volume += size;
        } else {
            state.rolling_sell_volume += size;
        }

        if state.price_queue.len() == symbol_config.max_capacity {
            state.price_queue.pop_front();
        }
        if state.cvd_queue.len() == symbol_config.max_capacity {
            state.cvd_queue.pop_front();
        }
        if state.size_queue.len() == symbol_config.max_capacity {
            state.size_queue.pop_front();
        }
        if state.side_queue.len() == symbol_config.max_capacity {
            state.side_queue.pop_front();
        }

        state.price_queue.push_back(price);
        state.cvd_queue.push_back(state.current_cvd);
        state.size_queue.push_back(size);
        state.side_queue.push_back(side_val);

        let tick_is_buy = trade.side.eq_ignore_ascii_case("buy");
        if paper_trade_mode {
            update_pending_paper_orders(
                state,
                price,
                now_secs,
                15,
                tick_is_buy,
                size,
                symbol_config.contract_size,
            );
        }

        session_tracker.write().tick();

        // Global cap across all symbols (not per-symbol).
        let active_count: usize = others_count
            + if paper_trade_mode {
                state.active_paper_trades.len() + state.pending_paper_orders.len()
            } else {
                state.active_live_trades.len()
            };

        let current_len = state.price_queue.len();
        let mut script_buy = false;
        let mut script_sell = false;
        let past_index = current_len.saturating_sub(1 + symbol_config.lookback_ticks);
        let mut past_price_val = 0.0;
        let mut past_cvd_val = 0.0;
        if let (Some(&p), Some(&c)) = (
            state.price_queue.get(past_index),
            state.cvd_queue.get(past_index),
        ) {
            past_price_val = p;
            past_cvd_val = c;
        }
        let is_buy = trade.side.eq_ignore_ascii_case("buy");
        if let Some(Some(script_engine)) = scripts.get(&symbol) {
            if current_len > symbol_config.lookback_ticks {
                let mut script_ctx = crate::scripting::ScriptContext {
                    price,
                    size,
                    is_buy,
                    now_secs,
                    current_cvd: state.current_cvd,
                    p95_vol: state.cached_p95_volume,
                    atr,
                    rolling_volume: state.rolling_volume_sum,
                    past_price: past_price_val,
                    past_cvd: past_cvd_val,
                    ticks_since_entry: state.total_ticks.saturating_sub(state.last_entry_tick),
                    should_buy: false,
                    should_sell: false,
                };
                if let Err(e) = script_engine.execute_on_tick(&mut script_ctx) {
                    error!("Script execution error on {}: {}", symbol, e);
                }
                if script_ctx.should_buy && script_ctx.should_sell {
                    warn!("[{}] script set both buy+sell; ignoring signal", symbol);
                } else {
                    script_buy = script_ctx.should_buy;
                    script_sell = script_ctx.should_sell;
                }
            }
        }

        let mut want_buy = script_buy;
        let mut want_sell = script_sell;
        let delta_price = price - past_price_val;
        let cum_taker_volume = state.rolling_volume_sum;
        // Notional (USD) over the lookback window for the min-volume gate.
        let cum_notional = cum_taker_volume * price * symbol_config.contract_size;
        let price_impact = if cum_taker_volume > 0.0 {
            delta_price.abs() / cum_taker_volume
        } else {
            0.0
        };

        if !script_buy
            && !script_sell
            && symbol_config.strategy_type == "cvd_iceberg"
            && current_len > symbol_config.lookback_ticks
        {
            let volume_spike = size > state.cached_p95_volume;
            let cooldown_elapsed = state.total_ticks.saturating_sub(state.last_entry_tick)
                >= symbol_config.entry_cooldown_ticks;
            let can_absorb = !feed_is_stale
                && volume_spike
                && cum_notional > symbol_config.min_cvd_notional_usd
                && price_impact < symbol_config.max_price_impact_threshold
                && cooldown_elapsed;
            if can_absorb {
                if state.current_cvd > past_cvd_val && delta_price <= 0.0 {
                    want_sell = true;
                } else if state.current_cvd < past_cvd_val && delta_price >= 0.0 {
                    want_buy = true;
                }
            }
        }

        if (want_buy || want_sell) && !feed_is_stale {
            if active_count >= max_concurrent_positions {
                debug!(
                    "[{}] Signal ignored: max positions ({}) reached.",
                    symbol, max_concurrent_positions
                );
            } else {
                let side_str = if want_buy { "buy" } else { "sell" };
                info!("[{}] Strategy Signal {} @ {}, Delta Px: {:.5}, Cum Vol: {:.2}, Impact: {:.2e}, ATR: {:.5}", symbol, side_str, price, delta_price, cum_taker_volume, price_impact, atr);
                // Fixed-notional sizing ($2k default) instead of static dust lots.
                let order_size = contracts_for_notional(
                    price,
                    symbol_config.contract_size,
                    symbol_config.target_notional_usd,
                )
                .max(symbol_config.order_size);
                if let Some(tx) = &telegram_tx {
                    let mode = if paper_trade_mode { "PAPER" } else { "LIVE" };
                    let msg = format!(
                        "[ENTRY {}] | {} ({}) | Price: {:.5} | Size: {}",
                        side_str.to_uppercase(),
                        symbol,
                        mode,
                        price,
                        order_size
                    );
                    let _ = tx.try_send(msg);
                }
                let tp_mult = symbol_config.take_profit_bps / 10000.0;
                let sl_mult = symbol_config.stop_loss_bps / 10000.0;
                let (take_profit_price, stop_loss_price) = if want_buy {
                    (price * (1.0 + tp_mult), price * (1.0 - sl_mult))
                } else {
                    (price * (1.0 - tp_mult), price * (1.0 + sl_mult))
                };
                let now = now_secs;
                if paper_trade_mode {
                    // Maker queue: queue ahead = one order's base qty (conservative).
                    let order_base = order_size as f64 * symbol_config.contract_size;
                    state.pending_paper_orders.push(PendingPaperOrder {
                        entry_time: now,
                        limit_price: price,
                        side: side_str.to_string(),
                        size: order_size,
                        stop_loss_price,
                        take_profit_price,
                        ticks_held: 0,
                        queue_ahead_base: order_base,
                    });
                } else {
                    let order_result = order_manager
                        .place_order_with_retry(
                            symbol_config.product_id,
                            Some(price),
                            order_size,
                            side_str,
                            "limit",
                            config.order_manager.max_retries,
                            config.order_manager.retry_base_delay_secs,
                            config.order_manager.retry_max_delay_secs,
                            &config.order_manager.retry_on_status,
                        )
                        .await;

                    match order_result {
                        OrderResult::Open(fill)
                        | OrderResult::Partial(fill)
                        | OrderResult::Filled(fill) => {
                            state.active_orders.push(ActiveOrder {
                                id: fill.order_id,
                                price,
                                side: side_str.to_string(),
                                size: order_size,
                                created_at: now,
                                kind: "Entry".to_string(),
                            });
                        }
                        OrderResult::Failed(e) => {
                            error!("[{}] Failed to open position: {}", symbol, e);
                        }
                    }
                }
            }
        }
    }

    for (symbol, mut state) in states {
        if let Some(&last_price) = state.price_queue.back() {
            let Some(symbol_config) = config_map.get(&symbol).cloned() else {
                continue;
            };
            // Drop unfilled maker queue as CANCELLED (never entered).
            state.pending_paper_orders.clear();
            // Cancel resting exchange orders before force-closing.
            if !paper_trade_mode && !state.active_orders.is_empty() {
                let ids: Vec<String> = state.active_orders.iter().map(|o| o.id.clone()).collect();
                order_manager
                    .cancel_all_for_product(symbol_config.product_id, &ids)
                    .await;
                state.active_orders.clear();
            }
            if paper_trade_mode {
                for t in state.active_paper_trades.drain(..) {
                    let trade_fees = leg_fee(
                        notional(t.entry_price, symbol_config.contract_size, t.size),
                        true,
                    ) + leg_fee(
                        notional(last_price, symbol_config.contract_size, t.size),
                        true,
                    );
                    let trade_slippage = leg_slippage(
                        last_price,
                        symbol_config.contract_size,
                        t.size,
                        symbol_config.tick_size,
                        slippage_bps,
                    );
                    let raw_gross_pnl = if t.side == "buy" {
                        (last_price - t.entry_price) * t.size as f64 * symbol_config.contract_size
                    } else {
                        (t.entry_price - last_price) * t.size as f64 * symbol_config.contract_size
                    };
                    let net_pnl = raw_gross_pnl - trade_fees - trade_slippage;

                    let record = TradeRecord {
                        symbol: symbol.clone(),
                        entry_time: t.entry_time,
                        side: t.side.clone(),
                        entry_price: t.entry_price,
                        exit_price: last_price,
                        exit_reason: "SHUTDOWN".to_string(),
                        gross_pnl: raw_gross_pnl,
                        fees: trade_fees,
                        slippage: trade_slippage,
                        net_pnl,
                        size: t.size,
                        contract_size: symbol_config.contract_size,
                    };
                    if let Some(tx) = &telegram_tx {
                        let icon = if record.net_pnl > 0.0 { "+" } else { "-" };
                        let msg = format!("{} <b>POSITION CLOSED</b> | {}\nExit Reason: {}\nEntry: {:.5}\nExit: {:.5}\nNet PnL: {:.5} USD", icon, record.symbol, record.exit_reason, record.entry_price, record.exit_price, record.net_pnl);
                        let _ = tx.try_send(msg);
                    }
                    session_tracker.write().record_trade(record);

                    let log_line = format!(
                        "{},{},{},{},{},{},{:.5},{:.5},{:.5},{:.5}\n",
                        symbol,
                        t.entry_time,
                        t.side,
                        t.entry_price,
                        last_price,
                        "SHUTDOWN",
                        raw_gross_pnl,
                        trade_fees,
                        trade_slippage,
                        net_pnl
                    );
                    if let Some(ref mut writer) = csv_writer {
                        let _ = writer.write_all(log_line.as_bytes());
                    }
                }
            } else {
                for t in state.active_live_trades.drain(..) {
                    let close_side = if t.side == "buy" { "sell" } else { "buy" };
                    let (_order_id, fill_price) = if config.order_manager.track_fills {
                        let order_result = order_manager
                            .place_order_with_retry(
                                symbol_config.product_id,
                                None,
                                t.size,
                                close_side,
                                "market",
                                config.order_manager.max_retries,
                                config.order_manager.retry_base_delay_secs,
                                config.order_manager.retry_max_delay_secs,
                                &config.order_manager.retry_on_status,
                            )
                            .await;

                        match order_result {
                            OrderResult::Filled(fill) => {
                                if let Ok(fill_info) = order_manager
                                    .wait_for_fill(
                                        &fill.order_id,
                                        config.order_manager.fill_timeout_secs,
                                        SHUTDOWN_POLL_INTERVAL_MS,
                                    )
                                    .await
                                {
                                    (Some(fill.order_id), fill_info.price)
                                } else {
                                    warn!(
                                        "[{}] Fill confirmation timeout on shutdown, using order fill price",
                                        symbol
                                    );
                                    (Some(fill.order_id), fill.price)
                                }
                            }
                            OrderResult::Partial(fill) | OrderResult::Open(fill) => {
                                (Some(fill.order_id), fill.price)
                            }
                            OrderResult::Failed(e) => {
                                error!(
                                    "[{}] Failed to place close order on shutdown: {}",
                                    symbol, e
                                );
                                (None, last_price)
                            }
                        }
                    } else {
                        match order_manager
                            .place_order(
                                symbol_config.product_id,
                                None,
                                t.size,
                                close_side,
                                "market",
                                None,
                            )
                            .await
                        {
                            Ok(OrderResult::Filled(fill_info))
                            | Ok(OrderResult::Partial(fill_info))
                            | Ok(OrderResult::Open(fill_info)) => {
                                (Some(fill_info.order_id), fill_info.price)
                            }
                            Ok(OrderResult::Failed(e)) => {
                                error!(
                                    "[{}] Failed to place close order on shutdown: {}",
                                    symbol, e
                                );
                                (None, last_price)
                            }
                            Err(e) => {
                                error!(
                                    "[{}] Failed to place close order on shutdown: {}",
                                    symbol, e
                                );
                                (None, last_price)
                            }
                        }
                    };

                    let entry_price = t.fill_price.unwrap_or(t.entry_price);
                    let trade_fees = leg_fee(
                        notional(entry_price, symbol_config.contract_size, t.size),
                        false,
                    ) + leg_fee(
                        notional(fill_price, symbol_config.contract_size, t.size),
                        false,
                    );
                    let trade_slippage = leg_slippage(
                        fill_price,
                        symbol_config.contract_size,
                        t.size,
                        symbol_config.tick_size,
                        slippage_bps,
                    );
                    let raw_gross_pnl = if t.side == "buy" {
                        (fill_price - entry_price) * t.size as f64 * symbol_config.contract_size
                    } else {
                        (entry_price - fill_price) * t.size as f64 * symbol_config.contract_size
                    };
                    let net_pnl = raw_gross_pnl - trade_fees - trade_slippage;

                    let record = TradeRecord {
                        symbol: symbol.clone(),
                        entry_time: t.entry_time,
                        side: t.side.clone(),
                        entry_price,
                        exit_price: fill_price,
                        exit_reason: "SHUTDOWN".to_string(),
                        gross_pnl: raw_gross_pnl,
                        fees: trade_fees,
                        slippage: trade_slippage,
                        net_pnl,
                        size: t.size,
                        contract_size: symbol_config.contract_size,
                    };
                    if let Some(tx) = &telegram_tx {
                        let icon = if record.net_pnl > 0.0 { "+" } else { "-" };
                        let msg = format!("{} <b>POSITION CLOSED</b> | {}\nExit Reason: {}\nEntry: {:.5}\nExit: {:.5}\nNet PnL: {:.5} USD", icon, record.symbol, record.exit_reason, record.entry_price, record.exit_price, record.net_pnl);
                        let _ = tx.try_send(msg);
                    }
                    session_tracker.write().record_trade(record);

                    let log_line = format!(
                        "{},{},{},{},{},{},{:.5},{:.5},{:.5},{:.5}\n",
                        symbol,
                        t.entry_time,
                        t.side,
                        entry_price,
                        fill_price,
                        "SHUTDOWN",
                        raw_gross_pnl,
                        trade_fees,
                        trade_slippage,
                        net_pnl
                    );
                    if let Some(ref mut writer) = csv_writer {
                        let _ = writer.write_all(log_line.as_bytes());
                    }
                }
            }
        }
    }

    if let Some(mut writer) = csv_writer {
        let _ = writer.flush();
    }

    let tracker = session_tracker.read();
    tracker.print_summary();

    info!("Strategy engine shutting down gracefully.");
}
