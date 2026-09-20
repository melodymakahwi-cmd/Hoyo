"""
Deriv Monitor — Kivy Android app.

Screens (all in one window, switched by tabs at the top):
  - Status: connection, today's P&L vs limit, start/stop
  - Settings: API token, account type, symbols, daily limit, email (optional)
  - Plan: trade plan rules + supply zones (view/edit)
  - Log: recent events

All settings persist on-device via Kivy's JsonStore (no manual config.json
editing needed). Alerts fire as native Android notifications via plyer,
and also still send email if you fill in the email fields.
"""
import json
import queue
import threading

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.scrollview import ScrollView
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.uix.progressbar import ProgressBar
from kivy.storage.jsonstore import JsonStore
from kivy.metrics import dp

from bot_core import BotCore

try:
    from plyer import notification as plyer_notification
except Exception:
    plyer_notification = None


DEFAULT_CONFIG = {
    "deriv": {
        "app_id": "1089",
        "api_token": "",
        "account_type": "demo",
        "symbols": ["frxEURUSD", "frxGBPUSD"],
        "candle_granularity_seconds": 300,
    },
    "risk": {"daily_loss_limit": 100.0, "currency": "USD", "hard_lock_when_breached": True},
    "strategy": {
        "ma_fast_period": 9, "ma_slow_period": 21,
        "lookback_candles_for_rejection": 5,
        "min_upper_wick_ratio": 0.55, "max_body_ratio": 0.35,
    },
    "news": {
        "check_interval_minutes": 15,
        "impact_levels_to_alert": ["High"],
        "minutes_before_event_to_alert": 30,
        "watch_currencies": ["USD", "EUR", "GBP", "JPY"],
    },
    "email": {
        "smtp_host": "smtp.gmail.com", "smtp_port": 587,
        "smtp_username": "", "smtp_password": "",
        "from_address": "", "to_address": "",
    },
}

DEFAULT_PLAN = {
    "plan_name": "Short-side Supply Zone Rejection",
    "bias": "short",
    "max_trades_per_day": 3,
    "rules": [
        "Only trade when price mitigates a marked supply zone",
        "Wait for a shooting star candle on the zone",
        "Confirm with multi-wick rejection at the zone",
        "Confirm with fast MA crossing below slow MA",
        "No trade if high-impact news is within 30 minutes",
    ],
    "supply_zones": {},
}


def notify(title, body):
    if plyer_notification:
        try:
            plyer_notification.notify(title=title, message=body, timeout=10)
            return
        except Exception:
            pass
    print(f"[NOTIFY] {title}: {body}")


class LabeledInput(BoxLayout):
    def __init__(self, label_text, value="", **kwargs):
        super().__init__(orientation="vertical", size_hint_y=None, height=dp(56), **kwargs)
        self.add_widget(Label(text=label_text, size_hint_y=None, height=dp(18),
                               font_size="12sp", color=(0.6, 0.6, 0.6, 1), halign="left"))
        self.input = TextInput(text=str(value), multiline=False, size_hint_y=None, height=dp(36))
        self.add_widget(self.input)

    @property
    def text(self):
        return self.input.text

    @text.setter
    def text(self, v):
        self.input.text = str(v)


class StatusScreen(Screen):
    def __init__(self, app_ref, **kwargs):
        super().__init__(name="status", **kwargs)
        self.app_ref = app_ref
        root = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12))

        self.pnl_label = Label(text="0.00", font_size="32sp", size_hint_y=None, height=dp(48))
        root.add_widget(self.pnl_label)

        self.limit_label = Label(text="limit: -0.00", size_hint_y=None, height=dp(20), color=(0.6, 0.6, 0.6, 1))
        root.add_widget(self.limit_label)

        self.bar = ProgressBar(max=100, value=0, size_hint_y=None, height=dp(10))
        root.add_widget(self.bar)

        self.status_label = Label(text="Not connected", size_hint_y=None, height=dp(24))
        root.add_widget(self.status_label)

        self.toggle_btn = Button(text="Start monitoring", size_hint_y=None, height=dp(48))
        self.toggle_btn.bind(on_press=self.on_toggle)
        root.add_widget(self.toggle_btn)

        root.add_widget(Label(text="Recent events", size_hint_y=None, height=dp(20),
                               color=(0.6, 0.6, 0.6, 1)))
        scroll = ScrollView()
        self.log_box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(4))
        self.log_box.bind(minimum_height=self.log_box.setter("height"))
        scroll.add_widget(self.log_box)
        root.add_widget(scroll)

        self.add_widget(root)

    def on_toggle(self, *_):
        self.app_ref.toggle_bot()

    def set_running(self, running):
        self.toggle_btn.text = "Stop monitoring" if running else "Start monitoring"

    def push_log(self, title, body):
        row = Label(text=f"[b]{title}[/b]\n{body}", markup=True, size_hint_y=None,
                     height=dp(46), font_size="12sp", halign="left", valign="top")
        row.bind(size=lambda w, s: setattr(w, "text_size", (w.width, None)))
        self.log_box.add_widget(row, index=0)

    def update_status(self, summary: dict):
        pnl = summary.get("realized_pnl_today", 0.0)
        limit = summary.get("daily_loss_limit", 0.0)
        locked = summary.get("locked", False)
        connected = summary.get("connected", False)
        self.pnl_label.text = f"{pnl:+.2f}"
        self.pnl_label.color = (0.94, 0.28, 0.30, 1) if pnl < 0 else (0.25, 0.72, 0.35, 1)
        self.limit_label.text = f"limit: -{abs(limit):.2f}   trades: {summary.get('trades_today', 0)}"
        self.bar.value = summary.get("pct_of_limit_used", 0)
        state = "LOCKED — limit reached" if locked else ("Connected — watching" if connected else "Not connected")
        self.status_label.text = state


class SettingsScreen(Screen):
    def __init__(self, app_ref, **kwargs):
        super().__init__(name="settings", **kwargs)
        self.app_ref = app_ref
        scroll = ScrollView()
        root = GridLayout(cols=1, padding=dp(16), spacing=dp(10), size_hint_y=None)
        root.bind(minimum_height=root.setter("height"))

        cfg = app_ref.config

        self.token = LabeledInput("Deriv API token", cfg["deriv"]["api_token"])
        self.account_type = LabeledInput("Account type (demo / real)", cfg["deriv"]["account_type"])
        self.symbols = LabeledInput("Symbols (comma-separated)", ",".join(cfg["deriv"]["symbols"]))
        self.granularity = LabeledInput("Candle seconds", cfg["deriv"]["candle_granularity_seconds"])
        self.daily_limit = LabeledInput("Daily loss limit", cfg["risk"]["daily_loss_limit"])
        self.email_user = LabeledInput("Email address (optional)", cfg["email"]["smtp_username"])
        self.email_pass = LabeledInput("Email app password (optional)", cfg["email"]["smtp_password"])

        for w in [self.token, self.account_type, self.symbols, self.granularity,
                  self.daily_limit, self.email_user, self.email_pass]:
            root.add_widget(w)

        save_btn = Button(text="Save settings", size_hint_y=None, height=dp(48))
        save_btn.bind(on_press=self.save)
        root.add_widget(save_btn)

        self.msg = Label(text="", size_hint_y=None, height=dp(20), color=(0.25, 0.72, 0.35, 1))
        root.add_widget(self.msg)

        scroll.add_widget(root)
        self.add_widget(scroll)

    def save(self, *_):
        cfg = self.app_ref.config
        cfg["deriv"]["api_token"] = self.token.text.strip()
        cfg["deriv"]["account_type"] = self.account_type.text.strip() or "demo"
        cfg["deriv"]["symbols"] = [s.strip() for s in self.symbols.text.split(",") if s.strip()]
        try:
            cfg["deriv"]["candle_granularity_seconds"] = int(self.granularity.text)
        except ValueError:
            pass
        try:
            cfg["risk"]["daily_loss_limit"] = float(self.daily_limit.text)
        except ValueError:
            pass
        cfg["email"]["smtp_username"] = self.email_user.text.strip()
        cfg["email"]["smtp_password"] = self.email_pass.text.strip()
        cfg["email"]["from_address"] = self.email_user.text.strip()
        cfg["email"]["to_address"] = self.email_user.text.strip()
        self.app_ref.save_config()
        self.msg.text = "Saved."


class PlanScreen(Screen):
    def __init__(self, app_ref, **kwargs):
        super().__init__(name="plan", **kwargs)
        self.app_ref = app_ref
        scroll = ScrollView()
        root = GridLayout(cols=1, padding=dp(16), spacing=dp(10), size_hint_y=None)
        root.bind(minimum_height=root.setter("height"))

        root.add_widget(Label(text="Rules (one per line)", size_hint_y=None, height=dp(20),
                               color=(0.6, 0.6, 0.6, 1)))
        self.rules_input = TextInput(text="\n".join(app_ref.plan["rules"]), size_hint_y=None, height=dp(140))
        root.add_widget(self.rules_input)

        root.add_widget(Label(
            text="Supply zones — one per line as: SYMBOL,low,high,note",
            size_hint_y=None, height=dp(36), color=(0.6, 0.6, 0.6, 1)))
        zones_text = "\n".join(
            f"{sym},{z['zone_low']},{z['zone_high']},{z.get('note', '')}"
            for sym, zones in app_ref.plan.get("supply_zones", {}).items()
            for z in zones
        )
        self.zones_input = TextInput(text=zones_text, size_hint_y=None, height=dp(140))
        root.add_widget(self.zones_input)

        save_btn = Button(text="Save plan", size_hint_y=None, height=dp(48))
        save_btn.bind(on_press=self.save)
        root.add_widget(save_btn)
        self.msg = Label(text="", size_hint_y=None, height=dp(20), color=(0.25, 0.72, 0.35, 1))
        root.add_widget(self.msg)

        scroll.add_widget(root)
        self.add_widget(scroll)

    def save(self, *_):
        plan = self.app_ref.plan
        plan["rules"] = [line.strip() for line in self.rules_input.text.split("\n") if line.strip()]

        zones = {}
        for line in self.zones_input.text.split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 3:
                continue
            symbol, low, high = parts[0], parts[1], parts[2]
            note = parts[3] if len(parts) > 3 else ""
            try:
                zones.setdefault(symbol, []).append(
                    {"zone_low": float(low), "zone_high": float(high), "note": note})
            except ValueError:
                continue
        plan["supply_zones"] = zones
        self.app_ref.save_plan()
        self.msg.text = "Saved."


class RootLayout(BoxLayout):
    pass


class DerivMonitorApp(App):
    def build(self):
        self.store = JsonStore(self.user_data_dir + "/deriv_monitor_store.json")
        self.config_data = self._load("config", DEFAULT_CONFIG)
        self.plan = self._load("plan", DEFAULT_PLAN)
        self.bot = None
        self.event_queue = queue.Queue()

        root = BoxLayout(orientation="vertical")
        tabs = BoxLayout(size_hint_y=None, height=dp(44))
        self.sm = ScreenManager()

        self.status_screen = StatusScreen(self)
        self.settings_screen = SettingsScreen(self)
        self.plan_screen = PlanScreen(self)
        for scr in (self.status_screen, self.settings_screen, self.plan_screen):
            self.sm.add_widget(scr)

        for name, label in [("status", "Status"), ("settings", "Settings"), ("plan", "Plan")]:
            btn = Button(text=label)
            btn.bind(on_press=lambda inst, n=name: setattr(self.sm, "current", n))
            tabs.add_widget(btn)

        root.add_widget(tabs)
        root.add_widget(self.sm)

        Clock.schedule_interval(self._drain_events, 0.5)
        return root

    @property
    def config(self):
        return self.config_data

    def _load(self, key, default):
        if self.store.exists(key):
            return self.store.get(key)["data"]
        data = json.loads(json.dumps(default))  # deep copy
        self.store.put(key, data=data)
        return data

    def save_config(self):
        self.store.put("config", data=self.config_data)

    def save_plan(self):
        self.store.put("plan", data=self.plan)

    def toggle_bot(self):
        if self.bot and self.bot.running:
            self.bot.stop()
            self.status_screen.set_running(False)
            return

        if not self.config_data["deriv"]["api_token"]:
            self.status_screen.push_log("Missing token", "Add your Deriv API token in Settings first.")
            return

        self.bot = BotCore(
            config=self.config_data,
            plan=self.plan,
            on_status=lambda s: self.event_queue.put(("status", s)),
            on_event=lambda t, b: self.event_queue.put(("event", (t, b))),
            on_error=lambda m: self.event_queue.put(("error", m)),
        )
        self.bot.start()
        self.status_screen.set_running(True)

    def _drain_events(self, *_):
        while not self.event_queue.empty():
            kind, payload = self.event_queue.get_nowait()
            if kind == "status":
                self.status_screen.update_status(payload)
            elif kind == "event":
                title, body = payload
                self.status_screen.push_log(title, body)
                notify(title, body)
            elif kind == "error":
                self.status_screen.push_log("Error", payload)
                self.status_screen.set_running(False)


if __name__ == "__main__":
    DerivMonitorApp().run()
