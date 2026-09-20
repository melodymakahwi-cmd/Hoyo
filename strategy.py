"""
Detects your short-side entry setup on a series of OHLC candles:

  1. Supply zone mitigated  -> latest candle's high trades into a user-defined zone
  2. Shooting star formed   -> small body near the low, long upper wick, little lower wick
  3. Multi-wick rejection   -> several of the recent candles show upper wicks stalling
                               at/above the zone while closing back below it
  4. MA cross               -> fast MA crosses below slow MA (bearish) within the lookback

Candles are Deriv's format: dicts with epoch, open, high, low, close.
All price fields arrive as strings from the API — callers should cast to float
before passing in, which the helpers below also do defensively.
"""
from dataclasses import dataclass, field
from typing import List, Dict, Optional


@dataclass
class SupplyZone:
    zone_low: float
    zone_high: float
    note: str = ""


@dataclass
class SetupResult:
    triggered: bool
    zone_mitigated: bool = False
    shooting_star: bool = False
    multi_wick_rejection: bool = False
    ma_cross_bearish: bool = False
    details: Dict = field(default_factory=dict)


def _f(candle: dict, key: str) -> float:
    return float(candle[key])


def _is_shooting_star(candle: dict, min_upper_wick_ratio: float, max_body_ratio: float) -> bool:
    o, h, l, c = _f(candle, "open"), _f(candle, "high"), _f(candle, "low"), _f(candle, "close")
    full_range = h - l
    if full_range <= 0:
        return False
    body = abs(c - o)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    body_ratio = body / full_range
    upper_wick_ratio = upper_wick / full_range
    lower_wick_ratio = lower_wick / full_range
    return (
        upper_wick_ratio >= min_upper_wick_ratio
        and body_ratio <= max_body_ratio
        and lower_wick_ratio <= 0.15
    )


def _zone_mitigated(candle: dict, zone: SupplyZone) -> bool:
    h = _f(candle, "high")
    return zone.zone_low <= h <= zone.zone_high * 1.001 or (h >= zone.zone_low and _f(candle, "close") <= zone.zone_high)


def _multi_wick_rejection(candles: List[dict], zone: SupplyZone, lookback: int) -> bool:
    recent = candles[-lookback:]
    rejections = 0
    for c in recent:
        h, cl = _f(c, "high"), _f(c, "close")
        if h >= zone.zone_low and cl < zone.zone_high:
            rejections += 1
    return rejections >= 2


def _sma(values: List[float], period: int) -> Optional[List[float]]:
    if len(values) < period:
        return None
    out = []
    for i in range(period - 1, len(values)):
        out.append(sum(values[i - period + 1:i + 1]) / period)
    return out


def _ma_cross_bearish(closes: List[float], fast_period: int, slow_period: int) -> bool:
    fast = _sma(closes, fast_period)
    slow = _sma(closes, slow_period)
    if not fast or not slow:
        return False
    # Align lengths (slow series is shorter/later-starting)
    offset = len(fast) - len(slow)
    fast_aligned = fast[offset:]
    if len(fast_aligned) < 2 or len(slow) < 2:
        return False
    prev_fast, curr_fast = fast_aligned[-2], fast_aligned[-1]
    prev_slow, curr_slow = slow[-2], slow[-1]
    return prev_fast >= prev_slow and curr_fast < curr_slow


def detect_short_setup(
    candles: List[dict],
    zones: List[SupplyZone],
    ma_fast_period: int = 9,
    ma_slow_period: int = 21,
    lookback_candles_for_rejection: int = 5,
    min_upper_wick_ratio: float = 0.55,
    max_body_ratio: float = 0.35,
) -> SetupResult:
    if len(candles) < max(ma_slow_period + 2, lookback_candles_for_rejection + 1):
        return SetupResult(triggered=False, details={"reason": "not enough candles"})

    latest = candles[-1]
    closes = [_f(c, "close") for c in candles]

    matched_zone = None
    for z in zones:
        if _zone_mitigated(latest, z):
            matched_zone = z
            break

    if matched_zone is None:
        return SetupResult(triggered=False, details={"reason": "no zone mitigated"})

    shooting_star = _is_shooting_star(latest, min_upper_wick_ratio, max_body_ratio)
    rejection = _multi_wick_rejection(candles, matched_zone, lookback_candles_for_rejection)
    ma_cross = _ma_cross_bearish(closes, ma_fast_period, ma_slow_period)

    triggered = shooting_star and rejection and ma_cross

    return SetupResult(
        triggered=triggered,
        zone_mitigated=True,
        shooting_star=shooting_star,
        multi_wick_rejection=rejection,
        ma_cross_bearish=ma_cross,
        details={"zone": matched_zone.__dict__, "latest_close": closes[-1]},
    )
