import numpy as np
from collections import deque

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
        # a static O(1) gate for every subsequent tick (no per-tick sort/percentile
        # across the 22.2M-tick dataset). Statistically equivalent for the spike
        # filter over multi-day timelines.
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

    def compute_atr(self, period):
        if len(self.price_queue) < period + 1:
            return 1.0
        trs = []
        for i in range(len(self.price_queue) - period, len(self.price_queue)):
            price = self.price_queue[i]
            prev_close = self.price_queue[i - 1]
            trs.append(abs(price - prev_close))
        return sum(trs) / period

    def on_tick(self, tick):
        self.total_ticks += 1
        price = float(tick['px'])
        size = float(tick['qty'])
        # ev flag check for buy vs sell (BUY_EVENT = 536870912)
        side = 1 if (int(tick['ev']) & 536870912) != 0 else -1
        
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

            unrealized_pnl = (
                (price - self.entry_price) * self.active_position * 
                self.symbol_config['order_size'] * self.symbol_config['contract_size']
            )

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

            # Basis-point SL/TP established at entry time, mirroring the Rust
            # engine's passive post_only limit-order layout (no time-decay).
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

        # Compute-block p95 cache: recompute the 1000-tick window 95th percentile
        # EXACTLY ONCE every P95_UPDATE_INTERVAL ticks and cache it. All ticks in
        # between read the static cached_p95_volume as an O(1) gate -- this removes
        # the per-tick np.percentile (sort) that would otherwise run 22.2M times.
        self._p95_counter += 1
        if self._p95_counter >= self.P95_UPDATE_INTERVAL or self.cached_p95_volume == float('inf'):
            if len(self.volume_buffer) >= 10:
                arr = np.fromiter(self.volume_buffer, dtype=np.float64,
                                  count=len(self.volume_buffer))
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
        # We fade massive aggressive blocks that FAIL to move price: a sign that
        # market orders are slamming into a hidden institutional Iceberg that
        # absorbs the flow (low Price_Impact). We coat the tail of the iceberg
        # with a PASSIVE MAKER limit and fade the aggressive side.
        current_len = len(self.price_queue)
        if current_len > lookback and self.active_position == 0:
            past_index = current_len - 1 - lookback
            past_price = self.price_queue[past_index]
            past_cvd = self.cvd_queue[past_index]

            # Volume-Weighted Price Impact over the lookback window:
            #   Delta_Price = Current_Price - Past_Price
            #   Cumulative_Taker_Volume = sum of |size| over the lookback window
            #   Price_Impact = |Delta_Price| / Cumulative_Taker_Volume
            delta_price = price - past_price
            cum_taker_volume = self.rolling_volume_sum
            price_impact = (abs(delta_price) / cum_taker_volume) if cum_taker_volume > 0.0 else 0.0

            # O(1) 95th-percentile volume spike gate (cached compute-block value)
            volume_spike = size > self.cached_p95_volume

            can_absorb = (
                volume_spike and
                cum_taker_volume > self.symbol_config['min_cvd_notional_usd'] and
                price_impact < self.symbol_config['max_price_impact_threshold'] and
                (self.total_ticks - self.last_entry_tick) >= self.symbol_config['entry_cooldown_ticks']
            )

            if can_absorb:
                if self.current_cvd > past_cvd and delta_price <= 0.0:
                    # Aggressive BUYING but price hit a ceiling -> fade SHORT
                    self._open_position(price, -1)
                elif self.current_cvd < past_cvd and delta_price >= 0.0:
                    # Aggressive SELLING but price hit a floor -> fade LONG
                    self._open_position(price, 1)

    def _open_position(self, price, direction):
        """Open a Passive Limit (Maker) Iceberg-Absorption Fade position.

        The entry price is pinned to the current inside price (price) to
        simulate a resting limit fill; the fee leg is a maker rebate (see
        close_position) with zero initial slippage.
        """
        self.active_position = direction
        self.entry_price = price
        self.highest_price = price
        self.lowest_price = price
        self.ticks_elapsed = 0
        self.last_entry_tick = self.total_ticks
        self.total_trades += 1

        tp_mult = self.symbol_config['take_profit_bps'] / 10000.0
        sl_mult = self.symbol_config['stop_loss_bps'] / 10000.0
        if direction == 1:
            self.take_profit_price = price * (1.0 + tp_mult)
            self.stop_loss_price = price * (1.0 - sl_mult)
        else:
            self.take_profit_price = price * (1.0 - tp_mult)
            self.stop_loss_price = price * (1.0 + sl_mult)

        if self.verbose:
            kind = "LONG" if direction == 1 else "SHORT"
            print(f"*** {kind} MOMENTUM ENTRY triggered at price {price} | "
                  f"SL: {self.stop_loss_price:.4f}, TP: {self.take_profit_price:.4f} ***")

    def close_position(self, exit_price, exit_type):
        # Calculate raw gross PnL
        size_base = self.symbol_config['order_size'] * self.symbol_config['contract_size']
        trade_pnl = (
            (exit_price - self.entry_price) * self.active_position * size_base
        )
        
        maker_fee_rate = self.general_config['fees']['maker_fee_rate']
        taker_fee_rate = self.general_config['fees']['taker_fee_rate']
        slippage_bps = self.general_config['fees']['slippage_bps']
        
        # Passive Limit (Maker) Momentum-Ignition: the entry is a resting limit
        # order filled at the inside price, so it earns the maker rebate/lower
        # fee and carries ZERO initial slippage.
        entry_fee = self.entry_price * maker_fee_rate * size_base
        
        # Exit fee and slippage depends on exit reason
        if exit_type == "tp":
            # TP is passive limit order but acts as taker fill when price reaches it
            exit_fee = exit_price * taker_fee_rate * size_base
            trade_slippage = 0.0
        elif exit_type == "sl":
            # SL is Taker Limit Order (capped risk, taker fee, zero slippage)
            exit_fee = exit_price * taker_fee_rate * size_base
            trade_slippage = 0.0
        else: # "trailing" or "timeout"
            # Trailing stop or Timeout are Market orders (Taker fee + slippage)
            exit_fee = exit_price * taker_fee_rate * size_base
            trade_slippage = exit_price * (slippage_bps / 10000.0) * size_base
            
        trade_fees = entry_fee + exit_fee
        net_pnl = trade_pnl - trade_fees - trade_slippage
        
        # Accumulate Profit Factor components (gross, pre-fee)
        if trade_pnl > 0:
            self.gross_wins += trade_pnl
        else:
            self.gross_losses += -trade_pnl
        self.total_fees += trade_fees + trade_slippage
        
        self.closed_pnl += net_pnl
        self.active_position = 0
