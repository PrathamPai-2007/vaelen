use config::{Config, File};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct AppConfig {
    pub general: GeneralConfig,
    pub websocket: WebSocketConfig,
    pub order_manager: OrderManagerConfig,
    pub fees: FeesConfig,
    pub strategy: StrategyConfig,
    pub session_tracking: SessionTrackingConfig,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct GeneralConfig {
    pub api_base_url: String,
    pub paper_trade_mode: bool,
    pub max_concurrent_positions: usize,
    pub trade_channel_buffer: usize,
    pub trades_dir: String,
    pub log_level: String,
    pub json_logs: bool,
    pub log_file: Option<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct WebSocketConfig {
    pub ws_url: String,
    pub symbols: Vec<String>,
    pub ping_interval_secs: u64,
    pub watchdog_timeout_secs: u64,
    pub reconnect_base_secs: u64,
    pub reconnect_max_secs: u64,
    pub user_agent: String,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct OrderManagerConfig {
    pub request_timeout_secs: u64,
    pub max_retries: u32,
    pub retry_base_delay_secs: u64,
    pub retry_max_delay_secs: u64,
    pub retry_on_status: Vec<u16>,
    pub track_fills: bool,
    pub fill_timeout_secs: u64,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct FeesConfig {
    pub taker_fee_rate: f64,
    pub maker_fee_rate: f64,
    pub slippage_bps: f64,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct StrategyConfig {
    #[serde(default = "default_symbols")]
    pub symbols: Vec<SymbolConfig>,
}

fn default_symbols() -> Vec<SymbolConfig> {
    vec![]
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct SymbolConfig {
    pub symbol: String,
    pub product_id: u64,
    pub contract_size: f64,
    pub order_size: i64,
    pub tick_size: f64,
    pub stop_loss_bps: f64,
    pub take_profit_bps: f64,
    pub hold_ticks: usize,
    pub entry_cooldown_ticks: u64,
    pub trailing_stop_atr_mult: f64,
    pub min_trailing_stop_distance: f64,
    pub atr_period: usize,
    pub lookback_ticks: usize,
    #[serde(default)]
    pub min_price_drop_bps: f64,
    #[serde(default)]
    pub min_price_rise_bps: f64,
    #[serde(default)]
    pub max_price_impact_threshold: f64,
    pub volume_threshold: f64,
    pub min_cvd_notional_usd: f64,
    pub max_capacity: usize,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct SessionTrackingConfig {
    pub enabled: bool,
    pub risk_free_rate: f64,
    pub equity_snapshot_interval: usize,
    pub initial_equity: f64,
    pub estimated_ticks_per_day: f64,
}

impl AppConfig {
    pub fn load() -> Result<Self, config::ConfigError> {
        let builder = Config::builder()
            .add_source(File::with_name("config").required(true))
            .add_source(config::Environment::with_prefix("DELTA").separator("__"))
            .build()?;
        
        builder.try_deserialize()
    }
}

pub fn load_config() -> Result<AppConfig, config::ConfigError> {
    AppConfig::load()
}