use config::{Config, File};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Deserialize, Serialize, Default)]
pub struct TelegramConfig {
    #[serde(default)]
    pub enabled: bool,
    #[serde(default)]
    pub bot_token: String,
    #[serde(default)]
    pub chat_id: String,
}

#[derive(Debug, Clone, Deserialize, Serialize, Default)]
pub struct NotificationsConfig {
    #[serde(default)]
    pub telegram: TelegramConfig,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct AppConfig {
    pub general: GeneralConfig,
    pub websocket: WebSocketConfig,
    pub order_manager: OrderManagerConfig,
    pub fees: FeesConfig,
    pub strategy: StrategyConfig,
    pub session_tracking: SessionTrackingConfig,
    #[serde(default)]
    pub notifications: NotificationsConfig,
}

fn default_strategy_type() -> String { "cvd_iceberg".to_string() }

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
    #[serde(default = "default_max_consecutive_failures")]
    pub max_consecutive_failures: u32,
}

fn default_max_consecutive_failures() -> u32 { 3 }

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

    #[serde(default = "default_strategy_type")]
    pub strategy_type: String,
    
    #[serde(default)]
    pub script_path: Option<String>,
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
    pub fn validate(&self) -> Result<(), String> {
        if self.general.api_base_url.trim().is_empty() {
            return Err("api_base_url cannot be empty".to_string());
        }
        if url::Url::parse(&self.general.api_base_url).is_err() {
            return Err(format!("api_base_url '{}' is not a valid URL", self.general.api_base_url));
        }

        if self.fees.maker_fee_rate < 0.0 || self.fees.maker_fee_rate > 1.0 {
            return Err("maker_fee_rate must be between 0 and 1".to_string());
        }
        if self.fees.taker_fee_rate < 0.0 || self.fees.taker_fee_rate > 1.0 {
            return Err("taker_fee_rate must be between 0 and 1".to_string());
        }

        Ok(())
    }

    pub fn load() -> anyhow::Result<Self> {
        Self::load_from_path("config")
    }

    pub fn load_from_path(path: &str) -> anyhow::Result<Self> {
        let builder = Config::builder()
            .add_source(File::with_name(path).required(true))
            .add_source(config::Environment::with_prefix("DELTA").separator("__"))
            .build()?;
        
        let config: Self = builder.try_deserialize()?;
        config.validate().map_err(|e| anyhow::anyhow!("Configuration validation failed: {}", e))?;
        Ok(config)
    }
}

pub fn load_config() -> anyhow::Result<AppConfig> {
    AppConfig::load()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn valid_config() -> AppConfig {
        AppConfig {
            general: GeneralConfig {
                api_base_url: "https://api.delta.exchange".to_string(),
                paper_trade_mode: true,
                max_concurrent_positions: 1,
                trade_channel_buffer: 1000,
                trades_dir: "trades".to_string(),
                log_level: "info".to_string(),
                json_logs: false,
                log_file: None,
            },
            websocket: WebSocketConfig {
                ws_url: "wss://socket.delta.exchange".to_string(),
                symbols: vec![],
                ping_interval_secs: 30,
                watchdog_timeout_secs: 10,
                reconnect_base_secs: 1,
                reconnect_max_secs: 30,
                user_agent: "test".to_string(),
            },
            order_manager: OrderManagerConfig {
                request_timeout_secs: 10,
                max_retries: 3,
                retry_base_delay_secs: 1,
                retry_max_delay_secs: 30,
                retry_on_status: vec![],
                track_fills: false,
                fill_timeout_secs: 10,
                max_consecutive_failures: 3,
            },
            fees: FeesConfig {
                taker_fee_rate: 0.0005,
                maker_fee_rate: 0.0002,
                slippage_bps: 2.0,
            },
            strategy: StrategyConfig { symbols: vec![] },
            session_tracking: SessionTrackingConfig {
                enabled: false,
                risk_free_rate: 0.0,
                equity_snapshot_interval: 60,
                initial_equity: 1000.0,
                estimated_ticks_per_day: 1000.0,
            },
        }
    }

    #[test]
    fn test_valid_config_passes() {
        let config = valid_config();
        assert!(config.validate().is_ok());
    }

    #[test]
    fn test_invalid_url() {
        let mut config = valid_config();
        config.general.api_base_url = "not a url".to_string();
        let err = config.validate().unwrap_err();
        assert!(err.contains("not a valid URL"));

        config.general.api_base_url = "".to_string();
        let err = config.validate().unwrap_err();
        assert!(err.contains("cannot be empty"));
    }

    #[test]
    fn test_invalid_fees() {
        let mut config = valid_config();
        config.fees.maker_fee_rate = 1.5;
        let err = config.validate().unwrap_err();
        assert!(err.contains("maker_fee_rate must be between 0 and 1"));

        let mut config2 = valid_config();
        config2.fees.taker_fee_rate = -0.1;
        let err = config2.validate().unwrap_err();
        assert!(err.contains("taker_fee_rate must be between 0 and 1"));
    }
}