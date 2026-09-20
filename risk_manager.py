"""
Tracks today's realized P&L from Deriv's transaction stream and enforces
the daily loss limit. Deriv's `transaction` stream sends an entry per
buy/sell with an `amount` and `action_type` (buy/sell). We accumulate
profit/loss for sells (contract settlements) for the current UTC day.
"""
import datetime as dt
import logging

log = logging.getLogger("risk_manager")


class RiskManager:
    def __init__(self, daily_loss_limit: float, hard_lock_when_breached: bool = True):
        self.daily_loss_limit = daily_loss_limit
        self.hard_lock_when_breached = hard_lock_when_breached
        self._day = self._today()
        self.realized_pnl_today = 0.0
        self.trades_today = 0
        self.locked = False
        self._on_limit_breached_cb = None

    @staticmethod
    def _today():
        return dt.datetime.utcnow().date()

    def on_limit_breached(self, callback):
        """callback(pnl: float, limit: float) called once per day when the limit is first breached."""
        self._on_limit_breached_cb = callback

    def _roll_day_if_needed(self):
        today = self._today()
        if today != self._day:
            log.info("New UTC day — resetting daily P&L and lock state")
            self._day = today
            self.realized_pnl_today = 0.0
            self.trades_today = 0
            self.locked = False

    async def handle_transaction(self, msg: dict):
        self._roll_day_if_needed()
        tx = msg.get("transaction", {})
        action = tx.get("action")
        amount = tx.get("amount", 0)
        # Deriv sends negative amounts for buys (money out) and the settled
        # amount for sells (money back). Net effect on balance == P&L impact.
        if action in ("sell", "buy"):
            self.realized_pnl_today += amount
            if action == "sell":
                self.trades_today += 1
            log.info("Transaction: %s amount=%.2f | today P&L=%.2f", action, amount, self.realized_pnl_today)

        was_locked = self.locked
        if self.realized_pnl_today <= -abs(self.daily_loss_limit):
            self.locked = True
            if not was_locked and self.hard_lock_when_breached and self._on_limit_breached_cb:
                await self._on_limit_breached_cb(self.realized_pnl_today, self.daily_loss_limit)

    def can_trade(self) -> bool:
        self._roll_day_if_needed()
        return not (self.locked and self.hard_lock_when_breached)

    def progress_summary(self) -> dict:
        self._roll_day_if_needed()
        return {
            "date": str(self._day),
            "realized_pnl_today": round(self.realized_pnl_today, 2),
            "daily_loss_limit": self.daily_loss_limit,
            "pct_of_limit_used": round(min(100.0, (max(0.0, -self.realized_pnl_today) / abs(self.daily_loss_limit)) * 100), 1),
            "trades_today": self.trades_today,
            "locked": self.locked,
        }
