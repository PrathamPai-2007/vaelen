use config::{Config, File};
use serde::{Deserialize, Serialize};

#[derive(Clone, Deserialize, Serialize, Default)]
pub struct TelegramConfig {
    #[serde(default)]
    pub enabled: bool,
    #[serde(default)]
    pub bot_token: String,
    #[serde(default)]
    pub chat_id: String,
}

impl std::fmt::Debug for TelegramConfig {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("TelegramConfig")
            .field("enabled", &self.enabled)
            .field("bot_token", &"<redacted>")
            .field("chat_id", &"<redacted>")
            .finish()
    }
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

fn default_strategy_type() -> String {
    "cvd_iceberg".to_string()
}

fn default_target_notional() -> f64 {
    2000.0
}

fn default_max_leverage() -> f64 {
    10.0
}

fn default_candle_mins() -> u64 {
    5
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
    #[serde(default = "default_max_consecutive_failures")]
    pub max_consecutive_failures: u32,
}

fn default_max_consecutive_failures() -> u32 {
    3
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
    /// Fixed USD notional per entry. Contracts = floor(target / (price*contract_size)).
    #[serde(default = "default_target_notional")]
    pub target_notional_usd: f64,
    /// Exchange leverage ceiling (headroom only; effective stays ~0.4x).
    #[serde(default = "default_max_leverage")]
    pub max_leverage: f64,
    #[serde(default = "default_candle_mins")]
    pub candle_mins: u64,
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
    #[serde(alias = "ticks_per_day_estimate")]
    pub estimated_ticks_per_day: f64,
}

impl AppConfig {
    pub fn validate(&self) -> Result<(), String> {
        if self.general.api_base_url.trim().is_empty() {
            return Err("api_base_url cannot be empty".to_string());
        }
        if url::Url::parse(&self.general.api_base_url).is_err() {
            return Err(format!(
                "api_base_url '{}' is not a valid URL",
                self.general.api_base_url
            ));
        }

        if self.websocket.symbols.is_empty() {
            return Err("websocket.symbols must contain at least one symbol".to_string());
        }
        if self.general.trade_channel_buffer == 0 {
            return Err("trade_channel_buffer must be greater than zero".to_string());
        }
        if self.general.max_concurrent_positions == 0 {
            return Err("max_concurrent_positions must be greater than zero".to_string());
        }
        if self.session_tracking.equity_snapshot_interval == 0 {
            return Err("equity_snapshot_interval must be greater than zero".to_string());
        }
        if self.websocket.reconnect_base_secs == 0
            || self.websocket.reconnect_max_secs < self.websocket.reconnect_base_secs
        {
            return Err("reconnect backoff must be positive and max must be >= base".to_string());
        }
        if url::Url::parse(&self.websocket.ws_url).is_err() {
            return Err(format!(
                "websocket.ws_url '{}' is not a valid URL",
                self.websocket.ws_url
            ));
        }
        if self
            .websocket
            .user_agent
            .parse::<reqwest::header::HeaderValue>()
            .is_err()
        {
            return Err("websocket.user_agent is not a valid header value".to_string());
        }
        if self.general.trade_channel_buffer > 1_000_000 {
            return Err("trade_channel_buffer unreasonably large (>1M)".to_string());
        }
        if !(0.0..500.0).contains(&self.fees.slippage_bps) {
            return Err("slippage_bps must be in [0, 500)".to_string());
        }
        if self.order_manager.request_timeout_secs == 0 || self.order_manager.fill_timeout_secs == 0
        {
            return Err("request_timeout_secs and fill_timeout_secs must be > 0".to_string());
        }
        if self.notifications.telegram.enabled
            && (self.notifications.telegram.bot_token.is_empty()
                || self.notifications.telegram.chat_id.is_empty())
        {
            return Err("telegram enabled requires bot_token and chat_id".to_string());
        }
        // Live universe: BTCUSD + ETHUSD only. Meme/alt symbols are research-only.
        for symbol in &self.strategy.symbols {
            let upper = symbol.symbol.to_ascii_uppercase();
            if !["BTCUSD", "ETHUSD"].contains(&upper.as_str()) {
                return Err(format!(
                    "{}: live universe is BTCUSD/ETHUSD only (no memecoins)",
                    symbol.symbol
                ));
            }
            if symbol.product_id == 0 {
                return Err(format!("{}: product_id must be > 0", symbol.symbol));
            }
            if symbol.hold_ticks == 0 {
                return Err(format!("{}: hold_ticks must be > 0", symbol.symbol));
            }
            if symbol.trailing_stop_atr_mult <= 0.0 || !symbol.trailing_stop_atr_mult.is_finite() {
                return Err(format!(
                    "{}: trailing_stop_atr_mult must be positive",
                    symbol.symbol
                ));
            }
            if symbol.target_notional_usd <= 0.0 || !symbol.target_notional_usd.is_finite() {
                return Err(format!(
                    "{}: target_notional_usd must be positive",
                    symbol.symbol
                ));
            }
            if !(1.0..=50.0).contains(&symbol.max_leverage) {
                return Err(format!(
                    "{}: max_leverage must be in [1, 50]",
                    symbol.symbol
                ));
            }
            if symbol.candle_mins == 0 || symbol.candle_mins > 60 {
                return Err(format!("{}: candle_mins must be in [1, 60]", symbol.symbol));
            }
            if symbol.strategy_type != "cvd_iceberg" {
                return Err(format!(
                    "{}: unknown strategy_type '{}'",
                    symbol.symbol, symbol.strategy_type
                ));
            }
            if let Some(path) = &symbol.script_path {
                if !std::path::Path::new(path).starts_with("strategies") {
                    return Err(format!(
                        "{}: script_path must live under strategies/",
                        symbol.symbol
                    ));
                }
            }
        }
        // WS <-> strategy symbol sets must match or trades silently drop.
        {
            use std::collections::HashSet;
            let ws: HashSet<String> = self
                .websocket
                .symbols
                .iter()
                .map(|s| s.to_ascii_uppercase())
                .collect();
            let st: HashSet<String> = self
                .strategy
                .symbols
                .iter()
                .map(|s| s.symbol.to_ascii_uppercase())
                .collect();
            if ws != st {
                return Err(format!(
                    "websocket.symbols {:?} must equal strategy symbols {:?}",
                    self.websocket.symbols,
                    self.strategy
                        .symbols
                        .iter()
                        .map(|s| &s.symbol)
                        .collect::<Vec<_>>()
                ));
            }
        }

        for symbol in &self.strategy.symbols {
            if symbol.max_capacity == 0 {
                return Err(format!(
                    "{}: max_capacity must be greater than zero",
                    symbol.symbol
                ));
            }
            if symbol.lookback_ticks == 0 || symbol.lookback_ticks >= symbol.max_capacity {
                return Err(format!(
                    "{}: lookback_ticks must be greater than zero and less than max_capacity",
                    symbol.symbol
                ));
            }
            if symbol.atr_period == 0 {
                return Err(format!(
                    "{}: atr_period must be greater than zero",
                    symbol.symbol
                ));
            }
            if symbol.order_size <= 0 {
                return Err(format!(
                    "{}: order_size must be greater than zero",
                    symbol.symbol
                ));
            }
            if !symbol.contract_size.is_finite() || symbol.contract_size <= 0.0 {
                return Err(format!("{}: contract_size must be positive", symbol.symbol));
            }
            if !symbol.tick_size.is_finite() || symbol.tick_size <= 0.0 {
                return Err(format!("{}: tick_size must be positive", symbol.symbol));
            }
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
        config
            .validate()
            .map_err(|e| anyhow::anyhow!("Configuration validation failed: {}", e))?;
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
                symbols: vec!["BTCUSD".to_string()],
                ping_interval_secs: 30,
                watchdog_timeout_secs: 10,
                reconnect_base_secs: 1,
                reconnect_max_secs: 30,
                user_agent: "Vaelen/0.1.0".to_string(),
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
            strategy: StrategyConfig {
                symbols: vec![SymbolConfig {
                    symbol: "BTCUSD".to_string(),
                    product_id: 27,
                    contract_size: 0.001,
                    order_size: 1,
                    target_notional_usd: 2000.0,
                    max_leverage: 10.0,
                    candle_mins: 5,
                    tick_size: 0.5,
                    stop_loss_bps: 15.0,
                    take_profit_bps: 45.0,
                    hold_ticks: 600,
                    entry_cooldown_ticks: 2000,
                    trailing_stop_atr_mult: 1.5,
                    min_trailing_stop_distance: 1.0,
                    atr_period: 14,
                    lookback_ticks: 24,
                    min_price_drop_bps: 0.0,
                    min_price_rise_bps: 0.0,
                    max_price_impact_threshold: 1e-6,
                    volume_threshold: 0.5,
                    min_cvd_notional_usd: 10000.0,
                    max_capacity: 100,
                    strategy_type: "cvd_iceberg".to_string(),
                    script_path: None,
                }],
            },
            session_tracking: SessionTrackingConfig {
                enabled: false,
                risk_free_rate: 0.0,
                equity_snapshot_interval: 60,
                initial_equity: 1000.0,
                estimated_ticks_per_day: 1000.0,
            },
            notifications: NotificationsConfig::default(),
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
