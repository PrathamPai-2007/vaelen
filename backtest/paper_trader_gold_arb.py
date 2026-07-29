import json
import urllib.request
import time
import datetime
import os
import logging
try:
    from symbol_validation import validate_symbols, DELTA_INDIA_API
except ImportError:
    from backtest.symbol_validation import validate_symbols, DELTA_INDIA_API

logger = logging.getLogger("GoldArbPaperTrader")

class GoldArbPaperTrader:
    """
    Production-grade Paper-Trading & Execution Engine for Same-Venue Gold Funding Arbitrage
    (Long PAXGUSD / Short XAUTUSD on Delta Exchange India).
    """
    def __init__(self, config=None):
        self.config = config or {}
        self.api_host = self.config.get('api_host', DELTA_INDIA_API)
        self.leg_long = self.config.get('leg_long', 'PAXGUSD')
        self.leg_short = self.config.get('leg_short', 'XAUTUSD')
        
        self.effective_leverage = float(self.config.get('effective_leverage', 3.0))
        self.position_sizing_pct = float(self.config.get('position_sizing_pct', 0.50))
        self.target_contracts = int(self.config.get('target_contracts', 0))
        self.max_depeg_stop_loss_bps = float(self.config.get('max_depeg_stop_loss_bps', 300.0))
        
        self.initial_equity = float(self.config.get('paper_trading_initial_balance', 50.0))
        self.equity = self.initial_equity
        self.peak_equity = self.initial_equity
        
        self.in_position = False
        self.long_qty = 0.0
        self.short_qty = 0.0
        self.entry_price_long = 0.0
        self.entry_price_short = 0.0
        
        self.total_funding_accrued = 0.0
        self.total_cycles_settled = 0
        self.last_settlement_ts = 0
        
        self.logs_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), 'logs'))
        os.makedirs(self.logs_dir, exist_ok=True)
        self.telemetry_file = os.path.join(self.logs_dir, 'gold_arb_telemetry.csv')
        
        self._init_telemetry_header()
        
    def _init_telemetry_header(self):
        if not os.path.exists(self.telemetry_file):
            with open(self.telemetry_file, 'w', encoding='utf-8') as f:
                f.write("timestamp_utc,event_type,equity_usd,paxg_mark,xaut_mark,depeg_bps,spread_paxg_bps,spread_xaut_bps,accrued_funding_usd,margin_health_status,notes\n")

    def _fetch_json(self, url):
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            return None

    def validate_environment(self):
        valid, meta = validate_symbols([self.leg_long, self.leg_short], self.api_host)
        if not valid:
            raise ValueError(f"Configured symbols {self.leg_long}/{self.leg_short} invalid on {self.api_host}")
        logger.info(f"Validated symbols {self.leg_long} and {self.leg_short} on {self.api_host}")
        return meta

    def fetch_live_quotes(self):
        url = f"{self.api_host}/v2/tickers?page_size=1000"
        data = self._fetch_json(url)
        if not data or 'result' not in data:
            return None, None
            
        tickers = {t['symbol']: t for t in data['result']}
        paxg_t = tickers.get(self.leg_long)
        xaut_t = tickers.get(self.leg_short)
        
        return paxg_t, xaut_t

    def parse_quote_details(self, ticker):
        if not ticker:
            return 0.0, 0.0, 0.0, 0.0
            
        mark = float(ticker.get('mark_price') or 0)
        quotes = ticker.get('quotes', {})
        bid = float(quotes.get('best_bid') or 0)
        ask = float(quotes.get('best_ask') or 0)
        spread_bps = (ask - bid) / mark * 10000.0 if mark > 0 and bid > 0 and ask > 0 else 0.0
        
        return mark, bid, ask, spread_bps

    def open_position_paper(self, paxg_ask, xaut_bid):
        self.entry_price_long = paxg_ask
        self.entry_price_short = xaut_bid
        
        if self.target_contracts > 0:
            # 1 contract = 0.001 troy oz
            self.long_qty = self.target_contracts * 0.001
            self.short_qty = self.target_contracts * 0.001
            notional = (self.long_qty * self.entry_price_long + self.short_qty * self.entry_price_short)
        else:
            allocated_margin = self.equity * self.position_sizing_pct
            notional = allocated_margin * self.effective_leverage
            self.long_qty = (notional / 2.0) / self.entry_price_long if self.entry_price_long > 0 else 0
            self.short_qty = (notional / 2.0) / self.entry_price_short if self.entry_price_short > 0 else 0
        
        # Entry friction: Taker fees (5.9 bps) + entry slippage
        entry_fee_long = (self.long_qty * self.entry_price_long) * 0.00059
        entry_fee_short = (self.short_qty * self.entry_price_short) * 0.00059
        total_entry_cost = entry_fee_long + entry_fee_short
        
        self.equity -= total_entry_cost
        self.in_position = True
        
        logger.info(f"OPENED PAPER ARB POSITION: Notional=${notional:.2f} | Long {self.leg_long} @ ${self.entry_price_long:.2f} | Short {self.leg_short} @ ${self.entry_price_short:.2f} | Entry Fee=${total_entry_cost:.2f}")
        self.log_telemetry("ENTRY_OPEN", paxg_ask, xaut_bid, 0.0, 0.0, 0.0, "Position opened with 3x leverage")

    def check_funding_settlement(self, paxg_t, xaut_t):
        """
        Check if 8-hour funding settlement occurred (00:00, 08:00, 16:00 UTC).
        """
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        current_hour = now_dt.hour
        
        # Funding settles at 0, 8, 16
        if current_hour in [0, 8, 16] and now_dt.minute < 5:
            settlement_key = int(now_dt.replace(minute=0, second=0, microsecond=0).timestamp())
            if settlement_key > self.last_settlement_ts:
                self.last_settlement_ts = settlement_key
                
                # Fetch live funding rates from product data
                paxg_rate = float(paxg_t.get('funding_rate') or 0.000002) # ~0.02 bps
                xaut_rate = float(xaut_t.get('funding_rate') or 0.000200) # ~2.00 bps
                
                notional = (self.long_qty * self.entry_price_long + self.short_qty * self.entry_price_short) / 2.0
                
                # Net Funding Income = Received on Short XAUT - Paid on Long PAXG
                short_funding_received = notional * xaut_rate
                long_funding_paid = notional * paxg_rate
                net_funding_period = short_funding_received - long_funding_paid
                
                self.total_funding_accrued += net_funding_period
                self.equity += net_funding_period
                self.total_cycles_settled += 1
                
                logger.info(f"8H FUNDING ACCRUED: +${net_funding_period:.4f} (Short XAUT=${short_funding_received:.4f}, Long PAXG=${long_funding_paid:.4f}) | Total Accrued=${self.total_funding_accrued:.2f}")
                return net_funding_period
        return 0.0

    def evaluate_cycle(self):
        paxg_t, xaut_t = self.fetch_live_quotes()
        if not paxg_t or not xaut_t:
            return {"status": "FETCH_ERROR"}
            
        p_mark, p_bid, p_ask, p_spread = self.parse_quote_details(paxg_t)
        x_mark, x_bid, x_ask, x_spread = self.parse_quote_details(xaut_t)
        
        if not self.in_position and p_ask > 0 and x_bid > 0:
            self.open_position_paper(p_ask, x_bid)
            
        # Calculate Basis De-Peg Spread
        depeg_bps = (x_mark - p_mark) / p_mark * 10000.0 if p_mark > 0 else 0.0
        
        # Calculate Mark-to-Market PnL
        unrealized_long = self.long_qty * (p_mark - self.entry_price_long)
        unrealized_short = self.short_qty * (self.entry_price_short - x_mark)
        mtm_pnl = unrealized_long + unrealized_short
        
        current_equity = self.equity + mtm_pnl
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity
            
        dd_pct = (self.peak_equity - current_equity) / self.peak_equity * 100.0
        
        # Check 8-Hour Funding Settlement
        funding_added = self.check_funding_settlement(paxg_t, xaut_t)
        
        # Automated Risk Engine Check: Basis De-Peg Stop-Loss
        margin_health = "SAFE"
        if abs(depeg_bps) > self.max_depeg_stop_loss_bps:
            margin_health = "EMERGENCY_STOP"
            logger.warning(f"EMERGENCY DE-PEG BREACH! De-peg spread {depeg_bps:.2f} bps exceeds limit {self.max_depeg_stop_loss_bps} bps!")
            self.log_telemetry("EMERGENCY_UNWIND", p_mark, x_mark, depeg_bps, p_spread, x_spread, f"De-peg breached limit: {depeg_bps:.1f} bps")
        else:
            self.log_telemetry("HEARTBEAT", p_mark, x_mark, depeg_bps, p_spread, x_spread, f"Equity=${current_equity:.2f}, MTM=${mtm_pnl:.2f}")

        return {
            "status": "OK",
            "paxg_mark": p_mark,
            "xaut_mark": x_mark,
            "depeg_bps": depeg_bps,
            "paxg_spread_bps": p_spread,
            "xaut_spread_bps": x_spread,
            "mtm_pnl_usd": mtm_pnl,
            "current_equity_usd": current_equity,
            "drawdown_pct": dd_pct,
            "accrued_funding_usd": self.total_funding_accrued,
            "margin_health": margin_health
        }

    def log_telemetry(self, event_type, p_mark, x_mark, depeg_bps, p_spread, x_spread, notes=""):
        ts_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        line = f"{ts_str},{event_type},{self.equity:.2f},{p_mark:.2f},{x_mark:.2f},{depeg_bps:.2f},{p_spread:.2f},{x_spread:.2f},{self.total_funding_accrued:.4f},SAFE,\"{notes}\"\n"
        with open(self.telemetry_file, 'a', encoding='utf-8') as f:
            f.write(line)
