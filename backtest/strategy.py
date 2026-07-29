import numpy as np
from collections import deque
import random

class CVDMomentumStrategy:
    """
    Institutional Iceberg-Absorption Fade strategy (Volume-Weighted Price Impact).

    We fade massive aggressive blocks that FAIL to move price: market orders are
    slamming into a hidden institutional Iceberg that absorbs the flow (low
    Volume-Weighted Price Impact). We coat the tail of the iceberg with a PASSIVE
    MAKER limit and fade the aggressive side:
        * aggressive BUYING but price hits a ceiling (delta_price <= 0) -> SHORT
        * aggressive SELLING but price hits a floor  (delta_price >= 0) -> LONG
    """

    def __init__(self, hbt, symbol_config, general_config, verbose=True):
        self.hbt = hbt
        self.symbol_config = symbol_config
        self.general_config = general_config
        # When False, per-tick entry/exit prints are suppressed. Required for the
        # WFO hot loop (millions of ticks x many Optuna trials) and for thread-safe
        # output under n_jobs parallelism.
        self.verbose = verbose
        
        # Mirroring Rust SymbolState
        self.price_queue = deque(maxlen=symbol_config['max_capacity'])
        self.cvd_queue = deque(maxlen=symbol_config['max_capacity'])
        self.size_queue = deque(maxlen=symbol_config['max_capacity'])
        self.size_queue_side = deque(maxlen=symbol_config['max_capacity'])
        self.current_cvd = 0.0
        self.total_ticks = 0
        self.last_entry_tick = 0
        
        # Rolling volume tracking to avoid O(N) list operations on every tick
        self.rolling_volume_sum = 0.0
        self.rolling_volume_sq_sum = 0.0
        self.rolling_buy_volume = 0.0
        self.rolling_sell_volume = 0.0
        
        # 95th percentile rolling volume buffer (sliding window of 1000 ticks)
        self.volume_buffer = deque(maxlen=1000)
        # Compute-block-cached 95th-percentile volume. Recomputed EXACTLY ONCE
        # every P95_UPDATE_INTERVAL ticks over the 1000-tick buffer, then held as
        # a static O(1) gate for every subsequent tick.
        self.cached_p95_volume = float('inf')
        self._p95_counter = 0
        self.P95_UPDATE_INTERVAL = 500
        
        # Position & Performance tracking
        self.active_position = 0  # 1 = Long, -1 = Short, 0 = Flat
        self.entry_price = 0.0
        self.highest_price = 0.0
        self.lowest_price = 0.0
        self.ticks_elapsed = 0
        self.total_trades = 0
        self.closed_pnl = 0.0
        # Fee-adjusted Profit Factor components (gross, pre-fee)
        self.gross_wins = 0.0
        self.gross_losses = 0.0
        self.total_fees = 0.0
        self.order_id = 1
        self.stop_loss_price = 0.0
        self.take_profit_price = 0.0
        self.trade_records = []  # list of tuples: (gross_win, gross_loss, fee_and_slippage)

        # --- Maker Fill Realism (Fix #1) ---
        # Config-driven fill probability for passive maker entries.
        # Literature: passive fades at trigger tick fill ~40-70% (e.g., Cont et al. 2013,
        # Eisler et al. 2012 on limit order fill rates). Default 0.55 as mid-range.
        self.fill_probability = symbol_config.get('fill_probability', 0.55)
        
        # Maker timeout: 5 seconds in Rust engine. Convert to tick count using
        # config-driven ticks_per_second estimate (default 500 ticks/s ~ 5s = 2500 ticks).
        ticks_per_second = symbol_config.get('ticks_per_second', 500)
        maker_timeout_seconds = symbol_config.get('maker_timeout_seconds', 5.0)
        self.maker_timeout_ticks = int(ticks_per_second * maker_timeout_seconds)
        
        # Track pending maker orders awaiting fill
        self.pending_maker_order = None  # dict with keys: price, direction, entry_tick, order_id

    def compute_atr(self, period):
        if len(self.price_queue) < period + 1:
            return 1.0
        trs = []
        for i in range(len(self.price_queue) - period, len(self.price_queue)):
            price = self.price_queue[i]
            prev_close = self.price_queue[i - 1]
            trs.append(abs(price - prev_close))
        return sum(trs) / period

    def _check_maker_fill(self, tick_price, tick_size, tick_side):
        """
        Check if a pending maker order would fill at this tick.
        Fill condition: opposing volume passes through our limit price.
        For SHORT (maker sell limit at ask): need aggressive BUY volume >= our size at our price.
        For LONG (maker buy limit at bid): need aggressive SELL volume >= our size at our price.
        
        We use tick volume-at-price as proxy for queue depth. Fill probability model:
        - If market trades through our price (price crossed): fill guaranteed (100%).
        - If price matches our limit price & tick is opposing side:
            - If opposing volume >= order size: fill_prob = fill_probability
            - Else: fill_prob = fill_probability * (opposing_volume / order_size) * 0.5
        """
        if self.pending_maker_order is None:
            return False
            
        order = self.pending_maker_order
        order_price = order['price']
        direction = order['direction']  # 1 = LONG (buy limit), -1 = SHORT (sell limit)
        order_size = self.symbol_config['order_size']
        
        # 5-second maker timeout check
        if (self.total_ticks - order['entry_tick']) >= self.maker_timeout_ticks:
            if self.verbose:
                kind = "LONG" if direction == 1 else "SHORT"
                print(f"*** {kind} MAKER TIMEOUT at tick {self.total_ticks} (price {order_price}) ***")
            self.pending_maker_order = None
            return False
        
        # Passive limit logic:
        # - LONG (buy limit): filled by aggressive SELLs (tick_side == -1) at or below limit price
        # - SHORT (sell limit): filled by aggressive BUYs (tick_side == 1) at or above limit price
        if direction == 1:
            price_crossed = (tick_price < order_price)
            price_match = (tick_price == order_price)
            side_match = (tick_side == -1)
        else:
            price_crossed = (tick_price > order_price)
            price_match = (tick_price == order_price)
            side_match = (tick_side == 1)

        filled = False
        fill_prob = 0.0

        if price_crossed:
            filled = True
            fill_prob = 1.0
        elif price_match and side_match:
            tick_volume = float(tick_size)
            fill_prob = self.fill_probability
            if tick_volume < order_size and order_size > 0:
                fill_prob *= 0.5 * (tick_volume / order_size)
            if random.random() < fill_prob:
                filled = True

        if filled:
            if self.verbose:
                kind = "LONG" if direction == 1 else "SHORT"
                print(f"*** {kind} MAKER FILL at tick {self.total_ticks} price {tick_price} (prob={fill_prob:.2f}) ***")
            self.pending_maker_order = None
            return True

        return False

    def on_tick(self, tick):
        self.total_ticks += 1
        price = float(tick['px'])
        size = float(tick['qty'])
        # ev flag check for buy vs sell (BUY_EVENT = 536870912)
        side = 1 if (int(tick['ev']) & 536870912) != 0 else -1
        
        # --- Maker Fill Check (Fix #1) ---
        # Check pending maker order BEFORE processing new signal
        if self.pending_maker_order is not None and self.active_position == 0:
            order_copy = self.pending_maker_order.copy()
            if self._check_maker_fill(price, size, side):
                # Fill occurred - convert pending order to active position
                self.active_position = order_copy['direction']
                self.entry_price = order_copy['price']
                self.highest_price = order_copy['price']
                self.lowest_price = order_copy['price']
                self.ticks_elapsed = 0
                self.last_entry_tick = self.total_ticks
                self.total_trades += 1

                tp_mult = self.symbol_config['take_profit_bps'] / 10000.0
                sl_mult = self.symbol_config['stop_loss_bps'] / 10000.0
                if self.active_position == 1:
                    self.take_profit_price = self.entry_price * (1.0 + tp_mult)
                    self.stop_loss_price = self.entry_price * (1.0 - sl_mult)
                else:
                    self.take_profit_price = self.entry_price * (1.0 - tp_mult)
                    self.stop_loss_price = self.entry_price * (1.0 + sl_mult)

                if self.verbose:
                    kind = "LONG" if self.active_position == 1 else "SHORT"
                    print(f"*** {kind} MAKER FILL ENTRY at price {self.entry_price} | "
                          f"SL: {self.stop_loss_price:.4f}, TP: {self.take_profit_price:.4f} ***")
        
        # 1. Update CVD
        if side == 1:
            self.current_cvd += size
        else:
            self.current_cvd -= size

        atr = max(self.compute_atr(self.symbol_config['atr_period']), self.symbol_config['tick_size'])

        # 2. Check position exits
        if self.active_position != 0:
            self.ticks_elapsed += 1
            self.highest_price = max(self.highest_price, price)
            self.lowest_price = min(self.lowest_price, price)

            trailing_stop_distance = max(
                self.symbol_config['trailing_stop_atr_mult'] * atr, 
                self.symbol_config['min_trailing_stop_distance']
            )
            
            trailing_stop_activated = (
                (self.highest_price - self.entry_price > trailing_stop_distance) if self.active_position == 1
                else (self.entry_price - self.lowest_price > trailing_stop_distance)
            )
            
            trailing_stop_price = (
                (self.highest_price - trailing_stop_distance) if self.active_position == 1
                else (self.lowest_price + trailing_stop_distance)
            )

            exit_type = None

            # Basis-point SL/TP established at entry time
            hold_ticks = self.symbol_config['hold_ticks']
            if self.active_position == 1:
                hit_sl = price <= self.stop_loss_price
                hit_tp = price >= self.take_profit_price
            else:
                hit_sl = price >= self.stop_loss_price
                hit_tp = price <= self.take_profit_price

            if hit_sl:
                exit_type = "sl"
            elif hit_tp:
                exit_type = "tp"
            elif trailing_stop_activated and (
                (self.active_position == 1 and price <= trailing_stop_price) or
                (self.active_position == -1 and price >= trailing_stop_price)
            ):
                exit_type = "trailing"
            elif self.ticks_elapsed >= hold_ticks:
                exit_type = "timeout"

            if exit_type is not None:
                self.close_position(price, exit_type)

        # 3. Update rolling volume sum (subtract outgoing, add incoming)
        lookback = self.symbol_config['lookback_ticks']
        outgoing_size = 0.0
        outgoing_size_sq = 0.0
        if len(self.size_queue) >= lookback:
            outgoing_size = self.size_queue[-lookback]
            outgoing_size_sq = outgoing_size * outgoing_size
            outgoing_side = self.size_queue_side[-lookback]
            if outgoing_side > 0:
                self.rolling_buy_volume -= outgoing_size
            else:
                self.rolling_sell_volume -= outgoing_size
            
        self.rolling_volume_sum += size - outgoing_size
        self.rolling_volume_sq_sum += size * size - outgoing_size_sq
        if side > 0:
            self.rolling_buy_volume += size
        else:
            self.rolling_sell_volume += size
        
        # Track volume in buffer for 95th percentile filter
        self.volume_buffer.append(size)

        # Compute-block p95 cache
        self._p95_counter += 1
        if self._p95_counter >= self.P95_UPDATE_INTERVAL or self.cached_p95_volume == float('inf'):
            if len(self.volume_buffer) >= 10:
                arr = np.fromiter(self.volume_buffer, dtype=np.float64, count=len(self.volume_buffer))
                self.cached_p95_volume = float(np.percentile(arr, 95))
                self._p95_counter = 0
            else:
                self.cached_p95_volume = float('inf')

        # 4. Add current values to rolling queues
        self.price_queue.append(price)
        self.cvd_queue.append(self.current_cvd)
        self.size_queue.append(size)
        self.size_queue_side.append(side)

        # 5. Institutional Iceberg-Absorption Fade (Volume-Weighted Price Impact)
        current_len = len(self.price_queue)
        if current_len > lookback and self.active_position == 0 and self.pending_maker_order is None:
            past_index = current_len - 1 - lookback
            past_price = self.price_queue[past_index]
            past_cvd = self.cvd_queue[past_index]

            delta_price = price - past_price
            cum_taker_volume = self.rolling_volume_sum
            price_impact = (abs(delta_price) / cum_taker_volume) if cum_taker_volume > 0.0 else 0.0

            volume_spike = size > self.cached_p95_volume

            can_absorb = (
                volume_spike and
                cum_taker_volume > self.symbol_config['min_cvd_notional_usd'] and
                price_impact < self.symbol_config['max_price_impact_threshold'] and
                (self.total_ticks - self.last_entry_tick) >= self.symbol_config['entry_cooldown_ticks']
            )

            if can_absorb:
                if self.current_cvd > past_cvd and delta_price <= 0.0:
                    # Aggressive BUYING but price hit a ceiling -> fade SHORT (maker sell limit)
                    self._open_position(price, -1)
                elif self.current_cvd < past_cvd and delta_price >= 0.0:
                    # Aggressive SELLING but price hit a floor -> fade LONG (maker buy limit)
                    self._open_position(price, 1)

    def _open_position(self, price, direction):
        """
        Open a Passive Limit (Maker) Iceberg-Absorption Fade position.
        
        Instead of immediate fill, we place a passive maker limit order and track
        it as pending. The fill is checked on subsequent ticks via _check_maker_fill.
        """
        self.order_id += 1
        self.pending_maker_order = {
            'price': price,
            'direction': direction,
            'entry_tick': self.total_ticks,
            'order_id': self.order_id
        }
        
        if self.verbose:
            kind = "LONG" if direction == 1 else "SHORT"
            print(f"*** {kind} MAKER LIMIT PLACED at price {price} (tick {self.total_ticks}) "
                  f"| timeout={self.maker_timeout_ticks} ticks ***")

    def close_position(self, exit_price, exit_type):
        # Calculate raw gross PnL
        size_base = self.symbol_config['order_size'] * self.symbol_config['contract_size']
        trade_pnl = (
            (exit_price - self.entry_price) * self.active_position * size_base
        )
        
        maker_fee_rate = self.general_config['fees']['maker_fee_rate']
        taker_fee_rate = self.general_config['fees']['taker_fee_rate']
        slippage_bps = self.general_config['fees']['slippage_bps']
        
        # Passive Limit (Maker) entry: resting limit order (maker fee, zero initial slippage)
        entry_fee = self.entry_price * maker_fee_rate * size_base
        
        # Exit fee and slippage depend on exit reason
        if exit_type == "tp":
            # TP is passive limit order but acts as taker fill when price reaches it
            exit_fee = exit_price * taker_fee_rate * size_base
            trade_slippage = 0.0
        elif exit_type == "sl":
            # Fix #2: SL fires as Taker Order during fast move -> carries slippage
            exit_fee = exit_price * taker_fee_rate * size_base
            trade_slippage = exit_price * (slippage_bps / 10000.0) * size_base
        else:  # "trailing" or "timeout"
            # Trailing stop or Timeout are Market orders (Taker fee + slippage)
            exit_fee = exit_price * taker_fee_rate * size_base
            trade_slippage = exit_price * (slippage_bps / 10000.0) * size_base
            
        trade_fees = entry_fee + exit_fee
        net_pnl = trade_pnl - trade_fees - trade_slippage
        
        # Accumulate Profit Factor components (gross, pre-fee)
        gross_win = max(trade_pnl, 0.0)
        gross_loss = max(-trade_pnl, 0.0)
        total_fee_and_slip = trade_fees + trade_slippage

        self.gross_wins += gross_win
        self.gross_losses += gross_loss
        self.total_fees += total_fee_and_slip
        
        self.trade_records.append((gross_win, gross_loss, total_fee_and_slip))

        self.closed_pnl += net_pnl
        self.active_position = 0
        self.pending_maker_order = None


class MACDMomentumStrategy:
    """
    5-minute Trend-Following MACD Momentum Strategy.

    Architecture:
    1. Entry Logic:
       - Normalize MACD as a percentage of price: MACD_% = ((EMA_fast - EMA_slow) / Price) * 100
       - Trend Filter: 200 EMA on 15m (or 1h) timeframe establishing directional bias.
       - Go Long: MACD_% crosses ABOVE +X% threshold AND Price > 200 EMA.
       - Go Short: MACD_% crosses BELOW -X% threshold AND Price < 200 EMA.
    2. Risk & Execution Constraints (Strict Vaelen Standards):
       - Include hard Stop Losses (1.5x ATR) and target Risk-Reward ratio (e.g. 1:1.5+) on entry.
       - Parameterize all thresholds in config.toml (macd_fast, macd_slow, ema_filter, norm_threshold_pct, sl_atr_mult, risk_reward_ratio).
    """

    def __init__(self, hbt, symbol_config, general_config, verbose=True):
        self.hbt = hbt
        self.symbol_config = symbol_config
        self.general_config = general_config
        self.verbose = verbose

        # Strategy Parameters
        macd_cfg = general_config.get('macd_momentum', {})
        self.macd_fast = symbol_config.get('macd_fast', macd_cfg.get('macd_fast', 12))
        self.macd_slow = symbol_config.get('macd_slow', macd_cfg.get('macd_slow', 26))
        self.macd_signal = symbol_config.get('macd_signal', macd_cfg.get('macd_signal', 9))
        self.ema_filter_period = symbol_config.get('ema_filter', macd_cfg.get('ema_filter', 200))
        self.norm_threshold_pct = symbol_config.get('norm_threshold_pct', macd_cfg.get('norm_threshold_pct', 0.15))
        self.sl_atr_mult = symbol_config.get('sl_atr_mult', macd_cfg.get('sl_atr_mult', 1.5))
        self.risk_reward_ratio = symbol_config.get('risk_reward_ratio', macd_cfg.get('risk_reward_ratio', 1.5))
        self.atr_period = symbol_config.get('atr_period', macd_cfg.get('atr_period', 14))
        self.candle_interval_sec = symbol_config.get('candle_interval_mins', macd_cfg.get('candle_interval_mins', 5)) * 60
        self.trend_filter_interval_sec = symbol_config.get('trend_filter_interval_mins', macd_cfg.get('trend_filter_interval_mins', 15)) * 60
        self.entry_cooldown_ticks = symbol_config.get('entry_cooldown_ticks', macd_cfg.get('entry_cooldown_ticks', 100))

        # Execution parameters
        self.order_type = symbol_config.get('order_type', macd_cfg.get('order_type', 'limit'))
        self.max_hold_mins = symbol_config.get('max_hold_mins', macd_cfg.get('max_hold_mins', 15))
        self.scalper_offer_enabled = symbol_config.get('scalper_offer_enabled', macd_cfg.get('scalper_offer_enabled', True))
        self.max_hold_sec = self.max_hold_mins * 60.0

        # Alpha smoothing constants
        self.alpha_fast = 2.0 / (self.macd_fast + 1.0)
        self.alpha_slow = 2.0 / (self.macd_slow + 1.0)
        self.alpha_signal = 2.0 / (self.macd_signal + 1.0)
        self.alpha_200 = 2.0 / (self.ema_filter_period + 1.0)

        # Indicator state
        self.current_5m_start_ts = 0
        self.current_15m_start_ts = 0
        self.ema_fast_val = None
        self.ema_slow_val = None
        self.ema_signal_val = None
        self.ema_200_trend_val = None
        self.prev_macd_pct = 0.0
        self.curr_macd_pct = 0.0

        # Price history queue
        self.price_queue = deque(maxlen=symbol_config.get('max_capacity', 1000))
        self.total_ticks = 0
        self.last_entry_tick = 0

        # Position & Performance tracking
        self.active_position = 0  # 1 = Long, -1 = Short, 0 = Flat
        self.entry_price = 0.0
        self.entry_ts = 0.0
        self.highest_price = 0.0
        self.lowest_price = 0.0
        self.ticks_elapsed = 0
        self.total_trades = 0
        self.closed_pnl = 0.0
        self.gross_wins = 0.0
        self.gross_losses = 0.0
        self.total_fees = 0.0
        self.order_id = 1
        self.stop_loss_price = 0.0
        self.take_profit_price = 0.0
        self.trade_records = []

    def compute_atr(self, period):
        if len(self.price_queue) < period + 1:
            return self.symbol_config.get('tick_size', 0.0001)
        trs = []
        prices = list(self.price_queue)
        for i in range(1, len(prices)):
            high = max(prices[i-1], prices[i])
            low = min(prices[i-1], prices[i])
            tr = high - low
            trs.append(tr)
        if not trs:
            return self.symbol_config.get('tick_size', 0.0001)
        return float(np.mean(trs[-period:]))

    def on_tick(self, tick):
        self.total_ticks += 1
        price = float(tick['px'])
        self.price_queue.append(price)

        if hasattr(tick, 'dtype') and tick.dtype.names is not None:
            names = tick.dtype.names
            ts_raw = float(tick['local_ts']) if 'local_ts' in names else (float(tick['exch_ts']) if 'exch_ts' in names else (float(tick['ts']) if 'ts' in names else 0.0))
        else:
            ts_raw = 0.0

        if ts_raw > 1e16:
            ts_sec = ts_raw / 1_000_000_000.0
        elif ts_raw > 1e11:
            ts_sec = ts_raw / 1_000.0
        else:
            ts_sec = ts_raw

        atr = self.compute_atr(self.atr_period)

        # 1. Update 15m 200 EMA and 5m MACD EMAs
        bucket_15m = int(ts_sec // self.trend_filter_interval_sec) * self.trend_filter_interval_sec
        if bucket_15m > self.current_15m_start_ts or self.ema_200_trend_val is None:
            self.current_15m_start_ts = bucket_15m
            self.ema_200_trend_val = price * self.alpha_200 + (self.ema_200_trend_val or price) * (1.0 - self.alpha_200)

        bucket_5m = int(ts_sec // self.candle_interval_sec) * self.candle_interval_sec
        if bucket_5m > self.current_5m_start_ts or self.ema_fast_val is None:
            self.current_5m_start_ts = bucket_5m
            self.ema_fast_val = price * self.alpha_fast + (self.ema_fast_val or price) * (1.0 - self.alpha_fast)
            self.ema_slow_val = price * self.alpha_slow + (self.ema_slow_val or price) * (1.0 - self.alpha_slow)
        else:
            self.ema_fast_val = price * self.alpha_fast + self.ema_fast_val * (1.0 - self.alpha_fast)
            self.ema_slow_val = price * self.alpha_slow + self.ema_slow_val * (1.0 - self.alpha_slow)

        macd_line = self.ema_fast_val - self.ema_slow_val
        self.curr_macd_pct = (macd_line / price) * 100.0

        if self.ema_signal_val is None:
            self.ema_signal_val = macd_line
        else:
            self.ema_signal_val = macd_line * self.alpha_signal + self.ema_signal_val * (1.0 - self.alpha_signal)

        # 2. Check position exits (Hard SL, TP, and Max 15-min Scalper Offer Hold Time)
        if self.active_position != 0:
            self.ticks_elapsed += 1
            self.highest_price = max(self.highest_price, price)
            self.lowest_price = min(self.lowest_price, price)

            hold_duration_sec = ts_sec - self.entry_ts if self.entry_ts > 0 else (self.ticks_elapsed * 0.1)

            exit_type = None
            if self.active_position == 1:
                if price <= self.stop_loss_price:
                    exit_type = "sl"
                elif price >= self.take_profit_price:
                    exit_type = "tp"
            else:
                if price >= self.stop_loss_price:
                    exit_type = "sl"
                elif price <= self.take_profit_price:
                    exit_type = "tp"

            if exit_type is None and hold_duration_sec >= self.max_hold_sec:
                exit_type = "max_hold_timeout"

            if exit_type is not None:
                self.close_position(price, exit_type, hold_duration_sec)

        # 3. Check Entry Signals (Go Long / Go Short)
        cooldown_elapsed = (self.total_ticks - self.last_entry_tick) >= self.entry_cooldown_ticks
        if self.active_position == 0 and cooldown_elapsed:
            ema_200 = self.ema_200_trend_val if self.ema_200_trend_val is not None else price

            long_signal = (self.prev_macd_pct <= self.norm_threshold_pct and
                           self.curr_macd_pct > self.norm_threshold_pct and
                           price > ema_200)

            short_signal = (self.prev_macd_pct >= -self.norm_threshold_pct and
                            self.curr_macd_pct < -self.norm_threshold_pct and
                            price < ema_200)

            if long_signal:
                self._open_position(price, 1, atr, ts_sec)
            elif short_signal:
                self._open_position(price, -1, atr, ts_sec)

        self.prev_macd_pct = self.curr_macd_pct

    def _open_position(self, price, direction, atr, ts_sec=0.0):
        self.order_id += 1
        self.active_position = direction
        self.entry_price = price
        self.entry_ts = ts_sec
        self.highest_price = price
        self.lowest_price = price
        self.ticks_elapsed = 0
        self.last_entry_tick = self.total_ticks
        self.total_trades += 1

        sl_dist = self.sl_atr_mult * atr
        tp_dist = sl_dist * self.risk_reward_ratio

        if direction == 1:
            self.stop_loss_price = price - sl_dist
            self.take_profit_price = price + tp_dist
        else:
            self.stop_loss_price = price + sl_dist
            self.take_profit_price = price - tp_dist

        if self.verbose:
            kind = "LONG" if direction == 1 else "SHORT"
            ord_kind = self.order_type.upper()
            print(f"*** MACD {kind} [{ord_kind} LIMIT ENTRY] @ {price:.5f} (MACD_%: {self.curr_macd_pct:.4f}%) | "
                  f"SL: {self.stop_loss_price:.5f}, TP: {self.take_profit_price:.5f} ***")

    def close_position(self, exit_price, exit_type, hold_duration_sec=0.0):
        size_base = self.symbol_config['order_size'] * self.symbol_config['contract_size']
        trade_pnl = (exit_price - self.entry_price) * self.active_position * size_base

        # Maker fee schedule for Limit entry: 0.02% (2.0 bps) instead of 0.05% taker
        maker_fee_rate = self.general_config['fees'].get('maker_fee_rate', 0.0002)
        taker_fee_rate = self.general_config['fees'].get('taker_fee_rate', 0.0005)
        slippage_bps = self.general_config['fees']['slippage_bps']

        # Entry fee: Maker Fee for Limit Orders
        entry_fee_rate = maker_fee_rate if self.order_type == "limit" else taker_fee_rate
        entry_fee = self.entry_price * entry_fee_rate * size_base

        # Delta Exchange Scalper Offer 0% Closing Fee Check (hold time <= max_hold_sec)
        if self.scalper_offer_enabled and hold_duration_sec <= self.max_hold_sec:
            exit_fee_rate = 0.0  # 0% Closing Fee under Scalper Offer!
        else:
            exit_fee_rate = maker_fee_rate if exit_type in ["tp", "limit"] else taker_fee_rate

        exit_fee = exit_price * exit_fee_rate * size_base

        # Slippage is 0 on Limit entries and Limit/TP exits; non-zero only on aggressive market SL/Timeout exits
        trade_slippage = exit_price * (slippage_bps / 10000.0) * size_base if exit_type not in ["tp", "limit"] else 0.0

        trade_fees = entry_fee + exit_fee
        net_pnl = trade_pnl - trade_fees - trade_slippage

        gross_win = max(trade_pnl, 0.0)
        gross_loss = max(-trade_pnl, 0.0)
        total_fee_and_slip = trade_fees + trade_slippage

        self.gross_wins += gross_win
        self.gross_losses += gross_loss
        self.total_fees += total_fee_and_slip
        self.trade_records.append((gross_win, gross_loss, total_fee_and_slip))
        self.closed_pnl += net_pnl

        if self.verbose:
            print(f"*** MACD POSITION CLOSED [{exit_type.upper()}] @ {exit_price:.5f} | Hold: {hold_duration_sec:.1f}s | "
                  f"Scalper 0% Fee: {hold_duration_sec <= self.max_hold_sec} | Net PnL: ${net_pnl:.4f} ***")

        self.active_position = 0
