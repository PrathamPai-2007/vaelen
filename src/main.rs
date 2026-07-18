use futures_util::{SinkExt, StreamExt};
use hmac::{Hmac, Mac};
use reqwest::{header, Client};
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use std::collections::VecDeque;
use std::fs::OpenOptions;
use std::io::Write;
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::protocol::frame::coding::CloseCode;
use tokio_tungstenite::tungstenite::protocol::CloseFrame;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;

#[derive(Clone, Debug)]
pub struct PaperTrade {
    pub entry_time: u64,
    pub entry_price: f64,
    pub side: String,
    pub size: i64,
    pub ticks_elapsed: usize,
}

#[derive(Clone, Debug)]
pub struct LiveTrade {
    pub product_id: u64,
    pub entry_time: u64,
    pub entry_price: f64,
    pub side: String,
    pub size: i64,
    pub ticks_elapsed: usize,
}

// --- PHASE 1: Dependency Layout & Core Data Structures ---

type HmacSha256 = Hmac<Sha256>;

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct SubscriptionPayload {
    pub r#type: String,
    pub payload: SubscriptionDetails,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct SubscriptionDetails {
    pub channels: Vec<ChannelInfo>,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct ChannelInfo {
    pub name: String,
    pub symbols: Vec<String>,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct TradeMessage {
    pub r#type: String,
    pub p: Option<String>,  // Price
    pub s: Option<f64>,     // Size (float)
    pub r: Option<String>,  // Buyer Role ("t" -> Buy, "m" -> Sell)
    pub sy: Option<String>, // Symbol
    pub t: Option<u64>,     // Timestamp
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct TradeEvent {
    pub price: String,
    pub size: String,
    pub side: String,
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

// --- END PHASE 1 ---

// --- PHASE 4: Asynchronous Order Manager & Execution Pool ---

#[derive(Clone)]
pub struct OrderManager {
    client: Arc<Client>,
    api_key: String,
    api_secret: String,
}

impl OrderManager {
    pub fn new(api_key: String, api_secret: String) -> Self {
        let client = Client::builder()
            .user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            .tcp_keepalive(Duration::from_secs(30)) // Short 30s probe to prevent CloudFront from dropping the connection
            .pool_idle_timeout(None) // Never close idle connections in the pool
            .build()
            .unwrap_or_else(|_| Client::new());
        Self {
            client: Arc::new(client),
            api_key,
            api_secret,
        }
    }

    pub fn place_order(&self, product_id: u64, price: Option<f64>, size: i64, side: &str, order_type: &str) {
        let client_clone = Arc::clone(&self.client);
        let api_key = self.api_key.clone();
        let api_secret = self.api_secret.clone();
        let side = side.to_string();
        let order_type = order_type.to_string();

        tokio::spawn(async move {
            let order_req = OrderRequest {
                product_id,
                size,
                side: side.clone(),
                order_type: order_type.clone(),
                price: price.map(|p| p.to_string()),
                post_only: if order_type == "limit" { Some("true".to_string()) } else { None },
            };

            let payload_str = match serde_json::to_string(&order_req) {
                Ok(s) => s,
                Err(e) => {
                    eprintln!("Failed to serialize order: {:?}", e);
                    return;
                }
            };

            let timestamp = match SystemTime::now().duration_since(UNIX_EPOCH) {
                Ok(n) => n.as_secs().to_string(),
                Err(e) => {
                    eprintln!("Time went backwards: {:?}", e);
                    return;
                }
            };

            let method = "POST";
            let path = "/v2/orders";
            let signature = generate_signature(&api_secret, method, &timestamp, path, &payload_str);

            let url = format!("https://api.india.delta.exchange{}", path);

            let res = client_clone
                .post(&url)
                .header("api-key", api_key)
                .header("signature", signature)
                .header("timestamp", timestamp)
                .header(header::CONTENT_TYPE, "application/json")
                .body(payload_str)
                .send()
                .await;

            match res {
                Ok(response) => {
                    if response.status().is_success() {
                        if order_type == "limit" {
                            println!(
                                "Order placed successfully: {} Limit {} contracts at {}",
                                side,
                                size,
                                price.unwrap_or(0.0)
                            );
                        } else {
                            println!("Order placed successfully: {} Market {} contracts", side, size);
                        }
                    } else {
                        eprintln!(
                            "Order placement failed with status: {}. Body: {:?}",
                            response.status(),
                            response.text().await
                        );
                    }
                }
                Err(e) => {
                    eprintln!("Order request failed: {:?}", e);
                }
            }
        });
    }
}

// --- END PHASE 4 ---

// --- PHASE 3: Memory-Bounded Analysis Engine & Strategy Logic ---

pub async fn run_strategy_engine(
    mut trade_rx: mpsc::Receiver<TradeEvent>,
    order_manager: OrderManager,
) {
    let max_capacity = 500;
    let lookback_ticks = 50;
    let paper_trade_mode = std::env::var("PAPER_TRADE_MODE").unwrap_or_else(|_| "false".to_string()).eq_ignore_ascii_case("true");
    let mut active_paper_trades: Vec<PaperTrade> = Vec::new();
    let mut active_live_trades: Vec<LiveTrade> = Vec::new();

    // Fee and slippage calculation constants
    let maker_fee_rate = 0.0002;       // 0.02% Maker fee
    let taker_fee_rate = 0.0005;       // 0.05% Taker fee
    let slippage_penalty = 1.0;        // Slippage penalty in USD per BTC
    let contract_size_btc = 0.001;     // 1 contract = 0.001 BTC

    std::fs::create_dir_all("trades").ok();
    let session_filename = format!("trades/{}.csv", chrono::Local::now().format("%Y-%m-%d_%H-%M-%S"));
    if let Ok(mut file) = OpenOptions::new().create(true).write(true).open(&session_filename) {
        let _ = file.write_all(b"entry_time,side,entry_price,exit_price,gross_pnl,fees,slippage,net_pnl\n");
    }
    
    let mut session_total_pnl = 0.0;
    let mut session_gross_pnl = 0.0;
    let mut session_total_fees = 0.0;
    let mut session_total_slippage = 0.0;
    let mut session_trades_entered = 0;
    let mut session_trades_closed = 0;

    let mut price_queue: VecDeque<f64> = VecDeque::with_capacity(max_capacity);
    let mut cvd_queue: VecDeque<f64> = VecDeque::with_capacity(max_capacity);
    let mut current_cvd: f64 = 0.0;

    println!("Strategy engine initialized. Paper Trading: {}", paper_trade_mode);

    while let Some(trade) = trade_rx.recv().await {
        let price = match trade.price.parse::<f64>() {
            Ok(p) => p,
            Err(e) => {
                eprintln!("Failed to parse price {}: {}", trade.price, e);
                continue;
            }
        };

        let size = match trade.size.parse::<f64>() {
            Ok(s) => s,
            Err(e) => {
                eprintln!("Failed to parse size {}: {}", trade.size, e);
                continue;
            }
        };

        if trade.side.eq_ignore_ascii_case("buy") {
            current_cvd += size;
        } else if trade.side.eq_ignore_ascii_case("sell") {
            current_cvd -= size;
        } else {
            eprintln!("Unknown trade side: {}", trade.side);
            continue;
        }

        if paper_trade_mode {
            let mut completed_trades = Vec::new();
            active_paper_trades.retain_mut(|t| {
                t.ticks_elapsed += 1;
                if t.ticks_elapsed == 50 {
                    completed_trades.push(t.clone());
                    false // remove
                } else {
                    true // keep
                }
            });
            for t in completed_trades {
                let size_btc = t.size as f64 * contract_size_btc;
                let entry_fee = t.entry_price * maker_fee_rate * size_btc;
                
                // Slippage applies to the market exit price
                let exit_price_with_slippage = if t.side == "buy" {
                    price - slippage_penalty
                } else {
                    price + slippage_penalty
                };
                
                let exit_fee = exit_price_with_slippage * taker_fee_rate * size_btc;
                let trade_fees = entry_fee + exit_fee;
                let trade_slippage = slippage_penalty * size_btc;
                
                let raw_gross_pnl = if t.side == "buy" {
                    (price - t.entry_price) * size_btc
                } else {
                    (t.entry_price - price) * size_btc
                };
                
                let net_pnl = raw_gross_pnl - trade_fees - trade_slippage;
                
                session_trades_closed += 1;
                session_gross_pnl += raw_gross_pnl;
                session_total_fees += trade_fees;
                session_total_slippage += trade_slippage;
                session_total_pnl += net_pnl;
                
                let log_line = format!(
                    "{},{},{},{},{:.4},{:.4},{:.4},{:.4}\n",
                    t.entry_time,
                    t.side,
                    t.entry_price,
                    price,
                    raw_gross_pnl,
                    trade_fees,
                    trade_slippage,
                    net_pnl
                );
                if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(&session_filename) {
                    let _ = file.write_all(log_line.as_bytes());
                }
            }
        } else {
            // Live Trading Exit Logic: Close positions after 50 ticks
            let mut completed_live_trades = Vec::new();
            active_live_trades.retain_mut(|t| {
                t.ticks_elapsed += 1;
                if t.ticks_elapsed == 50 {
                    completed_live_trades.push(t.clone());
                    false // remove
                } else {
                    true // keep
                }
            });
            for t in completed_live_trades {
                let size_btc = t.size as f64 * contract_size_btc;
                let entry_fee = t.entry_price * maker_fee_rate * size_btc;
                
                let exit_price_with_slippage = if t.side == "buy" {
                    price - slippage_penalty
                } else {
                    price + slippage_penalty
                };
                
                let exit_fee = exit_price_with_slippage * taker_fee_rate * size_btc;
                let trade_fees = entry_fee + exit_fee;
                let trade_slippage = slippage_penalty * size_btc;
                
                let raw_gross_pnl = if t.side == "buy" {
                    (price - t.entry_price) * size_btc
                } else {
                    (t.entry_price - price) * size_btc
                };
                
                let net_pnl = raw_gross_pnl - trade_fees - trade_slippage;
                
                session_trades_closed += 1;
                session_gross_pnl += raw_gross_pnl;
                session_total_fees += trade_fees;
                session_total_slippage += trade_slippage;
                session_total_pnl += net_pnl;
                
                let close_side = if t.side == "buy" { "sell" } else { "buy" };
                order_manager.place_order(t.product_id, None, t.size, close_side, "market");
                
                println!(
                    "Live position closed. Entry: {:.2}, Exit: {:.2}, Est. Net PnL: {:.4} USD",
                    t.entry_price, price, net_pnl
                );
                
                let log_line = format!(
                    "{},{},{},{},{:.4},{:.4},{:.4},{:.4}\n",
                    t.entry_time,
                    t.side,
                    t.entry_price,
                    price,
                    raw_gross_pnl,
                    trade_fees,
                    trade_slippage,
                    net_pnl
                );
                if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(&session_filename) {
                    let _ = file.write_all(log_line.as_bytes());
                }
            }
        }

        if price_queue.len() == max_capacity {
            price_queue.pop_front();
        }
        if cvd_queue.len() == max_capacity {
            cvd_queue.pop_front();
        }

        price_queue.push_back(price);
        cvd_queue.push_back(current_cvd);

        let current_len = price_queue.len();
        if current_len >= lookback_ticks + 1 {
            let past_index = current_len - 1 - lookback_ticks;
            if let (Some(&past_price), Some(&past_cvd)) =
                (price_queue.get(past_index), cvd_queue.get(past_index))
            {
                let min_price_drop = 5.0; // Require at least a $5 USD drop
                let price_drifted_down = price <= (past_price - min_price_drop);
                let cvd_growth = current_cvd - past_cvd;
                let threshold = size * 10.0;

                if price_drifted_down && cvd_growth > threshold {
                    println!(
                        "Bullish Passive Absorption Divergence Detected! Price: {}, CVD Growth: {}, Threshold: {}",
                        price, cvd_growth, threshold
                    );

                    let order_size = 100;
                    if paper_trade_mode {
                        let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();
                        active_paper_trades.push(PaperTrade {
                            entry_time: now,
                            entry_price: price,
                            side: "buy".to_string(),
                            size: order_size,
                            ticks_elapsed: 0,
                        });
                        session_trades_entered += 1;
                    } else {
                        let product_id = trade.product_id.unwrap_or(27); // default to 27 (BTCUSD) if not provided by ws
                        order_manager.place_order(product_id, Some(price), order_size, "buy", "limit");
                        
                        let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();
                        active_live_trades.push(LiveTrade {
                            product_id,
                            entry_time: now,
                            entry_price: price,
                            side: "buy".to_string(),
                            size: order_size,
                            ticks_elapsed: 0,
                        });
                        session_trades_entered += 1;
                    }
                }
            }
        }
    }

    println!("\n=== SESSION TRADING SUMMARY ===");
    println!("Total Trades Entered: {}", session_trades_entered);
    println!("Total Trades Closed:  {}", session_trades_closed);
    println!("Gross Session PnL:    {:.4} USD", session_gross_pnl);
    println!("Total Fees Paid:      {:.4} USD", session_total_fees);
    println!("Total Slippage Paid:  {:.4} USD", session_total_slippage);
    println!("Net Session PnL:      {:.4} USD", session_total_pnl);
    println!("===============================\n");

    println!("Strategy engine shutting down gracefully.");
}

// --- END PHASE 3 ---

// --- PHASE 2 & 5: Bounded Async Ingestion Feed & Resiliency State Machine ---

pub async fn run_ingestion_feed(
    trade_tx: mpsc::Sender<TradeEvent>,
    mut shutdown_rx: tokio::sync::broadcast::Receiver<()>,
) {
    let mut backoff = Duration::from_secs(1);
    let max_backoff = Duration::from_secs(60);

    loop {
        println!("Attempting WebSocket connection to: wss://public-socket.india.delta.exchange");
        let mut request = "wss://public-socket.india.delta.exchange"
            .into_client_request()
            .expect("Failed to create WebSocket request");
        
        request.headers_mut().insert(
            "User-Agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36".parse().unwrap()
        );

        let ws_stream = tokio::select! {
            res = tokio_tungstenite::connect_async_with_config(request, None, true) => {
                match res {
                    Ok((stream, _)) => stream,
                    Err(e) => {
                        eprintln!("WebSocket connection failed: {:?}", e);
                        println!("Reconnecting in {} seconds...", backoff.as_secs());
                        tokio::time::sleep(backoff).await;
                        backoff = std::cmp::min(backoff * 2, max_backoff);
                        continue;
                    }
                }
            }
            _ = shutdown_rx.recv() => {
                println!("Shutdown signal received before connecting.");
                break;
            }
        };

        println!("WebSocket connected successfully.");
        backoff = Duration::from_secs(1); // Reset backoff on success

        let (mut write, mut read) = ws_stream.split();

        let sub_payload = SubscriptionPayload {
            r#type: "subscribe".to_string(),
            payload: SubscriptionDetails {
                channels: vec![ChannelInfo {
                    name: "trades".to_string(),
                    symbols: vec!["BTCUSD".to_string()],
                }],
            },
        };

        if let Ok(msg_text) = serde_json::to_string(&sub_payload) {
            if let Err(e) = write.send(Message::Text(msg_text)).await {
                eprintln!("Failed to send subscription message: {:?}", e);
                continue;
            }
            println!("Subscription payload sent.");
        }

        loop {
            tokio::select! {
                msg_result = read.next() => {
                    match msg_result {
                        Some(Ok(msg)) => {
                            match msg {
                                 Message::Text(text) => {
                                    match serde_json::from_str::<TradeMessage>(&text) {
                                        Ok(trade_msg) => {
                                            if trade_msg.r#type == "trades" {
                                                if let (Some(price), Some(size), Some(role)) = (trade_msg.p, trade_msg.s, trade_msg.r) {
                                                    let side = if role == "t" { "buy".to_string() } else { "sell".to_string() };
                                                    let event = TradeEvent {
                                                        price,
                                                        size: size.to_string(),
                                                        side,
                                                        product_id: Some(27),
                                                    };
                                                    if let Err(e) = trade_tx.send(event).await {
                                                        eprintln!("Failed to send trade to strategy engine: {:?}", e);
                                                        return; // Receiver dropped, stop ingestion
                                                    }
                                                }
                                            }
                                        }
                                        Err(_e) => {
                                            // Silently ignore subscription confirmations
                                        }
                                    }
                                }
                                Message::Binary(bin) => {
                                    if let Ok(text) = String::from_utf8(bin) {
                                         if let Ok(trade_msg) = serde_json::from_str::<TradeMessage>(&text) {
                                             if trade_msg.r#type == "trades" {
                                                if let (Some(price), Some(size), Some(role)) = (trade_msg.p, trade_msg.s, trade_msg.r) {
                                                    let side = if role == "t" { "buy".to_string() } else { "sell".to_string() };
                                                    let event = TradeEvent {
                                                        price,
                                                        size: size.to_string(),
                                                        side,
                                                        product_id: Some(27),
                                                    };
                                                    if let Err(e) = trade_tx.send(event).await {
                                                        eprintln!("Failed to send trade to strategy engine: {:?}", e);
                                                        return;
                                                    }
                                                }
                                             }
                                         }
                                    }
                                }
                                Message::Ping(ping) => {
                                    if let Err(e) = write.send(Message::Pong(ping)).await {
                                        eprintln!("Failed to send pong: {:?}", e);
                                        break;
                                    }
                                }
                                Message::Close(_) => {
                                    println!("WebSocket closed by server.");
                                    break;
                                }
                                _ => {} // Ignore other frames
                            }
                        }
                        Some(Err(e)) => {
                            eprintln!("WebSocket read error: {:?}", e);
                            break;
                        }
                        None => {
                            println!("WebSocket stream ended.");
                            break;
                        }
                    }
                }
                _ = shutdown_rx.recv() => {
                    println!("Shutdown signal received. Closing WebSocket...");
                    let close_frame = CloseFrame {
                        code: CloseCode::Normal,
                        reason: "Client shutting down".into(),
                    };
                    let _ = write.send(Message::Close(Some(close_frame))).await;
                    return;
                }
            }
        }

        println!("Reconnecting in {} seconds...", backoff.as_secs());
        tokio::time::sleep(backoff).await;
        backoff = std::cmp::min(backoff * 2, max_backoff);
    }
    
    println!("Ingestion feed terminated.");
}

// --- END PHASE 2 & 5 ---

#[tokio::main]
async fn main() {
    // Load environment variables from a .env file if present
    dotenvy::dotenv().ok();

    println!("Starting Delta Trading Engine...");

    let api_key = std::env::var("DELTA_API_KEY").unwrap_or_else(|_| "dummy_api_key".to_string());
    let api_secret = std::env::var("DELTA_API_SECRET").unwrap_or_else(|_| "dummy_api_secret".to_string());


    let (trade_tx, trade_rx) = mpsc::channel::<TradeEvent>(1000);
    let (shutdown_tx, shutdown_rx1) = tokio::sync::broadcast::channel(1);
    
    let order_manager = OrderManager::new(api_key, api_secret);

    let strategy_handle = tokio::spawn(async move {
        run_strategy_engine(trade_rx, order_manager).await;
    });

    let ingestion_handle = tokio::spawn(async move {
        run_ingestion_feed(trade_tx, shutdown_rx1).await;
    });

    // Phase 5: Graceful Shutdown Monitor
    tokio::select! {
        _ = tokio::signal::ctrl_c() => {
            println!("\nCTRL-C intercepted. Initiating graceful shutdown...");
            let _ = shutdown_tx.send(());
        }
    }

    println!("Waiting for worker tasks to complete...");
    
    let _ = tokio::join!(ingestion_handle, strategy_handle);
    
    println!("System shutdown complete. All active feeds closed and queues finalized.");
}
