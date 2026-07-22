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
        self.verbose = verbose
        
        self.price_queue = deque(maxlen=symbol_config['max_capacity'])
        self.cvd_queue = deque(maxlen=symbol_config['max_capacity'])
        self.size_queue = deque(maxlen=symbol_config['max_capacity'])
        self.size_queue_side = deque(maxlen=symbol_config['max_capacity'])
        self.current_cvd = 0.0
        self.total_ticks = 0
        self.last_entry_tick = 0
        
        self.rolling_volume_sum = 0.0
        self.rolling_volume_sq_sum = 0.0
        self.rolling_buy_volume = 0.0
        self.rolling_sell_volume = 0.0
        
        self.volume_buffer = deque(maxlen=1000)
        self.cached_p95_volume = float('inf')
        self._p95_counter = 0
        self.P95_UPDATE_INTERVAL = 500
        
        self.active_position = 0
        self.entry_price = 0.0
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

        self.fill_probability = symbol_config.get('fill_probability', 0.55)
        ticks_per_second = symbol_config.get('ticks_per_second', 500)
        maker_timeout_seconds = symbol_config.get('maker_timeout_seconds', 5.0)
        self.maker_timeout_ticks = int(ticks_per_second * maker_timeout_seconds)
        self.pending_maker_order = None

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
        if self.pending_maker_order is None:
            return False
            
        order = self.pending_maker_order
        order_price = order['price']
        direction = order['direction']
        order_size = self.symbol_config['order_size']
        
        if (self.total_ticks - order['entry_tick']) >= self.maker_timeout_ticks:
            if self.verbose:
                kind = "LONG" if direction == 1 else "SHORT"
                print(f"*** {kind} MAKER TIMEOUT at tick {self.total_ticks} (price {order_price}) ***")
            self.pending_maker_order = None
            return False
        
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
        side = 1 if (int(tick['ev']) & 536870912) != 0 else -1
        
        if self.pending_maker_order is not None and self.active_position == 0:
            order_copy = self.pending_maker_order.copy()
            if self._check_maker_fill(price, size, side):
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
        
        if side == 1:
            self.current_cvd += size
        else:
            self.current_cvd -= size

        atr = max(self.compute_atr(self.symbol_config['atr_period']), self.symbol_config['tick_size'])

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
        
        self.volume_buffer.append(size)

        self._p95_counter += 1
        if self._p95_counter >= self.P95_UPDATE_INTERVAL or self.cached_p95_volume == float('inf'):
            if len(self.volume_buffer) >= 10:
                arr = np.fromiter(self.volume_buffer, dtype=np.float64, count=len(self.volume_buffer))
                self.cached_p95_volume = float(np.percentile(arr, 95))
                self._p95_counter = 0
            else:
                self.cached_p95_volume = float('inf')

        self.price_queue.append(price)
        self.cvd_queue.append(self.current_cvd)
        self.size_queue.append(size)
        self.size_queue_side.append(side)

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
                    self._open_position(price, -1)
                elif self.current_cvd < past_cvd and delta_price >= 0.0:
                    self._open_position(price, 1)

    def _open_position(self, price, direction):
        self.order_id += 1
        self.pending_maker_order = {
            'price': price,
            'direction': direction,
            'entry_tick': self.total_ticks,
            'order_id': self.order_id
        }
        if self.verbose:
            kind = "LONG" if direction == 1 else "SHORT"
            print(f"*** {kind} MAKER LIMIT PLACED at price {price} (tick {self.total_ticks}) | timeout={self.maker_timeout_ticks} ticks ***")

    def close_position(self, exit_price, exit_type):
        size_base = self.symbol_config['order_size'] * self.symbol_config['contract_size']
        trade_pnl = (exit_price - self.entry_price) * self.active_position * size_base
        
        maker_fee_rate = self.general_config['fees']['maker_fee_rate']
        taker_fee_rate = self.general_config['fees']['taker_fee_rate']
        slippage_bps = self.general_config['fees']['slippage_bps']
        
        entry_fee = self.entry_price * maker_fee_rate * size_base
        
        if exit_type == "tp":
            exit_fee = exit_price * taker_fee_rate * size_base
            trade_slippage = 0.0
        elif exit_type == "sl":
            exit_fee = exit_price * taker_fee_rate * size_base
            trade_slippage = exit_price * (slippage_bps / 10000.0) * size_base
        else:
            exit_fee = exit_price * taker_fee_rate * size_base
            trade_slippage = exit_price * (slippage_bps / 10000.0) * size_base
            
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
        self.active_position = 0
        self.pending_maker_order = None
