use parking_lot::RwLock;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::mpsc;
use tokio::time::interval;
use tracing::info;
use tracing_subscriber::{fmt, layer::SubscriberExt, util::SubscriberInitExt, EnvFilter, Layer};

use delta_trading_engine::config::load_config;
use delta_trading_engine::orders::OrderManager;
use delta_trading_engine::session::SessionTracker;
use delta_trading_engine::strategy_engine::run_strategy_engine;
use delta_trading_engine::websocket::{run_ingestion_feed, EngineMessage};

pub use delta_trading_engine::orders::{generate_signature, OrderRequest, OrderResponse};
pub use delta_trading_engine::strategy_engine::{ActiveOrder, LiveTrade, PaperTrade, SymbolState};
pub use delta_trading_engine::websocket::TradeMessage;

const SHUTDOWN_BROADCAST_CAPACITY: usize = 16;
const HEARTBEAT_INTERVAL_SECS: u64 = 60;

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
            std::path::Path::new(log_file)
                .parent()
                .unwrap_or(std::path::Path::new(".")),
            std::path::Path::new(log_file)
                .file_name()
                .unwrap()
                .to_str()
                .unwrap(),
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
    let api_secret =
        std::env::var("DELTA_API_SECRET").unwrap_or_else(|_| "dummy_api_secret".to_string());

    let (trade_tx, trade_rx) = mpsc::channel::<EngineMessage>(config.general.trade_channel_buffer);
    let (shutdown_tx, shutdown_rx1) = tokio::sync::broadcast::channel(SHUTDOWN_BROADCAST_CAPACITY);
    let shutdown_rx2 = shutdown_tx.subscribe();

    let order_manager = Arc::new(OrderManager::new(
        api_key,
        api_secret,
        config.general.api_base_url.clone(),
        Duration::from_secs(config.order_manager.request_timeout_secs),
        config.order_manager.max_consecutive_failures,
    ));

    let session_tracker = Arc::new(RwLock::new(SessionTracker::new(
        config.session_tracking.enabled,
        config.session_tracking.risk_free_rate,
        config.session_tracking.equity_snapshot_interval,
        config.session_tracking.initial_equity,
        config.session_tracking.estimated_ticks_per_day,
    )));

    let telegram_tx = if config.notifications.telegram.enabled {
        Some(delta_trading_engine::telegram::TelegramNotifier::spawn(
            config.notifications.telegram.bot_token.clone(),
            config.notifications.telegram.chat_id.clone()
        ))
    } else {
        None
    };

    let strategy_config = config.clone();
    let strategy_order_manager = order_manager.clone();
    let strategy_tracker = session_tracker.clone();

    let strategy_handle = tokio::spawn(async move {
        run_strategy_engine(
            trade_rx,
            strategy_order_manager,
            strategy_tracker,
            strategy_config,
            shutdown_rx2,
            telegram_tx,
        ).await;
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
            info!(
                "[HEARTBEAT] System alive - {}",
                chrono::Local::now().format("%H:%M:%S")
            );
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