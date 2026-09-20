"""
Wraps the existing monitoring logic (deriv_client, risk_manager, strategy,
news_monitor) so it can run in a background thread inside the Kivy app,
reporting state back to the UI via thread-safe callbacks.

This is the same logic as the desktop main.py — just adapted to run
inside an app with a UI instead of a terminal.
"""
import asyncio
import logging
import threading

from deriv_client import DerivClient
from risk_manager import RiskManager
from strategy import detect_short_setup, SupplyZone
from news_monitor import NewsMonitor

log = logging.getLogger("bot_core")


class BotCore:
    def __init__(self, config: dict, plan: dict, on_status, on_event, on_error):
        """
        config: dict matching config.example.json's shape
        plan: dict matching trade_plan.example.json's shape
        on_status(dict): called whenever P&L/lock/connection status changes
        on_event(title, body): called for a notification-worthy event
        on_error(message): called on a fatal error
        """
        self.config = config
        self.plan = plan
        self.on_status = on_status
        self.on_event = on_event
        self.on_error = on_error

        self._thread = None
        self._loop = None
        self._stop_flag = threading.Event()
        self.running = False

        self.client = None
        self.risk = None

    def start(self):
        if self.running:
            return
        self._stop_flag.clear()
        self._thread = threading.Thread(target=self._run_in_thread, daemon=True)
        self._thread.start()
        self.running = True

    def stop(self):
        self._stop_flag.set()
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
        self.running = False

    async def _shutdown(self):
        if self.client:
            await self.client.close()

    def _run_in_thread(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception as e:
            log.exception("Bot crashed")
            self.on_error(str(e))
        finally:
            self.running = False

    def zones_for(self, symbol: str):
        zones = self.plan.get("supply_zones", {}).get(symbol, [])
        return [SupplyZone(z["zone_low"], z["zone_high"], z.get("note", "")) for z in zones]

    async def _main(self):
        risk_cfg = self.config["risk"]
        self.risk = RiskManager(
            daily_loss_limit=risk_cfg["daily_loss_limit"],
            hard_lock_when_breached=risk_cfg.get("hard_lock_when_breached", True),
        )

        async def on_limit_breached(pnl, limit):
            self.on_event("Daily loss limit reached", f"P&L {pnl:.2f} past limit -{limit:.2f}. Trading locked.")
            self._push_status()

        self.risk.on_limit_breached(on_limit_breached)

        self.client = DerivClient(
            app_id=self.config["deriv"]["app_id"],
            api_token=self.config["deriv"]["api_token"],
            account_type=self.config["deriv"]["account_type"],
        )
        await self.client.connect()
        self.on_event("Connected", f"Logged in as {self.client.account_info.get('loginid')}")
        self._push_status(connected=True)

        async def on_transaction(msg):
            await self.risk.handle_transaction(msg)
            self._push_status()

        self.client.on("transaction", on_transaction)
        await self.client.subscribe_transactions()
        await self.client.subscribe_balance()

        symbols = self.config["deriv"]["symbols"]
        granularity = self.config["deriv"]["candle_granularity_seconds"]
        strat_cfg = self.config["strategy"]
        candle_cache = {s: [] for s in symbols}
        already_alerted = set()

        async def on_ohlc(msg):
            ohlc = msg.get("ohlc")
            if not ohlc:
                return
            symbol = ohlc.get("symbol")
            if symbol not in candle_cache:
                return
            candle = {"epoch": ohlc["open_time"], "open": ohlc["open"], "high": ohlc["high"],
                      "low": ohlc["low"], "close": ohlc["close"]}
            cache = candle_cache[symbol]
            if cache and cache[-1]["epoch"] == candle["epoch"]:
                cache[-1] = candle
            else:
                cache.append(candle)
                cache[:] = cache[-500:]

            zones = self.zones_for(symbol)
            if not zones or len(cache) < 30:
                return

            setup = detect_short_setup(
                cache, zones,
                ma_fast_period=strat_cfg.get("ma_fast_period", 9),
                ma_slow_period=strat_cfg.get("ma_slow_period", 21),
                lookback_candles_for_rejection=strat_cfg.get("lookback_candles_for_rejection", 5),
                min_upper_wick_ratio=strat_cfg.get("min_upper_wick_ratio", 0.55),
                max_body_ratio=strat_cfg.get("max_body_ratio", 0.35),
            )
            key = f"{symbol}:{candle['epoch']}"
            if setup.triggered and key not in already_alerted:
                already_alerted.add(key)
                if not self.risk.can_trade():
                    return
                self.on_event(f"Entry setup: {symbol}", "Zone + shooting star + rejection + MA cross confirmed.")

        self.client.on("ohlc", on_ohlc)
        for symbol in symbols:
            await self.client.subscribe_candles(symbol, granularity)

        news_cfg = self.config["news"]
        news_monitor = NewsMonitor(
            watch_currencies=news_cfg["watch_currencies"],
            impact_levels=news_cfg["impact_levels_to_alert"],
            minutes_before_alert=news_cfg["minutes_before_event_to_alert"],
        )

        async def news_loop():
            while not self._stop_flag.is_set():
                for event in await news_monitor.check():
                    self.on_event(
                        f"News: {event['title']} ({event['currency']})",
                        f"In ~{event['minutes_until']} min — {event['impact']} impact",
                    )
                await asyncio.sleep(news_cfg["check_interval_minutes"] * 60)

        async def status_loop():
            while not self._stop_flag.is_set():
                self._push_status()
                await asyncio.sleep(15)

        news_task = asyncio.ensure_future(news_loop())
        status_task = asyncio.ensure_future(status_loop())

        while not self._stop_flag.is_set():
            await asyncio.sleep(0.5)

        news_task.cancel()
        status_task.cancel()
        await self.client.close()

    def _push_status(self, connected=True):
        summary = self.risk.progress_summary() if self.risk else {}
        summary["connected"] = connected
        self.on_status(summary)
