use futures_util::{SinkExt, StreamExt};
use hmac::{Hmac, Mac};
use parking_lot::RwLock;
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use std::collections::{HashMap, VecDeque};
use std::fs::OpenOptions;
use std::io::{BufWriter, Write};
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio::sync::mpsc;
use tokio::time::interval;
use tokio_tungstenite::tungstenite::protocol::frame::coding::CloseCode;
use tokio_tungstenite::tungstenite::protocol::CloseFrame;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tracing::{debug, error, info, warn};
use tracing_subscriber::{fmt, layer::SubscriberExt, util::SubscriberInitExt, EnvFilter, Layer};

mod config;
mod session;
mod orders;

use config::{AppConfig, SymbolConfig, load_config};
use orders::{OrderManager, OrderResult};
use session::{SessionTracker, TradeRecord};

// Constants to replace magic numbers
const HEARTBEAT_TICK_INTERVAL: u64 = 200;
const BASIS_POINTS_DENOMINATOR: f64 = 10000.0;
const SHUTDOWN_POLL_INTERVAL_MS: u64 = 500;
const SHUTDOWN_BROADCAST_CAPACITY: usize = 16;
const HEARTBEAT_INTERVAL_SECS: u64 = 60;

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

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct TradeMessage {
    pub r#type: String,
    #[serde(default)]
    pub p: Option<String>,
    #[serde(default)]
    pub s: Option<f64>,
    #[serde(default)]
    pub r: Option<String>,
    #[serde(default)]
    pub side: Option<String>,
    #[serde(default)]
    pub sy: Option<String>,
    #[serde(default)]
    pub t: Option<u64>,
    #[serde(default)]
    pub product_id: Option<u64>,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct TradeEvent {
    pub price: String,
    pub size: String,
    pub side: String,
    pub symbol: String,
    pub product_id: Option<u64>,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct OrderRequest {
    pub product_id: u64,
    pub size: i64,
    pub side: String,
    pub order_type: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub price: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub post_only: Option<String>,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct OrderResponse {
    pub id: String,
    pub product_id: u64,
    pub size: i64,
    pub side: String,
    pub order_type: String,
    pub status: String,
    pub filled_size: Option<i64>,
    pub avg_fill_price: Option<String>,
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
    pub active_live_trades: Vec<LiveTrade>,
    pub active_orders: Vec<ActiveOrder>,
    pub rolling_volume_sum: f64,
    pub rolling_volume_sq_sum: f64,
    pub rolling_buy_volume: f64,
    pub rolling_sell_volume: f64,
    // MACD & Trend Filter state fields
    pub candle_5m_closes: VecDeque<f64>,
    pub candle_15m_closes: VecDeque<f64>,
    pub current_5m_start_ts: u64,
    pub current_15m_start_ts: u64,
    pub ema_fast_val: Option<f64>,
    pub ema_slow_val: Option<f64>,
    pub ema_signal_val: Option<f64>,
    pub ema_200_trend_val: Option<f64>,
    pub prev_macd_pct: f64,
    pub curr_macd_pct: f64,
}

impl SymbolState {
    fn new(max_capacity: usize) -> Self {
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
            active_live_trades: Vec::new(),
            active_orders: Vec::new(),
            rolling_volume_sum: 0.0,
            rolling_volume_sq_sum: 0.0,
            rolling_buy_volume: 0.0,
            rolling_sell_volume: 0.0,
            candle_5m_closes: VecDeque::with_capacity(1000),
            candle_15m_closes: VecDeque::with_capacity(1000),
            current_5m_start_ts: 0,
            current_15m_start_ts: 0,
            ema_fast_val: None,
            ema_slow_val: None,
            ema_signal_val: None,
            ema_200_trend_val: None,
            prev_macd_pct: 0.0,
            curr_macd_pct: 0.0,
        }
    }
}

type HmacSha256 = Hmac<Sha256>;

pub fn generate_signature(
    api_secret: &str,
    method: &str,
    timestamp: &str,
    path: &str,
    payload_str: &str,
) -> String {
    let signature_data = format!("{}{}{}{}", method, timestamp, path, payload_str);
    let mut mac = HmacSha256::new_from_slice(api_secret.as_bytes())
        .expect("HMAC can take key of any size");
    mac.update(signature_data.as_bytes());
    let result = mac.finalize();
    let code_bytes = result.into_bytes();
    hex::encode(code_bytes)
}

fn compute_atr(price_queue: &VecDeque<f64>, period: usize) -> f64 {
    if price_queue.len() < period + 1 {
        return 1.0;
    }
    let len = price_queue.len();
    let tr_sum: f64 = (len - period..len)
        .map(|i| (price_queue[i] - price_queue[i - 1]).abs())
        .sum();
    tr_sum / period as f64
}

/// Decode the trade direction from a Delta `trades` channel payload.
///
/// Delta encodes `r` as the **buyer** role, NOT the trade direction:
///   * `"t"` / `"taker"`  -> the buyer was the aggressor  -> BUY  flow (CVD += size)
///   * `"m"` / `"maker"`  -> the seller was the aggressor -> SELL flow (CVD -= size)
///
/// Newer payloads may carry an explicit `side` ("buy"/"sell"); when present it is
/// treated as the authoritative aggressor direction and overrides `r`.
fn decode_trade_side(role: Option<&str>, side: Option<&str>) -> &'static str {
    if let Some(s) = side {
        match s.to_ascii_lowercase().as_str() {
            "buy" => return "buy",
            "sell" => return "sell",
            _ => {}
        }
    }
    match role.map(|r| r.to_ascii_lowercase()).as_deref() {
        Some("t") | Some("taker") => "buy",
        Some("m") | Some("maker") => "sell",
        _ => "sell",
    }
}

fn compute_rolling_p95(buffer: &VecDeque<f64>) -> f64 {
    if buffer.len() < 10 {
        return f64::INFINITY;
    }
    let mut sorted: Vec<f64> = buffer.iter().cloned().collect();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let idx = (sorted.len() as f64 * 0.95) as usize;
    sorted[idx.min(sorted.len() - 1)]
}

pub async fn run_strategy_engine(
    mut trade_rx: mpsc::Receiver<TradeEvent>,
    order_manager: Arc<OrderManager>,
    session_tracker: Arc<RwLock<SessionTracker>>,
    config: Arc<AppConfig>,
    mut shutdown_rx: tokio::sync::broadcast::Receiver<()>,
) {
    let config_map: HashMap<String, SymbolConfig> = config.strategy.symbols
        .iter()
        .map(|c| (c.symbol.clone(), c.clone()))
        .collect();
    let mut states: HashMap<String, SymbolState> = HashMap::new();

    let paper_trade_mode = config.general.paper_trade_mode;
    let taker_fee_rate = config.fees.taker_fee_rate;
    let slippage_bps = config.fees.slippage_bps;
    let max_concurrent_positions = config.general.max_concurrent_positions;

    std::fs::create_dir_all(&config.general.trades_dir).ok();
    let session_filename = format!(
        "{}/{}.csv",
        config.general.trades_dir,
        chrono::Local::now().format("%Y-%m-%d_%H-%M-%S")
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
        config.strategy.symbols.iter().map(|c| &c.symbol).collect::<Vec<_>>()
    );

    loop {
        let trade = tokio::select! {
            trade_result = trade_rx.recv() => {
                match trade_result {
                    Some(t) => t,
                    None => break,
                }
            }
            _ = shutdown_rx.recv() => {
                info!("Strategy engine received shutdown signal.");
                break;
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
            None => continue,
        };

        let state = states.entry(symbol.clone()).or_insert_with(|| SymbolState::new(symbol_config.max_capacity));

        if trade.side.eq_ignore_ascii_case("buy") {
            state.current_cvd += size;
        } else if trade.side.eq_ignore_ascii_case("sell") {
            state.current_cvd -= size;
        } else {
            continue;
        }

        state.total_ticks += 1;
        if state.total_ticks.is_multiple_of(HEARTBEAT_TICK_INTERVAL) {
            let active_count = if paper_trade_mode {
                state.active_paper_trades.len()
            } else {
                state.active_live_trades.len()
            };
            debug!(
                "[{}] Tick #{} | Price: {} | CVD: {:.5} | Queue: {}/{} | Active: {}",
                symbol, state.total_ticks, price, state.current_cvd,
                state.price_queue.len(), symbol_config.max_capacity, active_count
            );
        }

        let atr = compute_atr(&state.price_queue, symbol_config.atr_period).max(symbol_config.tick_size);
        let now_secs = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();
        let mut newly_filled_entries = Vec::new();
        if !paper_trade_mode {
            let mut i = 0;
            while i < state.active_orders.len() {
                let order = &state.active_orders[i];
                if now_secs.saturating_sub(order.created_at) > 5 && order.kind == "Entry" {
                    warn!("[{}] Entry order {} timed out after 5s. Cancelling.", symbol, order.id);
                    let _ = order_manager.cancel_order(symbol_config.product_id, &order.id).await;
                    state.active_orders.remove(i);
                    continue;
                }
                
                // Poll status
                if let Ok(status) = order_manager.get_order_status(&order.id).await {
                    match status {
                        OrderResult::Filled(fill) => {
                            info!("[{}] Order {} ({}) filled at {}", symbol, order.id, order.kind, fill.price);
                            if order.kind == "Entry" {
                                newly_filled_entries.push((order.clone(), fill));
                            }
                            state.active_orders.remove(i);
                            continue;
                        }
                        OrderResult::Failed(_) => {
                            if order.kind == "TP" || order.kind == "SL" {
                                warn!("[{}] {} order {} was cancelled/rejected - position left without {} protection!", symbol, order.kind, order.id, order.kind);
                            }
                            state.active_orders.remove(i);
                            continue;
                        }
                        _ => {}
                    }
                }
                i += 1;
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
            match order_manager.place_order(symbol_config.product_id, Some(tp_price), order.size, close_side, "limit").await {
                Ok(OrderResult::Open(tp_fill) | OrderResult::Partial(tp_fill) | OrderResult::Filled(tp_fill)) => {
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
                    warn!("[{}] Failed to place TP limit order: {}. Position will rely on trailing stop/timeout.", symbol, e);
                }
                Err(e) => {
                    warn!("[{}] Failed to place TP limit order: {}. Position will rely on trailing stop/timeout.", symbol, e);
                }
            }
        }

        if paper_trade_mode {
            let mut completed = Vec::new();
            state.active_paper_trades.retain_mut(|t| {
                t.ticks_elapsed += 1;
                t.highest_price = t.highest_price.max(price);
                t.lowest_price = t.lowest_price.min(price);

                let trailing_stop_distance = (symbol_config.trailing_stop_atr_mult * atr).max(symbol_config.min_trailing_stop_distance);
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
                    || (trailing_stop_activated && (
                        (t.side == "buy" && price <= trailing_stop_price) ||
                        (t.side == "sell" && price >= trailing_stop_price)
                    ))
                    || t.ticks_elapsed >= symbol_config.hold_ticks;

                if should_exit { completed.push(t.clone()); false } else { true }
            });

            for t in completed {
                let size_btc = t.size as f64 * symbol_config.contract_size;
                let entry_fee = t.entry_price * taker_fee_rate * size_btc;
                let exit_fee = price * taker_fee_rate * size_btc;
                let trade_fees = entry_fee + exit_fee;
                let trade_slippage = price * (slippage_bps / BASIS_POINTS_DENOMINATOR) * size_btc;
                let raw_gross_pnl = if t.side == "buy" { (price - t.entry_price) * size_btc } else { (t.entry_price - price) * size_btc };
                let net_pnl = raw_gross_pnl - trade_fees - trade_slippage;

                let ts_dist = (symbol_config.trailing_stop_atr_mult * compute_atr(&state.price_queue, symbol_config.atr_period)).max(symbol_config.min_trailing_stop_distance);
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
                let hit_tp = if t.side == "buy" { price >= t.take_profit_price } else { price <= t.take_profit_price };
                let hit_sl = if t.side == "buy" { price <= t.stop_loss_price } else { price >= t.stop_loss_price };
                let exit_reason = if t.ticks_elapsed >= symbol_config.hold_ticks { "TIMEOUT" }
                    else if hit_sl { "STOP-LOSS" }
                    else if hit_tp { "TAKE-PROFIT" }
                    else if ts_activated && (
                        (t.side == "buy" && price <= ts_price) ||
                        (t.side == "sell" && price >= ts_price)
                    ) { "TRAILING-STOP" }
                    else { "TIMEOUT" };

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
                session_tracker.write().record_trade(record);

                info!(
                    "[{}] Paper trade closed [{}]. Entry: {:.5}, Exit: {:.5}, Net PnL: {:.5} USD",
                    symbol, exit_reason, t.entry_price, price, net_pnl
                );

                let log_line = format!(
                    "{},{},{},{},{},{},{:.5},{:.5},{:.5},{:.5}\n",
                    symbol, t.entry_time, t.side, t.entry_price, price, exit_reason,
                    raw_gross_pnl, trade_fees, trade_slippage, net_pnl
                );
                if let Some(ref mut writer) = csv_writer { let _ = writer.write_all(log_line.as_bytes()); }
            }
        } else {
            let mut completed = Vec::new();
            state.active_live_trades.retain_mut(|t| {
                t.ticks_elapsed += 1;
                t.highest_price = t.highest_price.max(price);
                t.lowest_price = t.lowest_price.min(price);
                let trailing_stop_distance = (symbol_config.trailing_stop_atr_mult * atr).max(symbol_config.min_trailing_stop_distance);
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
                    || (trailing_stop_activated && (
                        (t.side == "buy" && price <= trailing_stop_price) ||
                        (t.side == "sell" && price >= trailing_stop_price)
                    ))
                    || t.ticks_elapsed >= symbol_config.hold_ticks;
                if should_exit { completed.push(t.clone()); false } else { true }
            });

            for mut t in completed {
                let close_side = if t.side == "buy" { "sell" } else { "buy" };

                let hit_sl = if t.side == "buy" { price <= t.stop_loss_price } else { price >= t.stop_loss_price };
                let (order_type, order_price) = if hit_sl {
                    ("limit", Some(price)) // Stop Loss as Taker Limit
                } else {
                    ("market", None) // Trailing Stop / Timeout as Market
                };
                
                let order_result = if config.order_manager.track_fills {
                    order_manager.place_order_with_retry(
                        symbol_config.product_id,
                        order_price,
                        t.size,
                        close_side,
                        order_type,
                        config.order_manager.max_retries,
                        config.order_manager.retry_base_delay_secs,
                        config.order_manager.retry_max_delay_secs,
                        &config.order_manager.retry_on_status,
                    ).await
                } else {
                    match order_manager.place_order(
                        symbol_config.product_id,
                        order_price,
                        t.size,
                        close_side,
                        order_type,
                    ).await {
                        Ok(res) => res,
                        Err(e) => OrderResult::Failed(e),
                    }
                };

                let fill_price = match order_result {
                    OrderResult::Filled(fill) => {
                        t.fill_price = Some(fill.price);
                        fill.price
                    }
                    OrderResult::Partial(fill) => {
                        warn!("[{}] Partial fill for close order: {:?}", symbol, fill);
                        t.fill_price = Some(fill.price);
                        fill.price
                    }
                    OrderResult::Open(fill) => {
                        error!("[{}] Close order unexpectedly returned Open state: {:?}", symbol, fill);
                        price // Fallback
                    }
                    OrderResult::Failed(e) => {
                        error!("[{}] CRITICAL: Failed to close position: {}. Exchange position may still be open!", symbol, e);
                        price
                    }
                };

                let size_btc = t.size as f64 * symbol_config.contract_size;
                let entry_price = t.fill_price.unwrap_or(t.entry_price);
                let entry_fee = entry_price * taker_fee_rate * size_btc;
                let exit_fee = fill_price * taker_fee_rate * size_btc;
                let trade_fees = entry_fee + exit_fee;
                let trade_slippage = fill_price * (slippage_bps / BASIS_POINTS_DENOMINATOR) * size_btc;
                let raw_gross_pnl = if t.side == "buy" { (fill_price - entry_price) * size_btc } else { (entry_price - fill_price) * size_btc };
                let net_pnl = raw_gross_pnl - trade_fees - trade_slippage;

                let ts_dist = (symbol_config.trailing_stop_atr_mult * compute_atr(&state.price_queue, symbol_config.atr_period)).max(symbol_config.min_trailing_stop_distance);
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
                let hit_sl = if t.side == "buy" { fill_price <= t.stop_loss_price } else { fill_price >= t.stop_loss_price };
                let exit_reason = if t.ticks_elapsed >= symbol_config.hold_ticks { "TIMEOUT" }
                    else if hit_sl { "STOP-LOSS" }
                    else if ts_activated && (
                        (t.side == "buy" && fill_price <= ts_price) ||
                        (t.side == "sell" && fill_price >= ts_price)
                    ) { "TRAILING-STOP" }
                    else { "TIMEOUT" };

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
                session_tracker.write().record_trade(record);

                info!(
                    "[{}] Live trade closed [{}]. Entry: {:.5}, Exit: {:.5}, Est. Net PnL: {:.5} USD",
                    symbol, exit_reason, entry_price, fill_price, net_pnl
                );
                let log_line = format!(
                    "{},{},{},{},{},{},{:.5},{:.5},{:.5},{:.5}\n",
                    symbol, t.entry_time, t.side, entry_price, fill_price, exit_reason,
                    raw_gross_pnl, trade_fees, trade_slippage, net_pnl
                );
                if let Some(ref mut writer) = csv_writer { let _ = writer.write_all(log_line.as_bytes()); }
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
        state.rolling_volume_sum += size - outgoing_size;
        state.rolling_volume_sq_sum += size * size - outgoing_size_sq;

        let side_val = if trade.side.eq_ignore_ascii_case("buy") { 1.0 } else { -1.0 };
        if side_val > 0.0 {
            state.rolling_buy_volume += size;
        } else {
            state.rolling_sell_volume += size;
        }

        if state.price_queue.len() == symbol_config.max_capacity { state.price_queue.pop_front(); }
        if state.cvd_queue.len() == symbol_config.max_capacity { state.cvd_queue.pop_front(); }
        if state.size_queue.len() == symbol_config.max_capacity { state.size_queue.pop_front(); }
        if state.side_queue.len() == symbol_config.max_capacity { state.side_queue.pop_front(); }

        state.price_queue.push_back(price);
        state.cvd_queue.push_back(state.current_cvd);
        state.size_queue.push_back(size);
        state.side_queue.push_back(side_val);

        if state.volume_buffer.len() >= 1000 {
            state.volume_buffer.pop_front();
        }
        state.volume_buffer.push_back(size);

        // Block-cached 95th percentile volume update (every 500 ticks or on initial start)
        state.p95_counter += 1;
        if state.p95_counter >= 500 || state.cached_p95_volume.is_infinite() {
            if state.volume_buffer.len() >= 10 {
                state.cached_p95_volume = compute_rolling_p95(&state.volume_buffer);
                state.p95_counter = 0;
            }
        }

        // --- MACD Momentum Strategy & Trend Filter Calculations ---
        let macd_cfg = config.macd_momentum.as_ref();
        let macd_fast = symbol_config.macd_fast.unwrap_or_else(|| macd_cfg.map(|c| c.macd_fast).unwrap_or(12));
        let macd_slow = symbol_config.macd_slow.unwrap_or_else(|| macd_cfg.map(|c| c.macd_slow).unwrap_or(26));
        let macd_signal = symbol_config.macd_signal.unwrap_or_else(|| macd_cfg.map(|c| c.macd_signal).unwrap_or(9));
        let ema_filter = symbol_config.ema_filter.unwrap_or_else(|| macd_cfg.map(|c| c.ema_filter).unwrap_or(200));
        let norm_threshold_pct = symbol_config.norm_threshold_pct.unwrap_or_else(|| macd_cfg.map(|c| c.norm_threshold_pct).unwrap_or(0.15));
        let sl_atr_mult = symbol_config.sl_atr_mult.unwrap_or_else(|| macd_cfg.map(|c| c.sl_atr_mult).unwrap_or(1.5));
        let risk_reward_ratio = symbol_config.risk_reward_ratio.unwrap_or_else(|| macd_cfg.map(|c| c.risk_reward_ratio).unwrap_or(1.5));
        let candle_mins = symbol_config.candle_interval_mins.unwrap_or_else(|| macd_cfg.map(|c| c.candle_interval_mins).unwrap_or(5));
        let trend_mins = symbol_config.trend_filter_interval_mins.unwrap_or_else(|| macd_cfg.map(|c| c.trend_filter_interval_mins).unwrap_or(15));
        let macd_cooldown_ticks = symbol_config.entry_cooldown_ticks;

        let alpha_fast = 2.0 / (macd_fast as f64 + 1.0);
        let alpha_slow = 2.0 / (macd_slow as f64 + 1.0);
        let alpha_signal = 2.0 / (macd_signal as f64 + 1.0);
        let alpha_200 = 2.0 / (ema_filter as f64 + 1.0);

        let trade_ts_sec = now_secs;
        let interval_15m_sec = (trend_mins as u64) * 60;
        let bucket_15m = (trade_ts_sec / interval_15m_sec) * interval_15m_sec;

        if state.current_15m_start_ts == 0 {
            state.current_15m_start_ts = bucket_15m;
            state.ema_200_trend_val = Some(price);
        } else if bucket_15m > state.current_15m_start_ts || state.ema_200_trend_val.is_none() {
            state.current_15m_start_ts = bucket_15m;
            let next_200 = match state.ema_200_trend_val {
                Some(prev) => price * alpha_200 + prev * (1.0 - alpha_200),
                None => price,
            };
            state.ema_200_trend_val = Some(next_200);
        }

        let interval_5m_sec = (candle_mins as u64) * 60;
        let bucket_5m = (trade_ts_sec / interval_5m_sec) * interval_5m_sec;

        if state.current_5m_start_ts == 0 || state.ema_fast_val.is_none() {
            state.current_5m_start_ts = bucket_5m;
            state.ema_fast_val = Some(price);
            state.ema_slow_val = Some(price);
            state.ema_signal_val = Some(0.0);
        } else {
            let next_fast = price * alpha_fast + state.ema_fast_val.unwrap() * (1.0 - alpha_fast);
            let next_slow = price * alpha_slow + state.ema_slow_val.unwrap() * (1.0 - alpha_slow);
            state.ema_fast_val = Some(next_fast);
            state.ema_slow_val = Some(next_slow);
        }

        let macd_line = state.ema_fast_val.unwrap() - state.ema_slow_val.unwrap();
        let macd_pct = (macd_line / price) * 100.0;

        let next_signal = match state.ema_signal_val {
            Some(prev) => macd_line * alpha_signal + prev * (1.0 - alpha_signal),
            None => macd_line,
        };
        state.ema_signal_val = Some(next_signal);

        state.prev_macd_pct = state.curr_macd_pct;
        state.curr_macd_pct = macd_pct;

        let active_count = if paper_trade_mode { state.active_paper_trades.len() } else { state.active_live_trades.len() };
        let cooldown_elapsed = state.total_ticks.saturating_sub(state.last_entry_tick) >= macd_cooldown_ticks;
        let is_macd_strategy = symbol_config.strategy_type == "macd_momentum"
            || (symbol_config.strategy_type != "cvd_iceberg" && macd_cfg.map(|c| c.enabled).unwrap_or(false));

        if is_macd_strategy && cooldown_elapsed && active_count < max_concurrent_positions {
            let ema_200 = state.ema_200_trend_val.unwrap_or(price);

            // Go Long: MACD_% crosses ABOVE +X% threshold AND Price > 200 EMA
            let long_signal = state.prev_macd_pct <= norm_threshold_pct
                && state.curr_macd_pct > norm_threshold_pct
                && price > ema_200;

            // Go Short: MACD_% crosses BELOW -X% threshold AND Price < 200 EMA
            let short_signal = state.prev_macd_pct >= -norm_threshold_pct
                && state.curr_macd_pct < -norm_threshold_pct
                && price < ema_200;

            if long_signal {
                let sl_dist = sl_atr_mult * atr;
                let tp_dist = sl_dist * risk_reward_ratio;
                let stop_loss_price = price - sl_dist;
                let take_profit_price = price + tp_dist;
                let order_size = symbol_config.order_size;
                let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();

                info!(
                    "[{}] MACD Momentum Long @ {}, MACD_%: {:.4}%, EMA200: {:.5}, SL: {:.5}, TP: {:.5}, ATR: {:.5}",
                    symbol, price, state.curr_macd_pct, ema_200, stop_loss_price, take_profit_price, atr
                );

                if paper_trade_mode {
                    state.active_paper_trades.push(PaperTrade {
                        entry_time: now, entry_price: price, side: "buy".to_string(),
                        size: order_size, ticks_elapsed: 0, highest_price: price, lowest_price: price,
                        stop_loss_price, take_profit_price,
                    });
                    state.last_entry_tick = state.total_ticks;
                } else {
                    let order_result = order_manager.place_order_with_retry(
                        symbol_config.product_id,
                        Some(price),
                        order_size,
                        "buy",
                        "limit",
                        config.order_manager.max_retries,
                        config.order_manager.retry_base_delay_secs,
                        config.order_manager.retry_max_delay_secs,
                        &config.order_manager.retry_on_status,
                    ).await;

                    match order_result {
                        OrderResult::Open(fill) | OrderResult::Partial(fill) | OrderResult::Filled(fill) => {
                            state.active_orders.push(ActiveOrder {
                                id: fill.order_id, price, side: "buy".to_string(), size: order_size,
                                created_at: now, kind: "Entry".to_string(),
                            });
                            state.last_entry_tick = state.total_ticks;
                        }
                        OrderResult::Failed(e) => {
                            error!("[{}] Failed to open MACD Long position: {}", symbol, e);
                        }
                    }
                }
            } else if short_signal {
                let sl_dist = sl_atr_mult * atr;
                let tp_dist = sl_dist * risk_reward_ratio;
                let stop_loss_price = price + sl_dist;
                let take_profit_price = price - tp_dist;
                let order_size = symbol_config.order_size;
                let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();

                info!(
                    "[{}] MACD Momentum Short @ {}, MACD_%: {:.4}%, EMA200: {:.5}, SL: {:.5}, TP: {:.5}, ATR: {:.5}",
                    symbol, price, state.curr_macd_pct, ema_200, stop_loss_price, take_profit_price, atr
                );

                if paper_trade_mode {
                    state.active_paper_trades.push(PaperTrade {
                        entry_time: now, entry_price: price, side: "sell".to_string(),
                        size: order_size, ticks_elapsed: 0, highest_price: price, lowest_price: price,
                        stop_loss_price, take_profit_price,
                    });
                    state.last_entry_tick = state.total_ticks;
                } else {
                    let order_result = order_manager.place_order_with_retry(
                        symbol_config.product_id,
                        Some(price),
                        order_size,
                        "sell",
                        "limit",
                        config.order_manager.max_retries,
                        config.order_manager.retry_base_delay_secs,
                        config.order_manager.retry_max_delay_secs,
                        &config.order_manager.retry_on_status,
                    ).await;

                    match order_result {
                        OrderResult::Open(fill) | OrderResult::Partial(fill) | OrderResult::Filled(fill) => {
                            state.active_orders.push(ActiveOrder {
                                id: fill.order_id, price, side: "sell".to_string(), size: order_size,
                                created_at: now, kind: "Entry".to_string(),
                            });
                            state.last_entry_tick = state.total_ticks;
                        }
                        OrderResult::Failed(e) => {
                            error!("[{}] Failed to open MACD Short position: {}", symbol, e);
                        }
                    }
                }
            }
        }

        let current_len = state.price_queue.len();
        if symbol_config.strategy_type == "cvd_iceberg" && current_len > symbol_config.lookback_ticks {
            let past_index = current_len - 1 - symbol_config.lookback_ticks;
            if let (Some(&past_price), Some(&past_cvd)) =
                (state.price_queue.get(past_index), state.cvd_queue.get(past_index))
            {
                let delta_price = price - past_price;
                let cum_taker_volume = state.rolling_volume_sum;
                let price_impact = if cum_taker_volume > 0.0 {
                    delta_price.abs() / cum_taker_volume
                } else {
                    0.0
                };
                let volume_spike = size > state.cached_p95_volume;
                let cooldown_elapsed = state.total_ticks.saturating_sub(state.last_entry_tick) >= symbol_config.entry_cooldown_ticks;

                let can_absorb = volume_spike
                    && cum_taker_volume > symbol_config.min_cvd_notional_usd
                    && price_impact < symbol_config.max_price_impact_threshold
                    && cooldown_elapsed;

                if can_absorb {
                    if state.current_cvd > past_cvd && delta_price <= 0.0 {
                        // Aggressive BUYING but price hit a ceiling -> fade SHORT
                        if active_count >= max_concurrent_positions {
                            debug!("[{}] Short signal ignored: max positions ({}) reached.", symbol, max_concurrent_positions);
                        } else {
                            info!(
                                "[{}] Institutional Iceberg-Absorption Short @ {}, Delta Px: {:.5}, Cum Vol: {:.2}, Impact: {:.2e}, ATR: {:.5}",
                                symbol, price, delta_price, cum_taker_volume, price_impact, atr
                            );
                            let order_size = symbol_config.order_size;
                            let tp_mult = symbol_config.take_profit_bps / BASIS_POINTS_DENOMINATOR;
                            let sl_mult = symbol_config.stop_loss_bps / BASIS_POINTS_DENOMINATOR;
                            let take_profit_price = price * (1.0 - tp_mult);
                            let stop_loss_price = price * (1.0 + sl_mult);
                            let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();
                            if paper_trade_mode {
                                state.active_paper_trades.push(PaperTrade {
                                    entry_time: now, entry_price: price, side: "sell".to_string(),
                                    size: order_size, ticks_elapsed: 0, highest_price: price, lowest_price: price,
                                    stop_loss_price, take_profit_price,
                                });
                            } else {
                                let order_result = order_manager.place_order_with_retry(
                                    symbol_config.product_id,
                                    Some(price), // Use current price as Best Ask
                                    order_size,
                                    "sell",
                                    "limit",
                                    config.order_manager.max_retries,
                                    config.order_manager.retry_base_delay_secs,
                                    config.order_manager.retry_max_delay_secs,
                                    &config.order_manager.retry_on_status,
                                ).await;

                                match order_result {
                                    OrderResult::Open(fill) | OrderResult::Partial(fill) | OrderResult::Filled(fill) => {
                                        state.active_orders.push(ActiveOrder {
                                            id: fill.order_id,
                                            price,
                                            side: "sell".to_string(),
                                            size: order_size,
                                            created_at: now,
                                            kind: "Entry".to_string(),
                                        });
                                    }
                                    OrderResult::Failed(e) => {
                                        error!("[{}] Failed to open short position: {}", symbol, e);
                                    }
                                }
                            }
                        }
                    } else if state.current_cvd < past_cvd && delta_price >= 0.0 {
                        // Aggressive SELLING but price hit a floor -> fade LONG
                        if active_count >= max_concurrent_positions {
                            debug!("[{}] Long signal ignored: max positions ({}) reached.", symbol, max_concurrent_positions);
                        } else {
                            info!(
                                "[{}] Institutional Iceberg-Absorption Long @ {}, Delta Px: {:.5}, Cum Vol: {:.2}, Impact: {:.2e}, ATR: {:.5}",
                                symbol, price, delta_price, cum_taker_volume, price_impact, atr
                            );
                            let order_size = symbol_config.order_size;
                            let tp_mult = symbol_config.take_profit_bps / BASIS_POINTS_DENOMINATOR;
                            let sl_mult = symbol_config.stop_loss_bps / BASIS_POINTS_DENOMINATOR;
                            let take_profit_price = price * (1.0 + tp_mult);
                            let stop_loss_price = price * (1.0 - sl_mult);
                            let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();
                            if paper_trade_mode {
                                state.active_paper_trades.push(PaperTrade {
                                    entry_time: now, entry_price: price, side: "buy".to_string(),
                                    size: order_size, ticks_elapsed: 0, highest_price: price, lowest_price: price,
                                    stop_loss_price, take_profit_price,
                                });
                            } else {
                                let order_result = order_manager.place_order_with_retry(
                                    symbol_config.product_id,
                                    Some(price), // Use current price as Best Bid
                                    order_size,
                                    "buy",
                                    "limit",
                                    config.order_manager.max_retries,
                                    config.order_manager.retry_base_delay_secs,
                                    config.order_manager.retry_max_delay_secs,
                                    &config.order_manager.retry_on_status,
                                ).await;

                                match order_result {
                                    OrderResult::Open(fill) | OrderResult::Partial(fill) | OrderResult::Filled(fill) => {
                                        state.active_orders.push(ActiveOrder {
                                            id: fill.order_id,
                                            price,
                                            side: "buy".to_string(),
                                            size: order_size,
                                            created_at: now,
                                            kind: "Entry".to_string(),
                                        });
                                    }
                                    OrderResult::Failed(e) => {
                                        error!("[{}] Failed to open long position: {}", symbol, e);
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    for (symbol, mut state) in states {
        if let Some(&last_price) = state.price_queue.back() {
            let symbol_config = config_map.get(&symbol).unwrap();
            if paper_trade_mode {
                for t in state.active_paper_trades.drain(..) {
                    let size_btc = t.size as f64 * symbol_config.contract_size;
                    let entry_fee = t.entry_price * taker_fee_rate * size_btc;
                    let exit_fee = last_price * taker_fee_rate * size_btc;
                    let trade_fees = entry_fee + exit_fee;
                    let trade_slippage = last_price * (slippage_bps / BASIS_POINTS_DENOMINATOR) * size_btc;
                    let raw_gross_pnl = if t.side == "buy" { (last_price - t.entry_price) * size_btc } else { (t.entry_price - last_price) * size_btc };
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
                    session_tracker.write().record_trade(record);

                    let log_line = format!("{},{},{},{},{},{},{:.5},{:.5},{:.5},{:.5}\n",
                        symbol, t.entry_time, t.side, t.entry_price, last_price, "SHUTDOWN",
                        raw_gross_pnl, trade_fees, trade_slippage, net_pnl);
                    if let Some(ref mut writer) = csv_writer { let _ = writer.write_all(log_line.as_bytes()); }
                }
            } else {
                for t in state.active_live_trades.drain(..) {
                    let close_side = if t.side == "buy" { "sell" } else { "buy" };
                    let (_order_id, fill_price) = if config.order_manager.track_fills {
                        let order_result = order_manager.place_order_with_retry(
                            symbol_config.product_id,
                            None,
                            t.size,
                            close_side,
                            "market",
                            config.order_manager.max_retries,
                            config.order_manager.retry_base_delay_secs,
                            config.order_manager.retry_max_delay_secs,
                            &config.order_manager.retry_on_status,
                        ).await;

                        match order_result {
                            OrderResult::Filled(fill) => {
                                if let Ok(fill_info) = order_manager.wait_for_fill(
                                    &fill.order_id,
                                    config.order_manager.fill_timeout_secs,
                                    SHUTDOWN_POLL_INTERVAL_MS,
                                ).await {
                                    (Some(fill.order_id), fill_info.price)
                                } else {
                                    warn!("[{}] Fill confirmation timeout on shutdown, using order fill price", symbol);
                                    (Some(fill.order_id), fill.price)
                                }
                            }
                            OrderResult::Partial(fill) | OrderResult::Open(fill) => {
                                (Some(fill.order_id), fill.price)
                            }
                            OrderResult::Failed(e) => {
                                error!("[{}] Failed to place close order on shutdown: {}", symbol, e);
                                (None, last_price)
                            }
                        }
                    } else {
                        match order_manager.place_order(
                            symbol_config.product_id,
                            None,
                            t.size,
                            close_side,
                            "market",
                        ).await {
                            Ok(OrderResult::Filled(fill_info)) | Ok(OrderResult::Partial(fill_info)) | Ok(OrderResult::Open(fill_info)) => {
                                (Some(fill_info.order_id), fill_info.price)
                            }
                            Ok(OrderResult::Failed(e)) => {
                                error!("[{}] Failed to place close order on shutdown: {}", symbol, e);
                                (None, last_price)
                            }
                            Err(e) => {
                                error!("[{}] Failed to place close order on shutdown: {}", symbol, e);
                                (None, last_price)
                            }
                        }
                    };

                    let size_btc = t.size as f64 * symbol_config.contract_size;
                    let entry_price = t.fill_price.unwrap_or(t.entry_price);
                    let entry_fee = entry_price * taker_fee_rate * size_btc;
                    let exit_fee = fill_price * taker_fee_rate * size_btc;
                    let trade_fees = entry_fee + exit_fee;
                    let trade_slippage = fill_price * (slippage_bps / BASIS_POINTS_DENOMINATOR) * size_btc;
                    let raw_gross_pnl = if t.side == "buy" { (fill_price - entry_price) * size_btc } else { (entry_price - fill_price) * size_btc };
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
                    session_tracker.write().record_trade(record);

                    let log_line = format!("{},{},{},{},{},{},{:.5},{:.5},{:.5},{:.5}\n",
                        symbol, t.entry_time, t.side, entry_price, fill_price, "SHUTDOWN",
                        raw_gross_pnl, trade_fees, trade_slippage, net_pnl);
                    if let Some(ref mut writer) = csv_writer { let _ = writer.write_all(log_line.as_bytes()); }
                }
            }
        }
    }

    if let Some(mut writer) = csv_writer { let _ = writer.flush(); }

    let tracker = session_tracker.read();
    tracker.print_summary();

    info!("Strategy engine shutting down gracefully.");
}

pub async fn run_ingestion_feed(
    trade_tx: mpsc::Sender<TradeEvent>,
    config: Arc<AppConfig>,
    mut shutdown_rx: tokio::sync::broadcast::Receiver<()>,
) {
    let mut backoff = Duration::from_secs(config.websocket.reconnect_base_secs);
    let max_backoff = Duration::from_secs(config.websocket.reconnect_max_secs);
    let watchdog_timeout = Duration::from_secs(config.websocket.watchdog_timeout_secs);

    loop {
        let ws_url = config.websocket.ws_url.clone();
        info!("Attempting WebSocket connection to: {}", ws_url);
        let mut request = ws_url
            .into_client_request()
            .expect("Failed to create WebSocket request");

        request.headers_mut().insert(
            "User-Agent",
            config.websocket.user_agent.parse().unwrap()
        );

        let ws_stream = tokio::select! {
            res = tokio_tungstenite::connect_async_with_config(request, None, true) => {
                match res {
                    Ok((stream, _)) => stream,
                    Err(e) => {
                        error!("WebSocket connection failed: {:?}", e);
                        info!("Reconnecting in {} seconds...", backoff.as_secs());
                        tokio::time::sleep(backoff).await;
                        backoff = std::cmp::min(backoff * 2, max_backoff);
                        continue;
                    }
                }
            }
            _ = shutdown_rx.recv() => {
                info!("Shutdown signal received before connecting.");
                break;
            }
        };

        info!("WebSocket connected successfully.");
        backoff = Duration::from_secs(config.websocket.reconnect_base_secs);

        let (mut write, mut read) = ws_stream.split();

        let symbols_json = serde_json::to_string(&config.websocket.symbols).unwrap();
        let sub_payload = format!(
            r#"{{"type":"subscribe","payload":{{"channels":[{{"name":"trades","symbols":{}}}]}}}}"#,
            symbols_json
        );

        if let Err(e) = write.send(Message::Text(sub_payload.to_string())).await {
            error!("Failed to send subscription message: {:?}", e);
            continue;
        }
        info!("Subscription payload sent: {}", sub_payload);

        let mut last_message_time = SystemTime::now();

        loop {
            tokio::select! {
                msg_result = read.next() => {
                    match msg_result {
                        Some(Ok(msg)) => {
                            last_message_time = SystemTime::now();
                            match msg {
                                Message::Text(text) => {
                                    if let Ok(trade_msg) = serde_json::from_str::<TradeMessage>(&text) {
                                        if trade_msg.r#type == "trades" {
                                            if let (Some(price), Some(size)) = (trade_msg.p, trade_msg.s) {
                                                let side = decode_trade_side(trade_msg.r.as_deref(), trade_msg.side.as_deref()).to_string();
                                                let symbol = trade_msg.sy.unwrap_or_else(|| config.websocket.symbols[0].clone());
                                                let event = TradeEvent {
                                                    price,
                                                    size: size.to_string(),
                                                    side,
                                                    symbol,
                                                    product_id: trade_msg.product_id,
                                                };
                                                if let Err(e) = trade_tx.send(event).await {
                                                    error!("Failed to send trade to strategy engine (channel full): {:?}", e);
                                                    tokio::time::sleep(Duration::from_millis(10)).await;
                                                }
                                            }
                                        }
                                    }
                                }
                                Message::Binary(bin) => {
                                    if let Ok(text) = String::from_utf8(bin) {
                                        if let Ok(trade_msg) = serde_json::from_str::<TradeMessage>(&text) {
                                                if trade_msg.r#type == "trades" {
                                                    if let (Some(price), Some(size)) = (trade_msg.p, trade_msg.s) {
                                                        let side = decode_trade_side(trade_msg.r.as_deref(), trade_msg.side.as_deref()).to_string();
                                                        let symbol = trade_msg.sy.unwrap_or_else(|| config.websocket.symbols[0].clone());
                                                    let event = TradeEvent {
                                                        price,
                                                        size: size.to_string(),
                                                        side,
                                                        symbol,
                                                        product_id: trade_msg.product_id,
                                                    };
                                                    if let Err(e) = trade_tx.send(event).await {
                                                        error!("Failed to send trade to strategy engine (channel full): {:?}", e);
                                                        tokio::time::sleep(Duration::from_millis(10)).await;
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                                Message::Ping(ping) => {
                                    if let Err(e) = write.send(Message::Pong(ping)).await {
                                        error!("Failed to send pong: {:?}", e);
                                        break;
                                    }
                                }
                                Message::Close(_) => {
                                    info!("WebSocket closed by server.");
                                    break;
                                }
                                _ => {}
                            }
                        }
                        Some(Err(e)) => {
                            error!("WebSocket read error: {:?}", e);
                            break;
                        }
                        None => {
                            info!("WebSocket stream ended.");
                            break;
                        }
                    }
                }
                _ = shutdown_rx.recv() => {
                    info!("Shutdown signal received. Closing WebSocket...");
                    let close_frame = CloseFrame {
                        code: CloseCode::Normal,
                        reason: "Client shutting down".into(),
                    };
                    let _ = write.send(Message::Close(Some(close_frame))).await;
                    return;
                }
                _ = async {
                    loop {
                        tokio::time::sleep(Duration::from_secs(5)).await;
                        if SystemTime::now().duration_since(last_message_time).unwrap_or(Duration::MAX) > watchdog_timeout {
                            warn!("WebSocket watchdog timeout - no messages for {} seconds", config.websocket.watchdog_timeout_secs);
                            break;
                        }
                    }
                } => {
                    warn!("Watchdog triggered, forcing reconnect");
                    break;
                }
            }
        }

        info!("Reconnecting in {} seconds...", backoff.as_secs());
        tokio::time::sleep(backoff).await;
        backoff = std::cmp::min(backoff * 2, max_backoff);
    }

    info!("Ingestion feed terminated.");
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let config = Arc::new(load_config()?);

    let filter = EnvFilter::try_from_default_env()
        .or_else(|_| EnvFilter::try_new(&config.general.log_level))
        .unwrap();

    let fmt_layer = fmt::layer()
        .with_target(false)
        .with_thread_ids(false)
        .with_thread_names(false)
        .with_level(true)
        .with_file(false)
        .with_line_number(false);

    let mut layers = vec![];

    if config.general.json_logs {
        layers.push(fmt_layer.json().boxed());
    } else {
        layers.push(fmt_layer.boxed());
    }

    if let Some(log_file) = &config.general.log_file {
        if let Some(parent) = std::path::Path::new(log_file).parent() {
            std::fs::create_dir_all(parent).ok();
        }
        let file_appender = tracing_appender::rolling::daily(
            std::path::Path::new(log_file).parent().unwrap_or(std::path::Path::new(".")),
            std::path::Path::new(log_file).file_name().unwrap().to_str().unwrap()
        );
        let (non_blocking, _guard) = tracing_appender::non_blocking(file_appender);
        let file_layer = fmt::layer()
            .with_writer(non_blocking)
            .with_ansi(false)
            .json();
        layers.push(file_layer.boxed());
    }

    tracing_subscriber::registry()
        .with(filter)
        .with(layers)
        .init();

    info!("Starting Delta Trading Engine...");

    let api_key = std::env::var("DELTA_API_KEY").unwrap_or_else(|_| "dummy_api_key".to_string());
    let api_secret = std::env::var("DELTA_API_SECRET").unwrap_or_else(|_| "dummy_api_secret".to_string());

    let (trade_tx, trade_rx) = mpsc::channel::<TradeEvent>(config.general.trade_channel_buffer);
    let (shutdown_tx, shutdown_rx1) = tokio::sync::broadcast::channel(SHUTDOWN_BROADCAST_CAPACITY);
    let shutdown_rx2 = shutdown_tx.subscribe();

    let order_manager = Arc::new(OrderManager::new(
        api_key,
        api_secret,
        config.general.api_base_url.clone(),
        Duration::from_secs(config.order_manager.request_timeout_secs),
    ));

    let session_tracker = Arc::new(RwLock::new(SessionTracker::new(
        config.session_tracking.enabled,
        config.session_tracking.risk_free_rate,
        config.session_tracking.equity_snapshot_interval,
        config.session_tracking.initial_equity,
        config.session_tracking.estimated_ticks_per_day,
    )));

    let strategy_config = config.clone();
    let strategy_order_manager = order_manager.clone();
    let strategy_tracker = session_tracker.clone();

    let strategy_handle = tokio::spawn(async move {
        run_strategy_engine(trade_rx, strategy_order_manager, strategy_tracker, strategy_config, shutdown_rx2).await;
    });

    let ingestion_config = config.clone();
    let ingestion_handle = tokio::spawn(async move {
        run_ingestion_feed(trade_tx, ingestion_config, shutdown_rx1).await;
    });

    let heartbeat_handle = tokio::spawn(async move {
        let mut interval = interval(Duration::from_secs(HEARTBEAT_INTERVAL_SECS));
        interval.tick().await;
        loop {
            interval.tick().await;
            info!("[HEARTBEAT] System alive - {}", chrono::Local::now().format("%H:%M:%S"));
        }
    });

    tokio::select! {
        _ = tokio::signal::ctrl_c() => {
            info!("\nCTRL-C intercepted. Initiating graceful shutdown...");
            let _ = shutdown_tx.send(());
        }
    }

    info!("Waiting for worker tasks to complete...");

    let _ = tokio::join!(ingestion_handle, strategy_handle, heartbeat_handle);

    info!("System shutdown complete. All active feeds closed and queues finalized.");
    Ok(())
}