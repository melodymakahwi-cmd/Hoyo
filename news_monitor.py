"""
Polls a free, publicly-available economic calendar feed (Forex Factory's
"this week" JSON, commonly used for this exact purpose) and alerts when a
high-impact event for a watched currency is coming up soon.

If this feed ever changes shape or goes away, swap `fetch_events` to call
any economic-calendar API you have a key for (e.g. TradingEconomics,
FinancialModelingPrep) — everything downstream just needs a list of dicts
with: title, currency, impact, date (ISO string).
"""
import datetime as dt
import logging
import httpx

log = logging.getLogger("news_monitor")

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


async def fetch_events() -> list:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(FEED_URL, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        return resp.json()


def _parse_event_time(ev: dict) -> dt.datetime:
    # Feed provides an ISO-ish date string already in UTC.
    raw = ev.get("date")
    return dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))


class NewsMonitor:
    def __init__(self, watch_currencies, impact_levels, minutes_before_alert: int):
        self.watch_currencies = set(c.upper() for c in watch_currencies)
        self.impact_levels = set(i.lower() for i in impact_levels)
        self.minutes_before_alert = minutes_before_alert
        self._alerted_keys = set()

    async def check(self) -> list:
        """Returns newly-triggered events (not previously alerted) that are
        high-impact, for a watched currency, and within the alert window."""
        try:
            events = await fetch_events()
        except Exception as e:
            log.warning("Could not fetch news calendar: %s", e)
            return []

        now = dt.datetime.now(dt.timezone.utc)
        triggered = []
        for ev in events:
            impact = str(ev.get("impact", "")).lower()
            currency = str(ev.get("country", ev.get("currency", ""))).upper()
            if impact not in self.impact_levels or currency not in self.watch_currencies:
                continue
            try:
                event_time = _parse_event_time(ev)
            except Exception:
                continue
            minutes_until = (event_time - now).total_seconds() / 60
            key = f"{ev.get('title')}|{currency}|{event_time.isoformat()}"
            if 0 <= minutes_until <= self.minutes_before_alert and key not in self._alerted_keys:
                self._alerted_keys.add(key)
                triggered.append({
                    "title": ev.get("title"),
                    "currency": currency,
                    "impact": impact,
                    "event_time_utc": event_time.isoformat(),
                    "minutes_until": round(minutes_until, 1),
                })
        return triggered
