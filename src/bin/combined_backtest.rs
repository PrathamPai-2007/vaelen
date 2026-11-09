use delta_trading_engine::bar_strategies::{
    combine, Candle, JurikTrend, Signal, SmartMoneyFlow, TrendState,
};
use std::env;
use std::fs;

#[repr(C)]
struct CandleRecord {
    timestamp: u64,
    open: f64,
    high: f64,
    low: f64,
    close: f64,
    volume: f64,
    funding_rate: f64,
}

struct Position {
    side: TrendState,
    contracts: i64,
    entry: f64,
}

fn contract_value(symbol: &str) -> Option<f64> {
    Some(match symbol {
        "BTCUSD" => 0.001,
        "ETHUSD" => 0.01,
        "BNBUSD" => 0.1,
        "XRPUSD" => 1.0,
        "DOGEUSD" => 100.0,
        "LINKUSD" => 1.0,
        _ => return None,
    })
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = env::args().collect();
    if args.len() < 3 {
        eprintln!("Usage: combined_backtest <symbol> <candle_data_path> [interval_secs] [strategy] [target_notional]");
        return Ok(());
    }
    let symbol = &args[1];
    let contract = contract_value(symbol).ok_or("unsupported symbol")?;
    let interval: u64 = args.get(3).map_or(Ok(300), |value| value.parse())?;
    if interval != 300 {
        return Err("Delta candle input must use a 300-second interval".into());
    }
    let strategy = args.get(4).map(String::as_str).unwrap_or("combined");
    if !matches!(strategy, "jurik" | "flow" | "combined") {
        return Err("strategy must be jurik, flow, or combined".into());
    }
    let target_notional: f64 = args.get(5).map_or(Ok(1_000.0), |value| value.parse())?;
    if !target_notional.is_finite() || target_notional <= 0.0 {
        return Err("target_notional must be positive".into());
    }

    let bytes = fs::read(&args[2])?;
    let record_size = std::mem::size_of::<CandleRecord>();
    if bytes.len() % record_size != 0 {
        return Err("binary data is not aligned to the candle record size".into());
    }
    let records = unsafe {
        std::slice::from_raw_parts(
            bytes.as_ptr() as *const CandleRecord,
            bytes.len() / record_size,
        )
    };
    let mut jurik = JurikTrend::new(14, 1.5, 14, 1.5, 100)?;
    let mut flow = SmartMoneyFlow::new(10, 21, 14, 4)?;
    let mut previous_jurik = None;
    let mut position: Option<Position> = None;
    // Signal computed on close(t), executed at open(t+1): no lookahead.
    let mut pending: Option<TrendState> = None;
    let mut pending_exit = false;
    let mut gross_pnl = 0.0;
    let mut fees = 0.0;
    let mut funding_pnl = 0.0;
    let mut signals = 0usize;
    let fee_rate = 0.0005 * 1.18;
    let slippage = 5.0 / 10_000.0;
    const WARMUP_BARS: usize = 100;

    for (idx, record) in records.iter().enumerate() {
        let candle = Candle {
            timestamp: record.timestamp,
            open: record.open,
            high: record.high,
            low: record.low,
            close: record.close,
            volume: record.volume,
        };
        if let Some(open) = &position {
            if record.funding_rate != 0.0 {
                let notional = record.close * contract * open.contracts as f64;
                funding_pnl += if open.side == TrendState::Green {
                    -notional * record.funding_rate
                } else {
                    notional * record.funding_rate
                };
            }
        }
        let jurik_output = jurik.update(candle);
        let flow_output = flow.update(candle);
        let signal = match strategy {
            "jurik" => jurik_output.signal,
            "flow" => flow_output.signal,
            _ => combine(jurik_output, flow_output, previous_jurik),
        };
        // Execute last bar's pending signal at this bar's open (skip warmup).
        if idx >= WARMUP_BARS {
            if pending_exit {
                if let Some(open) = position.take() {
                    let exit = if open.side == TrendState::Green {
                        candle.open * (1.0 - slippage)
                    } else {
                        candle.open * (1.0 + slippage)
                    };
                    gross_pnl += if open.side == TrendState::Green {
                        (exit - open.entry) * contract * open.contracts as f64
                    } else {
                        (open.entry - exit) * contract * open.contracts as f64
                    };
                    fees += exit * contract * open.contracts as f64 * fee_rate;
                }
            } else if let Some(side) = pending.take() {
                // Close opposite before flipping (no phantom round-trip when flat).
                if let Some(open) = position.take() {
                    let exit = if open.side == TrendState::Green {
                        candle.open * (1.0 - slippage)
                    } else {
                        candle.open * (1.0 + slippage)
                    };
                    gross_pnl += if open.side == TrendState::Green {
                        (exit - open.entry) * contract * open.contracts as f64
                    } else {
                        (open.entry - exit) * contract * open.contracts as f64
                    };
                    fees += exit * contract * open.contracts as f64 * fee_rate;
                }
                let contracts = (target_notional / (candle.open * contract))
                    .floor()
                    .max(1.0) as i64;
                let entry = if side == TrendState::Green {
                    candle.open * (1.0 + slippage)
                } else {
                    candle.open * (1.0 - slippage)
                };
                fees += entry * contract * contracts as f64 * fee_rate;
                position = Some(Position {
                    side,
                    contracts,
                    entry,
                });
            }
        } else {
            pending = None;
            pending_exit = false;
        }
        if let Some(signal) = signal {
            signals += 1;
            match signal {
                Signal::Buy => {
                    pending = Some(TrendState::Green);
                    pending_exit = false;
                }
                Signal::Sell => {
                    pending = Some(TrendState::Red);
                    pending_exit = false;
                }
                Signal::Exit => {
                    pending = None;
                    // Only a real exit if a position is open (avoids phantom fees).
                    pending_exit = position.is_some();
                }
            }
        }
        previous_jurik = jurik_output.state;
    }
    if let Some(open) = position {
        let exit = if open.side == TrendState::Green {
            records.last().unwrap().close * (1.0 - slippage)
        } else {
            records.last().unwrap().close * (1.0 + slippage)
        };
        gross_pnl += if open.side == TrendState::Green {
            (exit - open.entry) * contract * open.contracts as f64
        } else {
            (open.entry - exit) * contract * open.contracts as f64
        };
        fees += exit * contract * open.contracts as f64 * fee_rate;
    }
    let net_pnl = gross_pnl + funding_pnl - fees;
    println!("{{\"symbol\":\"{}\",\"strategy\":\"{}\",\"candles\":{},\"signals\":{},\"contract_value\":{},\"target_notional\":{},\"gross_pnl\":{:.8},\"fees_incl_gst\":{:.8},\"funding_pnl\":{:.8},\"net_pnl\":{:.8},\"return_on_10000\":{:.8}}}", symbol, strategy, records.len(), signals, contract, target_notional, gross_pnl, fees, funding_pnl, net_pnl, net_pnl / 100.0);
    Ok(())
}
