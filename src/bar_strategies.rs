use std::collections::VecDeque;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Candle {
    pub timestamp: u64,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TrendState {
    Green,
    Red,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Signal {
    Buy,
    Sell,
    Exit,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct StrategyOutput {
    pub state: Option<TrendState>,
    pub signal: Option<Signal>,
    pub value: f64,
    pub ready: bool,
}

pub struct CandleAggregator {
    interval_secs: u64,
    current: Option<Candle>,
}

impl CandleAggregator {
    pub fn new(interval_secs: u64) -> Result<Self, String> {
        if interval_secs == 0 {
            return Err("candle interval must be greater than zero".to_string());
        }
        Ok(Self {
            interval_secs,
            current: None,
        })
    }

    pub fn push(&mut self, timestamp_ns: u64, price: f64, volume: f64) -> Option<Candle> {
        if !price.is_finite() || price <= 0.0 || !volume.is_finite() || volume < 0.0 {
            return None;
        }
        let timestamp = timestamp_ns / 1_000_000_000;
        let bucket = (timestamp / self.interval_secs) * self.interval_secs;
        match &mut self.current {
            None => {
                self.current = Some(Candle {
                    timestamp: bucket,
                    open: price,
                    high: price,
                    low: price,
                    close: price,
                    volume,
                });
            }
            Some(candle) if candle.timestamp == bucket => {
                candle.high = candle.high.max(price);
                candle.low = candle.low.min(price);
                candle.close = price;
                candle.volume += volume;
            }
            Some(candle) if bucket > candle.timestamp => {
                let completed = *candle;
                *candle = Candle {
                    timestamp: bucket,
                    open: price,
                    high: price,
                    low: price,
                    close: price,
                    volume,
                };
                return Some(completed);
            }
            Some(_) => {}
        }
        None
    }

    pub fn finish(&mut self) -> Option<Candle> {
        self.current.take()
    }
}

#[derive(Clone, Copy)]
struct JurikFilter {
    stage_one: f64,
    detrend: f64,
    stage_three: f64,
    result: f64,
    initialized: bool,
}

impl Default for JurikFilter {
    fn default() -> Self {
        Self {
            stage_one: 0.0,
            detrend: 0.0,
            stage_three: 0.0,
            result: 0.0,
            initialized: false,
        }
    }
}

impl JurikFilter {
    fn update(&mut self, source: f64, alpha: f64, beta: f64, phase: f64) -> f64 {
        let phase_ratio = (phase / 100.0 + 1.5).clamp(0.5, 2.5);
        if !self.initialized {
            self.stage_one = source;
            self.result = source;
            self.initialized = true;
            return source;
        }
        let inverse_alpha = 1.0 - alpha;
        self.stage_one = inverse_alpha * source + alpha * self.stage_one;
        self.detrend = (source - self.stage_one) * (1.0 - beta) + beta * self.detrend;
        let phase_shift = self.stage_one + phase_ratio * self.detrend;
        self.stage_three =
            (phase_shift - self.result) * inverse_alpha.powi(2) + alpha.powi(2) * self.stage_three;
        self.result += self.stage_three;
        self.result
    }
}

pub struct JurikTrend {
    length: usize,
    power: f64,
    atr_length: usize,
    multiplier: f64,
    warmup: usize,
    filters: [JurikFilter; 15],
    true_ranges: VecDeque<f64>,
    previous_close: Option<f64>,
    atr: f64,
    ratchet: Option<f64>,
    state: TrendState,
    bars: usize,
}

impl JurikTrend {
    pub fn new(
        length: usize,
        power: f64,
        atr_length: usize,
        multiplier: f64,
        warmup: usize,
    ) -> Result<Self, String> {
        if length < 2 || atr_length == 0 || power <= 0.0 || multiplier <= 0.0 {
            return Err("invalid Jurik parameters".to_string());
        }
        Ok(Self {
            length,
            power,
            atr_length,
            multiplier,
            warmup,
            filters: [JurikFilter::default(); 15],
            true_ranges: VecDeque::with_capacity(atr_length),
            previous_close: None,
            atr: 0.0,
            ratchet: None,
            state: TrendState::Green,
            bars: 0,
        })
    }

    pub fn update(&mut self, candle: Candle) -> StrategyOutput {
        let (alpha, beta) = jurik_coefficients(self.length, self.power);
        let phases = [
            0.0, 100.0, 86.0, 71.0, 57.0, 43.0, 29.0, 14.0, -14.0, -29.0, -43.0, -57.0, -71.0,
            -86.0, -100.0,
        ];
        let mut values = [0.0; 15];
        for (value, (filter, phase)) in values.iter_mut().zip(self.filters.iter_mut().zip(phases)) {
            *value = filter.update(candle.close, alpha, beta, phase);
        }

        let true_range = self
            .previous_close
            .map_or(candle.high - candle.low, |previous| {
                (candle.high - candle.low)
                    .max((candle.high - previous).abs())
                    .max((candle.low - previous).abs())
            });
        self.previous_close = Some(candle.close);
        self.true_ranges.push_back(true_range);
        if self.true_ranges.len() > self.atr_length {
            self.true_ranges.pop_front();
        }
        self.atr = self.true_ranges.iter().sum::<f64>() / self.true_ranges.len() as f64;
        self.bars += 1;

        let anchor = values[0];
        let upper = anchor + self.atr * self.multiplier;
        let lower = anchor - self.atr * self.multiplier;
        let previous = self.state;
        match self.ratchet {
            None => self.ratchet = Some(anchor),
            Some(level) if upper < level => {
                self.ratchet = Some(upper);
                self.state = TrendState::Red;
            }
            Some(level) if lower > level => {
                self.ratchet = Some(lower);
                self.state = TrendState::Green;
            }
            Some(_) => {}
        }
        let ready = self.bars >= self.warmup;
        let signal = if ready && self.state != previous {
            Some(if self.state == TrendState::Green {
                Signal::Buy
            } else {
                Signal::Sell
            })
        } else {
            None
        };
        StrategyOutput {
            state: ready.then_some(self.state),
            signal,
            value: anchor,
            ready,
        }
    }

    pub fn state(&self) -> TrendState {
        self.state
    }
}

fn jurik_coefficients(length: usize, power: f64) -> (f64, f64) {
    let length = length as f64;
    let beta = 0.45 * (length - 1.0) / (0.45 * (length - 1.0) + 2.0);
    (beta.powf(power), beta)
}

pub struct SmartMoneyFlow {
    momentum_period: usize,
    trend_period: usize,
    mfi_period: usize,
    smoothing: usize,
    candles: VecDeque<Candle>,
    composite: VecDeque<f64>,
    previous_composite: Option<f64>,
}

impl SmartMoneyFlow {
    pub fn new(
        momentum_period: usize,
        trend_period: usize,
        mfi_period: usize,
        smoothing: usize,
    ) -> Result<Self, String> {
        if momentum_period == 0 || trend_period == 0 || mfi_period == 0 || smoothing == 0 {
            return Err("Smart Money Flow periods must be greater than zero".to_string());
        }
        Ok(Self {
            momentum_period,
            trend_period,
            mfi_period,
            smoothing,
            candles: VecDeque::new(),
            composite: VecDeque::new(),
            previous_composite: None,
        })
    }

    pub fn update(&mut self, candle: Candle) -> StrategyOutput {
        self.candles.push_back(candle);
        let capacity = self
            .trend_period
            .max(self.mfi_period)
            .max(self.momentum_period * 2)
            + 2;
        while self.candles.len() > capacity {
            self.candles.pop_front();
        }
        let source = |c: &Candle| (c.high + c.low + c.close) / 3.0;
        let momentum_average = ema(self.candles.iter().map(source), self.momentum_period);
        let deviation = ema(
            self.candles
                .iter()
                .map(|c| (source(c) - momentum_average).abs()),
            self.momentum_period,
        );
        let momentum_wave = ema(
            self.candles.iter().map(|c| {
                let src = source(c);
                if deviation > 0.0 {
                    (src - momentum_average) / (0.015 * deviation)
                } else {
                    0.0
                }
            }),
            self.trend_period,
        );
        let (positive, negative) = self.candles.iter().rev().take(self.mfi_period).fold(
            (0.0, 0.0),
            |(positive, negative), c| {
                if c.close >= c.open {
                    (positive + source(c) * c.volume, negative)
                } else {
                    (positive, negative + source(c) * c.volume)
                }
            },
        );
        let mfi = if negative > 0.0 {
            100.0 - 100.0 / (1.0 + positive / negative)
        } else {
            100.0
        };
        let cmf = chaikin_flow(&self.candles, self.trend_period);
        let value = momentum_wave * 0.5 + cmf * 100.0 * 0.3 + (mfi - 50.0) * 1.2 * 0.2;
        self.composite.push_back(value);
        if self.composite.len() > self.smoothing {
            self.composite.pop_front();
        }
        let smoothed = self.composite.iter().sum::<f64>() / self.composite.len() as f64;
        let previous = self.previous_composite.replace(smoothed);
        let signal = match previous {
            Some(previous) if previous <= 0.0 && smoothed > 0.0 => Some(Signal::Buy),
            Some(previous) if previous >= 0.0 && smoothed < 0.0 => Some(Signal::Sell),
            _ => None,
        };
        StrategyOutput {
            state: Some(if smoothed >= 0.0 {
                TrendState::Green
            } else {
                TrendState::Red
            }),
            signal,
            value: smoothed,
            ready: self.candles.len() >= self.trend_period,
        }
    }
}

fn ema(values: impl Iterator<Item = f64>, period: usize) -> f64 {
    let mut result = None;
    let alpha = 2.0 / (period as f64 + 1.0);
    for value in values {
        result = Some(result.map_or(value, |previous: f64| {
            alpha * value + (1.0 - alpha) * previous
        }));
    }
    result.unwrap_or(0.0)
}

fn chaikin_flow(candles: &VecDeque<Candle>, period: usize) -> f64 {
    let (money_flow, volume) =
        candles
            .iter()
            .rev()
            .take(period)
            .fold((0.0, 0.0), |(flow, volume), candle| {
                let multiplier = if candle.high != candle.low {
                    (2.0 * candle.close - candle.low - candle.high) / (candle.high - candle.low)
                } else {
                    0.0
                };
                (flow + multiplier * candle.volume, volume + candle.volume)
            });
    if volume > 0.0 {
        money_flow / volume
    } else {
        0.0
    }
}

pub fn combine(
    jurik: StrategyOutput,
    flow: StrategyOutput,
    previous_jurik: Option<TrendState>,
) -> Option<Signal> {
    if !jurik.ready || !flow.ready {
        return None;
    }
    if previous_jurik.is_some_and(|previous| previous != jurik.state.unwrap_or(previous)) {
        return Some(Signal::Exit);
    }
    match (jurik.state, flow.signal) {
        (Some(TrendState::Green), Some(Signal::Buy)) => Some(Signal::Buy),
        (Some(TrendState::Red), Some(Signal::Sell)) => Some(Signal::Sell),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn aggregator_emits_only_completed_candles() {
        let mut aggregator = CandleAggregator::new(60).unwrap();
        assert!(aggregator.push(1_000_000_000, 100.0, 2.0).is_none());
        assert!(aggregator.push(30_000_000_000, 101.0, 3.0).is_none());
        let candle = aggregator.push(61_000_000_000, 99.0, 4.0).unwrap();
        assert_eq!(candle.open, 100.0);
        assert_eq!(candle.high, 101.0);
        assert_eq!(candle.low, 100.0);
        assert_eq!(candle.volume, 5.0);
    }

    #[test]
    fn jurik_warms_up_and_keeps_state() {
        let mut strategy = JurikTrend::new(8, 1.0, 3, 1.0, 3).unwrap();
        let candle = Candle {
            timestamp: 0,
            open: 100.0,
            high: 101.0,
            low: 99.0,
            close: 100.0,
            volume: 10.0,
        };
        assert!(!strategy.update(candle).ready);
        assert!(!strategy.update(candle).ready);
        assert!(strategy.update(candle).ready);
        assert_eq!(strategy.state(), TrendState::Green);
    }

    fn out(state: Option<TrendState>, signal: Option<Signal>, ready: bool) -> StrategyOutput {
        StrategyOutput {
            state,
            signal,
            value: 0.0,
            ready,
        }
    }

    #[test]
    fn combine_requires_both_strategies_ready() {
        let j = out(Some(TrendState::Green), None, false);
        let f = out(None, Some(Signal::Buy), true);
        assert_eq!(combine(j, f, None), None);
    }

    #[test]
    fn combine_trend_flip_exits_before_anything_else() {
        let j = out(Some(TrendState::Red), None, true);
        let f = out(None, Some(Signal::Buy), true);
        assert_eq!(combine(j, f, Some(TrendState::Green)), Some(Signal::Exit));
    }

    #[test]
    fn combine_only_trades_with_the_trend() {
        let green = out(Some(TrendState::Green), None, true);
        let red = out(Some(TrendState::Red), None, true);
        let buy = out(None, Some(Signal::Buy), true);
        let sell = out(None, Some(Signal::Sell), true);
        assert_eq!(
            combine(green, buy, Some(TrendState::Green)),
            Some(Signal::Buy)
        );
        assert_eq!(
            combine(red, sell, Some(TrendState::Red)),
            Some(Signal::Sell)
        );
        assert_eq!(combine(green, sell, Some(TrendState::Green)), None);
        assert_eq!(combine(red, buy, Some(TrendState::Red)), None);
    }

    #[test]
    fn aggregator_rejects_zero_interval() {
        assert!(CandleAggregator::new(0).is_err());
    }
}
