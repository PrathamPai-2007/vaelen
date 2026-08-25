use delta_trading_engine::config::AppConfig;
use delta_trading_engine::orders::OrderManager;
use delta_trading_engine::session::SessionTracker;
use delta_trading_engine::strategy_engine::run_strategy_engine;
use delta_trading_engine::websocket::{EngineMessage, TradeEvent};
use parking_lot::RwLock;
use std::env;
use std::fs::File;
use std::io::Read;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::{broadcast, mpsc};

#[derive(Debug, Clone)]
#[repr(C)]
struct TickRecord {
    ts: u64,
    px: f64,
    qty: f64,
    is_buy: u8,
    _padding: [u8; 7],
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = env::args().collect();
    if args.len() < 4 {
        eprintln!("Usage: backtest <config_path> <symbol> <binary_data_path>");
        std::process::exit(1);
    }
    let config_path = &args[1];
    let symbol = &args[2];
    let data_path = &args[3];

    let mut config = AppConfig::load_from_path(config_path)?;
    config.general.paper_trade_mode = true;
    let config = Arc::new(config);

    let session_tracker = Arc::new(RwLock::new(SessionTracker::new(
        config.session_tracking.enabled,
        config.session_tracking.risk_free_rate,
        config.session_tracking.equity_snapshot_interval,
        config.session_tracking.initial_equity,
        100_000.0,
    )));

    let order_manager = Arc::new(OrderManager::new(
        "".to_string(),
        "".to_string(),
        "dummy_client".to_string(),
        Duration::from_secs(5),
        5,
    ));

    let (trade_tx, trade_rx) = mpsc::channel::<EngineMessage>(10_000_000);
    let (_shutdown_tx, shutdown_rx) = broadcast::channel::<()>(1);

    let engine_handle = tokio::spawn(run_strategy_engine(
        trade_rx,
        order_manager,
        session_tracker.clone(),
        config.clone(),
        shutdown_rx,
        None,
    ));

    let mut file = File::open(data_path)?;
    let mut buffer = Vec::new();
    file.read_to_end(&mut buffer)?;

    let record_size = std::mem::size_of::<TickRecord>();
    let num_records = buffer.len() / record_size;

    let records: &[TickRecord] = unsafe {
        std::slice::from_raw_parts(buffer.as_ptr() as *const TickRecord, num_records)
    };

    let mut count = 0;
    for record in records {
        let event = TradeEvent {
            price: record.px.to_string(),
            size: record.qty.to_string(),
            side: if record.is_buy == 1 {
                "buy".to_string()
            } else {
                "sell".to_string()
            },
            symbol: symbol.clone(),
            product_id: Some(1),
            timestamp_ns: record.ts,
        };

        if let Err(_) = trade_tx.send(EngineMessage::Trade(event)).await {
            break;
        }
        count += 1;
    }

    drop(trade_tx);

    let _ = engine_handle.await;

    let tracker = session_tracker.read();
    let net_pnl = tracker.total_gross_pnl - tracker.total_fees - tracker.total_slippage;
    let win_rate = if tracker.trades_closed > 0 {
        tracker.winning_trades as f64 / tracker.trades_closed as f64
    } else {
        0.0
    };
    
    let trades_json = serde_json::to_string(&tracker.trades).unwrap_or_else(|_| "[]".to_string());
    
    println!(
        r#"{{"symbol": "{}", "total_ticks": {}, "total_trades": {}, "gross_pnl": {:.4}, "fees": {:.4}, "slippage": {:.4}, "net_pnl": {:.4}, "win_rate": {:.4}, "trades": {}}}"#,
        symbol, count, tracker.trades_closed, tracker.total_gross_pnl, tracker.total_fees, tracker.total_slippage, net_pnl, win_rate, trades_json
    );

    Ok(())
}
