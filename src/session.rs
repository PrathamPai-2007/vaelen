use chrono::{DateTime, Local};
use serde::{Deserialize, Serialize};
use serde_with::{serde_as, DisplayFromStr};
use std::collections::VecDeque;

const EQUITY_CURVE_CAPACITY: usize = 10000;
const TRADING_DAYS_PER_YEAR: f64 = 252.0;
const PERCENTAGE_MULTIPLIER: f64 = 100.0;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TradeRecord {
    pub symbol: String,
    pub entry_time: u64,
    pub side: String,
    pub entry_price: f64,
    pub exit_price: f64,
    pub exit_reason: String,
    pub gross_pnl: f64,
    pub fees: f64,
    pub slippage: f64,
    pub net_pnl: f64,
    pub size: i64,
    pub contract_size: f64,
}

#[serde_as]
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EquitySnapshot {
    #[serde_as(as = "DisplayFromStr")]
    pub timestamp: DateTime<Local>,
    pub equity: f64,
    pub drawdown: f64,
    pub peak_equity: f64,
}

pub struct SessionTracker {
    pub trades: Vec<TradeRecord>,
    pub equity_curve: VecDeque<EquitySnapshot>,
    pub peak_equity: f64,
    pub max_drawdown: f64,
    pub total_gross_pnl: f64,
    pub total_fees: f64,
    pub total_slippage: f64,
    pub total_net_pnl: f64,
    pub trades_entered: usize,
    pub trades_closed: usize,
    pub winning_trades: usize,
    pub losing_trades: usize,
    pub enabled: bool,
    pub risk_free_rate: f64,
    pub equity_snapshot_interval: usize,
    pub tick_counter: usize,
    pub initial_equity: f64,
    pub ticks_per_day_estimate: f64,
}

impl SessionTracker {
    pub fn new(enabled: bool, risk_free_rate: f64, equity_snapshot_interval: usize, initial_equity: f64, ticks_per_day_estimate: f64) -> Self {
        Self {
            trades: Vec::new(),
            equity_curve: VecDeque::with_capacity(EQUITY_CURVE_CAPACITY),
            peak_equity: initial_equity,
            max_drawdown: 0.0,
            total_gross_pnl: 0.0,
            total_fees: 0.0,
            total_slippage: 0.0,
            total_net_pnl: 0.0,
            trades_entered: 0,
            trades_closed: 0,
            winning_trades: 0,
            losing_trades: 0,
            enabled,
            risk_free_rate,
            equity_snapshot_interval,
            tick_counter: 0,
            initial_equity,
            ticks_per_day_estimate,
        }
    }

    pub fn record_trade(&mut self, trade: TradeRecord) {
        if !self.enabled {
            return;
        }

        self.trades.push(trade.clone());
        self.total_gross_pnl += trade.gross_pnl;
        self.total_fees += trade.fees;
        self.total_slippage += trade.slippage;
        self.total_net_pnl += trade.net_pnl;
        self.trades_closed += 1;

        if trade.net_pnl > 0.0 {
            self.winning_trades += 1;
        } else if trade.net_pnl < 0.0 {
            self.losing_trades += 1;
        }

        self.update_equity_snapshot();
    }

    pub fn record_trade_entry(&mut self) {
        if !self.enabled {
            return;
        }
        self.trades_entered += 1;
    }

    fn update_equity_snapshot(&mut self) {
        self.tick_counter += 1;
        if !self.tick_counter.is_multiple_of(self.equity_snapshot_interval) {
            return;
        }

        let current_equity = self.initial_equity + self.total_net_pnl;
        if current_equity > self.peak_equity {
            self.peak_equity = current_equity;
        }

        let drawdown = if self.peak_equity > 0.0 {
            (self.peak_equity - current_equity) / self.peak_equity
        } else {
            0.0
        };

        if drawdown > self.max_drawdown {
            self.max_drawdown = drawdown;
        }

        self.equity_curve.push_back(EquitySnapshot {
            timestamp: Local::now(),
            equity: current_equity,
            drawdown,
            peak_equity: self.peak_equity,
        });

        if self.equity_curve.len() > EQUITY_CURVE_CAPACITY {
            self.equity_curve.pop_front();
        }
    }

    pub fn tick(&mut self) {
        if !self.enabled {
            return;
        }
        self.update_equity_snapshot();
    }

    pub fn calculate_sharpe_ratio(&self) -> Option<f64> {
        if self.trades.is_empty() || self.equity_curve.len() < 2 {
            return None;
        }

        let returns: Vec<f64> = self.equity_curve
            .iter()
            .zip(self.equity_curve.iter().skip(1))
            .map(|(prev, curr)| {
                if prev.equity != 0.0 {
                    (curr.equity - prev.equity) / prev.equity.abs()
                } else {
                    0.0
                }
            })
            .collect();

        if returns.is_empty() {
            return None;
        }

        let mean_return = returns.iter().sum::<f64>() / returns.len() as f64;
        let variance = returns.iter()
            .map(|r| (r - mean_return).powi(2))
            .sum::<f64>() / returns.len() as f64;
        let std_dev = variance.sqrt();

        if std_dev == 0.0 {
            return None;
        }

        let snapshots_per_day = self.ticks_per_day_estimate / self.equity_snapshot_interval as f64;
        let annualization_factor = (TRADING_DAYS_PER_YEAR * snapshots_per_day).sqrt();
        let excess_return = mean_return - self.risk_free_rate / (TRADING_DAYS_PER_YEAR * snapshots_per_day);
        Some(excess_return / std_dev * annualization_factor)
    }

    pub fn calculate_sortino_ratio(&self) -> Option<f64> {
        if self.trades.is_empty() || self.equity_curve.len() < 2 {
            return None;
        }

        let returns: Vec<f64> = self.equity_curve
            .iter()
            .zip(self.equity_curve.iter().skip(1))
            .map(|(prev, curr)| {
                if prev.equity != 0.0 {
                    (curr.equity - prev.equity) / prev.equity.abs()
                } else {
                    0.0
                }
            })
            .collect();

        if returns.is_empty() {
            return None;
        }

        let mean_return = returns.iter().sum::<f64>() / returns.len() as f64;
        let downside_returns: Vec<f64> = returns.iter()
            .filter(|r| **r < 0.0)
            .cloned()
            .collect();

        if downside_returns.is_empty() {
            return Some(f64::INFINITY);
        }

        let downside_variance = downside_returns.iter()
            .map(|r| (r - mean_return).powi(2))
            .sum::<f64>() / downside_returns.len() as f64;
        let downside_dev = downside_variance.sqrt();

        if downside_dev == 0.0 {
            return None;
        }

        let snapshots_per_day = self.ticks_per_day_estimate / self.equity_snapshot_interval as f64;
        let annualization_factor = (TRADING_DAYS_PER_YEAR * snapshots_per_day).sqrt();
        let excess_return = mean_return - self.risk_free_rate / (TRADING_DAYS_PER_YEAR * snapshots_per_day);
        Some(excess_return / downside_dev * annualization_factor)
    }

    pub fn win_rate(&self) -> f64 {
        if self.trades_closed == 0 {
            0.0
        } else {
            self.winning_trades as f64 / self.trades_closed as f64
        }
    }

    pub fn avg_win(&self) -> f64 {
        let wins: Vec<f64> = self.trades.iter()
            .filter(|t| t.net_pnl > 0.0)
            .map(|t| t.net_pnl)
            .collect();
        if wins.is_empty() { 0.0 } else { wins.iter().sum::<f64>() / wins.len() as f64 }
    }

    pub fn avg_loss(&self) -> f64 {
        let losses: Vec<f64> = self.trades.iter()
            .filter(|t| t.net_pnl < 0.0)
            .map(|t| t.net_pnl)
            .collect();
        if losses.is_empty() { 0.0 } else { losses.iter().sum::<f64>() / losses.len() as f64 }
    }

    pub fn profit_factor(&self) -> f64 {
        let gross_profit: f64 = self.trades.iter()
            .filter(|t| t.net_pnl > 0.0)
            .map(|t| t.net_pnl)
            .sum();
        let gross_loss: f64 = self.trades.iter()
            .filter(|t| t.net_pnl < 0.0)
            .map(|t| t.net_pnl.abs())
            .sum();
        if gross_loss == 0.0 { f64::INFINITY } else { gross_profit / gross_loss }
    }

    pub fn print_summary(&self) {
        println!("\n=== SESSION TRADING SUMMARY ===");
        println!("Trades Entered:      {}", self.trades_entered);
        println!("Trades Closed:       {}", self.trades_closed);
        println!("Winning Trades:      {}", self.winning_trades);
        println!("Losing Trades:       {}", self.losing_trades);
        println!("Win Rate:            {:.2}%", self.win_rate() * PERCENTAGE_MULTIPLIER);
        println!("Avg Win:             {:.5} USD", self.avg_win());
        println!("Avg Loss:            {:.5} USD", self.avg_loss());
        println!("Profit Factor:       {:.2}", self.profit_factor());
        println!("Gross PnL:           {:.5} USD", self.total_gross_pnl);
        println!("Total Fees:          {:.5} USD", self.total_fees);
        println!("Total Slippage:      {:.5} USD", self.total_slippage);
        println!("Net PnL:             {:.5} USD", self.total_net_pnl);
        println!("Max Drawdown:        {:.2}%", self.max_drawdown * PERCENTAGE_MULTIPLIER);

        if let Some(sharpe) = self.calculate_sharpe_ratio() {
            println!("Sharpe Ratio:        {:.2}", sharpe);
        }

        if let Some(sortino) = self.calculate_sortino_ratio() {
            if sortino.is_infinite() {
                println!("Sortino Ratio:       INF (no downside volatility)");
            } else {
                println!("Sortino Ratio:       {:.2}", sortino);
            }
        }

        println!("===============================\n");
    }

    pub fn export_equity_curve(&self, path: &str) -> std::io::Result<()> {
        use std::fs::OpenOptions;
        use std::io::{BufWriter, Write};

        let file = OpenOptions::new().create(true).write(true).truncate(true).open(path)?;
        let mut writer = BufWriter::new(file);
        writeln!(writer, "timestamp,equity,drawdown,peak_equity")?;
        for snap in &self.equity_curve {
            writeln!(writer, "{},{:.5},{:.5},{:.5}",
                snap.timestamp.format("%Y-%m-%d %H:%M:%S"),
                snap.equity, snap.drawdown, snap.peak_equity)?;
        }
        writer.flush()?;
        Ok(())
    }

    pub fn export_trades(&self, path: &str) -> std::io::Result<()> {
        use std::fs::OpenOptions;
        use std::io::{BufWriter, Write};

        let file = OpenOptions::new().create(true).write(true).truncate(true).open(path)?;
        let mut writer = BufWriter::new(file);
        writeln!(writer, "symbol,entry_time,side,entry_price,exit_price,exit_reason,gross_pnl,fees,slippage,net_pnl,size,contract_size")?;
        for trade in &self.trades {
            writeln!(writer, "{},{},{},{:.5},{:.5},{},{:.5},{:.5},{:.5},{:.5},{},{}",
                trade.symbol,
                trade.entry_time,
                trade.side,
                trade.entry_price,
                trade.exit_price,
                trade.exit_reason,
                trade.gross_pnl,
                trade.fees,
                trade.slippage,
                trade.net_pnl,
                trade.size,
                trade.contract_size)?;
        }
        writer.flush()?;
        Ok(())
    }
}