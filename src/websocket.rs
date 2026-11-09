use futures_util::{SinkExt, StreamExt};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use std::time::{Duration, SystemTime};
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::protocol::frame::coding::CloseCode;
use tokio_tungstenite::tungstenite::protocol::CloseFrame;
use tokio_tungstenite::tungstenite::Message;
use tracing::{error, info, warn};

use crate::config::AppConfig;

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
    pub timestamp_ns: u64,
}

#[derive(Debug, Clone)]
pub enum EngineMessage {
    Trade(TradeEvent),
    FeedStale,
    FeedResumed,
}

/// Decode the trade direction from a Delta `trades` channel payload.
///
/// Delta encodes `r` as the **buyer** role, NOT the trade direction:
///   * `"t"` / `"taker"`  -> the buyer was the aggressor  -> BUY  flow (CVD += size)
///   * `"m"` / `"maker"`  -> the seller was the aggressor -> SELL flow (CVD -= size)
///
/// Newer payloads may carry an explicit `side` ("buy"/"sell"); when present it is
/// treated as the authoritative aggressor direction and overrides `r`.
fn decode_trade_side(role: Option<&str>, side: Option<&str>) -> Option<&'static str> {
    if let Some(s) = side {
        match s.to_ascii_lowercase().as_str() {
            "buy" => return Some("buy"),
            "sell" => return Some("sell"),
            _ => {}
        }
    }
    match role.map(|r| r.to_ascii_lowercase()).as_deref() {
        Some("t") | Some("taker") => Some("buy"),
        Some("m") | Some("maker") => Some("sell"),
        _ => None,
    }
}

pub async fn run_ingestion_feed(
    trade_tx: mpsc::Sender<EngineMessage>,
    config: Arc<AppConfig>,
    mut shutdown_rx: tokio::sync::broadcast::Receiver<()>,
) {
    let mut backoff = Duration::from_secs(config.websocket.reconnect_base_secs);
    let max_backoff = Duration::from_secs(config.websocket.reconnect_max_secs);
    let watchdog_timeout = Duration::from_secs(config.websocket.watchdog_timeout_secs);

    // Delta `t` is in microseconds -> *1000 = nanoseconds. Fallback: wall-clock ns.
    fn to_ns(t: Option<u64>) -> u64 {
        match t {
            Some(v) => v.saturating_mul(1000),
            None => SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos() as u64)
                .unwrap_or(0),
        }
    }

    loop {
        let ws_url = config.websocket.ws_url.clone();
        info!(url = %ws_url, "Attempting WebSocket connection");
        let mut request = match ws_url.clone().into_client_request() {
            Ok(r) => r,
            Err(e) => {
                error!(error = ?e, "Invalid WebSocket URL");
                tokio::time::sleep(backoff).await;
                backoff = calculate_backoff_with_jitter(backoff, max_backoff);
                continue;
            }
        };

        match config.websocket.user_agent.parse() {
            Ok(v) => {
                request.headers_mut().insert("User-Agent", v);
            }
            Err(e) => {
                error!(error = ?e, "Invalid User-Agent header");
                tokio::time::sleep(backoff).await;
                backoff = calculate_backoff_with_jitter(backoff, max_backoff);
                continue;
            }
        };

        let ws_stream = tokio::select! {
            res = tokio_tungstenite::connect_async_with_config(request, None, true) => {
                match res {
                    Ok((stream, _)) => stream,
                    Err(e) => {
                        error!(error = ?e, url = %ws_url, "WebSocket connection failed");
                        let _ = trade_tx.send(EngineMessage::FeedStale).await;
                        info!(backoff_secs = backoff.as_secs(), "Reconnecting in backoff...");
                        tokio::time::sleep(backoff).await;
                        backoff = calculate_backoff_with_jitter(backoff, max_backoff);
                        continue;
                    }
                }
            }
            _ = shutdown_rx.recv() => {
                info!("Shutdown signal received before connecting.");
                break;
            }
        };

        info!(url = %config.websocket.ws_url, "WebSocket connected successfully.");
        let _ = trade_tx.send(EngineMessage::FeedResumed).await;
        backoff = Duration::from_secs(config.websocket.reconnect_base_secs);

        let (mut write, mut read) = ws_stream.split();

        let symbols_json = match serde_json::to_string(&config.websocket.symbols) {
            Ok(s) => s,
            Err(e) => {
                error!(error = ?e, "Failed to serialize symbols");
                continue;
            }
        };
        let sub_payload = format!(
            r#"{{"type":"subscribe","payload":{{"channels":[{{"name":"trades","symbols":{}}}]}}}}"#,
            symbols_json
        );

        if let Err(e) = write.send(Message::Text(sub_payload.to_string())).await {
            error!(error = ?e, "Failed to send subscription message");
            continue;
        }
        info!(payload = %sub_payload, "Subscription payload sent");

        let mut last_message_time = tokio::time::Instant::now();
        let mut dropped_trades: u64 = 0;

        loop {
            tokio::select! {
                msg_result = read.next() => {
                    match msg_result {
                        Some(Ok(msg)) => {
                            last_message_time = tokio::time::Instant::now();
                            match msg {
                                Message::Text(text) => {
                                    match serde_json::from_str::<TradeMessage>(&text) {
                                        Ok(trade_msg) => {
                                        if trade_msg.r#type == "trades" {
                                            if let (Some(price), Some(size)) = (trade_msg.p, trade_msg.s) {
                                                let Some(side) = decode_trade_side(trade_msg.r.as_deref(), trade_msg.side.as_deref()) else {
                                                    warn!("unknown trade side r={:?} side={:?}; skipping", trade_msg.r, trade_msg.side);
                                                    continue;
                                                };
                                                let Some(symbol) = trade_msg.sy.clone().or_else(|| config.websocket.symbols.first().cloned()) else {
                                                    warn!("trade without symbol and empty symbol list; skipping");
                                                    continue;
                                                };
                                                let timestamp_ns = to_ns(trade_msg.t);
                                                let event = TradeEvent {
                                                    price,
                                                    size: size.to_string(),
                                                    side: side.to_string(),
                                                    symbol,
                                                    product_id: trade_msg.product_id,
                                                    timestamp_ns,
                                                };
                                                if let Err(e) = trade_tx.try_send(EngineMessage::Trade(event)) {
                                                    dropped_trades += 1;
                                                    warn!(error = ?e, dropped = dropped_trades, "trade channel full; dropping (backpressure)");
                                                }
                                            }
                                        }
                                        }
                                        Err(e) => warn!("unparseable WS text frame: {}", e),
                                    }
                                }
                                Message::Binary(bin) => {
                                    if let Ok(text) = String::from_utf8(bin) {
                                        match serde_json::from_str::<TradeMessage>(&text) {
                                            Ok(trade_msg) => {
                                                if trade_msg.r#type == "trades" {
                                                    if let (Some(price), Some(size)) = (trade_msg.p, trade_msg.s) {
                                                        let Some(side) = decode_trade_side(trade_msg.r.as_deref(), trade_msg.side.as_deref()) else {
                                                            warn!("unknown trade side; skipping");
                                                            continue;
                                                        };
                                                        let Some(symbol) = trade_msg.sy.clone().or_else(|| config.websocket.symbols.first().cloned()) else {
                                                            warn!("trade without symbol; skipping");
                                                            continue;
                                                        };
                                                    let timestamp_ns = to_ns(trade_msg.t);
                                                    let event = TradeEvent {
                                                        price,
                                                        size: size.to_string(),
                                                        side: side.to_string(),
                                                        symbol,
                                                        product_id: trade_msg.product_id,
                                                        timestamp_ns,
                                                    };
                                                    if let Err(e) = trade_tx.try_send(EngineMessage::Trade(event)) {
                                                        dropped_trades += 1;
                                                        warn!(error = ?e, dropped = dropped_trades, "trade channel full; dropping (backpressure)");
                                                    }
                                                }
                                            }
                                            }
                                            Err(e) => warn!("unparseable WS binary frame: {}", e),
                                        }
                                    }
                                }
                                Message::Ping(ping) => {
                                    if let Err(e) = write.send(Message::Pong(ping)).await {
                                        error!(error = ?e, "Failed to send pong");
                                        break;
                                    }
                                }
                                Message::Close(frame) => {
                                    info!(reason = ?frame, "WebSocket closed by server.");
                                    break;
                                }
                                _ => {}
                            }
                        }
                        Some(Err(e)) => {
                            error!(error = ?e, "WebSocket read error");
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
                        if last_message_time.elapsed() > watchdog_timeout {
                            warn!(timeout_secs = config.websocket.watchdog_timeout_secs, "WebSocket watchdog timeout - no messages");
                            break;
                        }
                    }
                } => {
                    warn!("Watchdog triggered, forcing reconnect");
                    let _ = trade_tx.send(EngineMessage::FeedStale).await;
                    break;
                }
            }
        }

        let _ = trade_tx.send(EngineMessage::FeedStale).await;
        info!(
            backoff_secs = backoff.as_secs(),
            "Reconnecting in backoff..."
        );
        tokio::time::sleep(backoff).await;
        backoff = calculate_backoff_with_jitter(backoff, max_backoff);
    }

    info!("Ingestion feed terminated.");
}

pub fn calculate_backoff_with_jitter(current_backoff: Duration, max_backoff: Duration) -> Duration {
    let current_secs = current_backoff.as_secs_f64();
    let mut next_secs = current_secs * 2.0;

    // Jitter: +/- 20%
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .subsec_nanos();
    let jitter_factor = 1.0 + ((nanos % 41) as f64 / 100.0 - 0.20);
    next_secs *= jitter_factor;

    let next_secs = next_secs.clamp(1.0, max_backoff.as_secs_f64());
    Duration::from_secs_f64(next_secs)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    #[test]
    fn test_backoff_with_jitter() {
        let max = Duration::from_secs(30);

        let start = Duration::from_secs(1);
        let next = calculate_backoff_with_jitter(start, max);
        // Expect next to be between 1.6 and 2.4 (2.0 * 0.8 and 2.0 * 1.2)
        assert!(next.as_secs_f64() >= 1.6);
        assert!(next.as_secs_f64() <= 2.4);

        let close_to_max = Duration::from_secs(20);
        let capped = calculate_backoff_with_jitter(close_to_max, max);
        // Expect next to be clamped at max (30)
        assert_eq!(capped, max);
    }
}
