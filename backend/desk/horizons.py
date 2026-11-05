"""Trading horizons (#63): how long a trade is meant to live, chosen per run apart from the
investing style (desk.profile). Each horizon picks the bars support/resistance and ATR come
from (desk.sr, desk.levels), how far stop and target sit in ATRs, and the instructions the
deciding agents get (``horizon_mandate``).

``swing`` is the desk's behaviour before horizons existed: daily + weekly bars, stop 1.5 ATR,
target 3 ATR. A run saved without a horizon reads as swing.

TradingAgents' own indicators stay daily; the horizon reaches the agents only through the
mandate and the desk's levels note, so a scalp plan is a reference map, not a trigger.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Horizon:
    id: str
    label: str                 # Korean, for the page
    holding: str               # Korean, for the page
    holding_en: str            # for the agents
    primary: str               # bar interval levels and ATR come from (desk.prices INTERVALS)
    primary_bars: int
    primary_k: int             # bars on each side a swing high/low must beat
    primary_label: str
    secondary: str             # the higher timeframe: stronger zones, range, last-bar pivot
    secondary_bars: int
    secondary_k: int
    secondary_label: str
    secondary_days: int        # a secondary bar is finished this many days after it opens
    range_bars: int            # secondary bars whose high/low form the range
    range_label: str
    last_label: str            # the last finished secondary bar ("지난주", "전일")
    pivot_label: str
    stop_atr: float
    target_atr: float
    min_rr: float
    flat_by_close: bool        # close before the session ends, never hold overnight
    valid_minutes: int | None  # how long the plan's entry stays valid after the quote it used
    max_distance_atr: float    # zones further than these are not actionable on this horizon
    max_distance_pct: float
    tolerance_pct: float       # candidates closer than this (or half an ATR) are one zone
    half_life: timedelta       # an older primary swing counts half after this long
    secondary_half_life: timedelta  # the same for the secondary bars' swings
    sr_ttl_sec: float
    exit_by: str               # what "Exit by" means on this horizon, for the agents
    note: str = ""             # extra instruction for this horizon only

    def ma_label(self, n: int) -> str:
        return f"{n}일선" if self.primary == "1d" else f"{n}봉선({self.primary_label})"

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "holding": self.holding}


HORIZONS: dict[str, Horizon] = {h.id: h for h in (
    Horizon(
        id="scalp", label="스캘핑", holding="수 분~1시간", holding_en="minutes to about an hour",
        primary="1m", primary_bars=200, primary_k=5, primary_label="1분봉",
        secondary="1d", secondary_bars=200, secondary_k=3, secondary_label="일봉", secondary_days=1,
        range_bars=5, range_label="5일", last_label="전일", pivot_label="전일 피벗",
        stop_atr=2.0, target_atr=3.0, min_rr=1.5, flat_by_close=True, valid_minutes=15,
        max_distance_atr=8.0, max_distance_pct=0.02, tolerance_pct=0.0005,
        half_life=timedelta(minutes=90), secondary_half_life=timedelta(days=30), sr_ttl_sec=30.0,
        exit_by="the same session, within about 15 minutes of entry or at stop/target, and before the close in any case",
        note=("This analysis takes minutes and its indicators are daily, so the result is a reference map, "
              "not a trigger: the owner enters only if the price is still within half an ATR of the entry "
              "when the plan arrives and the entry condition shows on the 1-minute tape; the plan expires "
              "15 minutes after the quoted price."),
    ),
    Horizon(
        id="intraday", label="단타", holding="당일", holding_en="the same trading day",
        primary="5m", primary_bars=390, primary_k=3, primary_label="5분봉",
        secondary="1d", secondary_bars=200, secondary_k=3, secondary_label="일봉", secondary_days=1,
        range_bars=20, range_label="20일", last_label="전일", pivot_label="전일 피벗",
        stop_atr=2.0, target_atr=4.0, min_rr=2.0, flat_by_close=True, valid_minutes=60,
        max_distance_atr=8.0, max_distance_pct=0.05, tolerance_pct=0.001,
        half_life=timedelta(days=1), secondary_half_life=timedelta(days=30), sr_ttl_sec=60.0,
        exit_by="before today's close, never overnight",
        note=("Daily indicators are backdrop only; entry and stop come from today's 5-minute structure and "
              "the prior session's high/low/pivot listed below."),
    ),
    Horizon(
        id="short_swing", label="2~3일", holding="2~3 거래일", holding_en="two to three trading sessions",
        primary="60m", primary_bars=390, primary_k=3, primary_label="60분봉",
        secondary="1d", secondary_bars=200, secondary_k=3, secondary_label="일봉", secondary_days=1,
        range_bars=20, range_label="20일", last_label="전일", pivot_label="전일 피벗",
        stop_atr=2.5, target_atr=5.0, min_rr=2.0, flat_by_close=False, valid_minutes=1440,
        max_distance_atr=6.0, max_distance_pct=0.15, tolerance_pct=0.0025,
        half_life=timedelta(days=30), secondary_half_life=timedelta(days=90), sr_ttl_sec=120.0,
        exit_by="the session by which the trade is closed if neither level is hit, within 2-3 sessions",
    ),
    Horizon(
        id="swing", label="스윙", holding="1~3주", holding_en="one to three weeks",
        primary="1d", primary_bars=200, primary_k=3, primary_label="일봉",
        secondary="1w", secondary_bars=156, secondary_k=2, secondary_label="주봉", secondary_days=7,
        range_bars=52, range_label="52주", last_label="지난주", pivot_label="주간 피벗",
        stop_atr=1.5, target_atr=3.0, min_rr=2.0, flat_by_close=False, valid_minutes=None,
        max_distance_atr=6.0, max_distance_pct=0.35, tolerance_pct=0.005,
        half_life=timedelta(days=365), secondary_half_life=timedelta(days=365), sr_ttl_sec=300.0,
        exit_by="the week by which the trade is reviewed if neither level is hit",
    ),
)}
DEFAULT_HORIZON = "swing"


def get(horizon: str | None) -> Horizon:
    """The horizon by id; None (a run saved before horizons) is swing."""
    return HORIZONS[horizon or DEFAULT_HORIZON]


def horizon_mandate(horizon: Horizon, atr: float | None = None) -> str:
    """Instructions for the deciding agents. Long, short and Hold are weighed alike, and
    every call names how it is entered, what voids it and when it is closed."""
    atr_part = f" ({atr})" if atr else ""
    text = (
        f"Trading horizon chosen by the owner for this run: {horizon.label} ({horizon.holding_en}). "
        "This overrides any holding period in the investor mandate above. Judge the setup on this horizon "
        "only: a thesis that needs more time is Hold, not a smaller Buy. Long, short and Hold carry equal "
        "weight - pick what the evidence on this horizon supports and say what would have made you choose "
        f"each of the other two. Use the desk's {horizon.primary_label} ({horizon.primary}) bars and their "
        f"ATR14{atr_part} below: stop about {horizon.stop_atr:g} ATR and target at least "
        f"{horizon.target_atr:g} ATR from the entry (reward:risk at least {horizon.min_rr:g}), moved only to "
        "sit beyond or before a listed zone. Every call, Hold included, states the entry type (now / limit "
        "at a level / stop on the break of a level, and what confirms it), the invalidation (the price or "
        f"event that voids the thesis before the stop) and the exit ({horizon.exit_by}). The Time Horizon "
        f"field states this holding ({horizon.holding_en}). Right after the final levels line that ends "
        "the Executive Summary, add one more line 'Entry: <now|limit|stop> / Invalidation: <text> / "
        "Exit by: <time or condition>' (keep these English labels in any output language)."
    )
    return f"{text} {horizon.note}" if horizon.note else text


def valid_until(horizon: Horizon, base: str | None) -> str | None:
    """When the plan's entry stops being valid: ``base`` (ISO time of the quote the agents saw)
    plus the horizon's window. None for swing or without a base time."""
    if not horizon.valid_minutes or not base:
        return None
    return (datetime.fromisoformat(base) + timedelta(minutes=horizon.valid_minutes)).isoformat(timespec="seconds")
