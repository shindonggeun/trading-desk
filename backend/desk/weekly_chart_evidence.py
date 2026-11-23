"""Read-only observations from the chart's existing NQ=F candles (#675, #896)."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, timedelta

from . import channels, chart_read, indicators, market_calendar
from . import nq_weekly_research as nq

SOURCE = "yahoo"
USAGE = "research_only"
METHODS = {
    "bollinger": "bollinger_sma20_2",
    "rsi": "wilder_rsi14",
    "channel": "log_parallel_outer_lower",
    "fib": "log_extension_2_618",
}
PRICE_KEYS = {"bollinger": "bollinger", "channel": "channel", "fib": "fib_2618"}
REASONS = {
    "weekly_source_unavailable": "주봉 데이터를 받지 못했습니다.",
    "unsupported_weekly_source": "이 주봉 원천의 확정 기준이 확인되지 않았습니다.",
    "invalid_weekly_timestamp": "주봉 조회 시각을 확인할 수 없습니다.",
    "invalid_weekly_bars": "주봉의 날짜 순서, 연속성 또는 가격을 확인할 수 없습니다.",
    "no_completed_week": "조회한 봉에 확정 주봉이 없습니다.",
    "stale_weekly_bars": "가장 최근에 마감된 주의 확정 봉을 아직 받지 못했습니다.",
    "unconfirmed_weekly_close": "다음 거래 주 개장 뒤 원천에서 주봉 마감을 확인할 수 있습니다.",
    "weekly_evidence_error": "주봉 관측 근거를 계산할 수 없습니다.",
    "chart_price_unavailable": "비교에 사용할 차트 종가가 없습니다.",
    "invalid_chart_price": "비교에 사용할 차트 종가 또는 시각을 확인할 수 없습니다.",
    "stale_chart_price": "차트 종가가 관측 주 시작보다 오래되었습니다.",
    "insufficient_history": "판정에 필요한 확정 주봉이 부족합니다.",
    "atr_unavailable": "접근·이탈 범위에 쓸 직전 주봉 ATR이 없습니다.",
    "channel_unavailable": "기준에 맞는 로그 평행 채널이 없습니다.",
    "fib_anchors_unavailable": "확정된 고점·저점·고점 앵커를 찾지 못했습니다.",
    "invalid_price_level": "유효한 양수 가격대를 구할 수 없습니다.",
    "insufficient_overlap_evidence": "겹침 여부를 결정할 근거가 부족합니다.",
    "no_observation_week": f"최근 {nq.RECOVERY_WEEKS + 1}개 확정 주 중 조정률과 가격대 접촉 조건을 확인한 주가 없습니다.",
}


def _reason(code: str | None) -> dict:
    return {"reasonCode": code, "reason": REASONS[code] if code else None}


def unavailable(code: str, now: datetime) -> dict:
    return {"status": "unavailable", "observedAt": now.astimezone(UTC).isoformat(), "usage": USAGE, **_reason(code)}


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return nq._aware(value).astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def _positive(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _expected_week(now: datetime) -> date:
    day = now.astimezone(nq.NY).date()
    monday = day - timedelta(days=day.weekday())
    return monday if nq._week_closed_at(monday) <= now else monday - nq.WEEK


def _current(chart_price: dict | None, week: date, now: datetime) -> dict:
    empty = {"price": None, "source": None, "asOf": None, "fetchedAt": None, "timeframe": None,
             "kind": "chart_bar_close"}
    if not chart_price:
        return {**empty, **_reason("chart_price_unavailable")}
    price, source, timeframe = (chart_price.get(key) for key in ("price", "source", "timeframe"))
    at, fetched = (_timestamp(chart_price.get(key)) for key in ("at", "fetched_at"))
    if (not _positive(price) or not isinstance(source, str) or not source
            or timeframe not in channels.TIMEFRAMES or at is None or fetched is None or not at <= fetched <= now):
        return {**empty, **_reason("invalid_chart_price")}
    if at.astimezone(nq.NY).date() < week:
        return {**empty, **_reason("stale_chart_price")}
    return {
        "price": price, "source": source, "asOf": at.isoformat(), "fetchedAt": fetched.isoformat(),
        "timeframe": timeframe, "kind": "chart_bar_close", **_reason(None),
    }


def chart_price(tfs: Iterable[str], results: Iterable[object], now: datetime) -> dict | None:
    """Select the latest usable bar label, keeping the requested short-first order on ties."""
    week = _expected_week(now)
    first, newest = None, None
    for tf, result in zip(tfs, results, strict=False):
        if not isinstance(result, dict):
            continue
        bars = result.get("bars")
        if not isinstance(bars, list) or not bars or not isinstance(last := bars[-1], dict):
            continue
        candidate = {"price": last.get("close"), "at": last.get("at"), "source": result.get("source"),
                     "fetched_at": result.get("fetched_at"), "timeframe": tf}
        current = _current(candidate, week, now)
        if current["asOf"] is not None:
            at = datetime.fromisoformat(current["asOf"])
            if newest is None or at > newest[0]:
                newest = at, candidate
        if first is None:
            first = candidate
    return newest[1] if newest is not None else first


def _evidence_row(key: str, value: float | None, state: str, reason: str | None = None,
                  *, distance: float | None = None, anchors: Sequence[str] = ()) -> dict:
    return {"value": value, "state": state, **_reason(reason), "distancePct": distance,
            "method": METHODS[key], "anchorWeeks": list(anchors)}


def _price_state(bar: dict, level: float, atr: float) -> str:
    if bar["close"] < level - nq.APPROACH_ATR * atr:
        return "below"
    return "near" if nq._near(bar["low"], level, atr) else "normal"


def _evidence(bar: dict, basis: nq._PriceBasis, rsi: float | None, current: float | None) -> dict:
    no_level = {"bollinger": "insufficient_history", "channel": "channel_unavailable",
                "fib": "fib_anchors_unavailable"}
    anchors = {"channel": basis.channel_anchors, "fib": basis.fib_anchors}
    evidence = {}
    for key, field in PRICE_KEYS.items():
        level = basis.levels[field]
        if basis.atr <= 0 and (key != "bollinger" or level is not None):
            evidence[key] = _evidence_row(key, None, "missing", "atr_unavailable")
        elif level is None:
            evidence[key] = _evidence_row(key, None, "missing", no_level[key])
        elif not _positive(level):
            evidence[key] = _evidence_row(key, None, "missing", "invalid_price_level")
        else:
            distance = (level / current - 1) * 100 if current is not None else None
            evidence[key] = _evidence_row(key, level, _price_state(bar, level, basis.atr), distance=distance,
                                          anchors=anchors.get(key, ()))
    if rsi is None:
        evidence["rsi"] = _evidence_row("rsi", None, "missing", "insufficient_history")
    else:
        evidence["rsi"] = _evidence_row("rsi", rsi, "oversold" if rsi <= nq.RSI_LIMIT else "normal")
    return evidence


def _overlap(evidence: dict) -> dict:
    near = [key for key in PRICE_KEYS if evidence[key]["state"] == "near"]
    oversold = evidence["rsi"]["state"] == "oversold"
    if nq._confluent(len(near), oversold):
        levels = [evidence[key]["value"] for key in near]
        return {"state": "overlap", "low": min(levels), "high": max(levels),
                "basis": near + (["rsi"] if oversold else []), **_reason(None)}
    unknown = sum(evidence[key]["state"] == "missing" for key in PRICE_KEYS)
    uncertain = nq._confluent(len(near) + unknown, oversold or evidence["rsi"]["state"] == "missing")
    return {"state": "missing" if uncertain else "none", "low": None, "high": None, "basis": [],
            **_reason("insufficient_overlap_evidence" if uncertain else None)}


def _at(bars: list[dict], bands: list, index: int) -> nq._PriceBasis:
    band = bands[index - 1] if index else None
    return nq._price_basis(bars[:index], band["lower"] if band is not None else None)


def _latest_anchor(bars: list[dict], bands: list, latest: nq._PriceBasis) -> tuple[int, nq._PriceBasis] | None:
    first = max(nq.WARMUP, len(bars) - 1 - nq.RECOVERY_WEEKS)
    for i in range(len(bars) - 1, first - 1, -1):
        if nq._drawdown(bars, i) < nq.CORRECTION_PCT:
            continue
        basis = latest if i == len(bars) - 1 else _at(bars, bands, i)
        if basis.atr > 0 and any(_positive(level) and nq._near(bars[i]["low"], level, basis.atr)
                                 for level in basis.levels.values()):
            return i, basis
    return None


def _check(result: tuple[str, str | None], observed: int, window: int, *, anchor: str, source: str,
           condition: str, invalidation: str, threshold: float, stop: float) -> dict:
    outcome, decision = result
    state = {"success": "confirmed", "invalidated": "invalidated", "expired": "not_confirmed"}[outcome]
    if outcome == "expired" and observed < window:
        state, decision = "watching", None
    return {"state": state, "condition": condition, "invalidation": invalidation, "anchorWeek": anchor,
            "decisionWeek": decision, "source": source, "windowWeeks": window, "threshold": threshold,
            "invalidationLevel": stop, **_reason(None)}


def _checks(bars: list[dict], bands: list, latest: nq._PriceBasis, source: str) -> tuple[dict, dict]:
    selected = _latest_anchor(bars, bands, latest)
    if selected is None:
        code = "insufficient_history" if len(bars) <= nq.WARMUP else "no_observation_week"
        empty = {"state": "missing", "condition": None, "invalidation": None, "anchorWeek": None,
                 "decisionWeek": None, "source": source, "threshold": None, "invalidationLevel": None, **_reason(code)}
        return {**empty, "windowWeeks": nq.BOUNCE_WEEKS}, {**empty, "windowWeeks": nq.RECOVERY_WEEKS}
    index, basis = selected
    anchor = bars[index]["at"]
    terms = nq._terms(bars[:index], bars[index], basis.atr)
    future = bars[index + 1:index + 1 + nq.RECOVERY_WEEKS]
    short = future[:nq.BOUNCE_WEEKS]
    uptrends = [chart_read.trend_state(bars[:end])["state"] == "up"
                for end in range(index + 2, index + 2 + len(future))]
    rebound = _check(nq._reaction(short, terms.bounce_target, terms.stop), len(short), nq.BOUNCE_WEEKS,
                     anchor=anchor, source=source,
                     condition=f"다음 {nq.BOUNCE_WEEKS}주 안에 주봉 종가가 {terms.bounce_target:,.2f} 이상",
                     invalidation=f"확정 주봉의 저가가 {terms.stop:,.2f} 미만",
                     threshold=terms.bounce_target, stop=terms.stop)
    recovery = _check(nq._recovery(future, uptrends, terms.resistance, terms.stop), len(future), nq.RECOVERY_WEEKS,
                      anchor=anchor, source=source,
                      condition=(f"다음 {nq.RECOVERY_WEEKS}주 안에 상승 추세와 종가 {terms.resistance:,.2f} 초과가 "
                                 f"{nq.RECOVERY_CONFIRM}주 연속"),
                      invalidation=f"확정 주봉의 종가가 {terms.stop:,.2f} 미만",
                      threshold=terms.resistance, stop=terms.stop)
    return rebound, recovery


def build(weekly: dict | None, chart_price: dict | None, now: datetime) -> dict:
    """Use only known completed weeks; the chart-price reference affects distances only."""
    if now.utcoffset() is None:
        raise ValueError("now must include a UTC offset")
    now = now.astimezone(UTC)
    root = {"observedAt": now.isoformat(), "usage": USAGE}

    if weekly is None:
        return unavailable("weekly_source_unavailable", now)
    if weekly.get("source") != SOURCE:
        return unavailable("unsupported_weekly_source", now)
    fetched = _timestamp(weekly.get("fetched_at"))
    if fetched is None or fetched > now:
        return unavailable("invalid_weekly_timestamp", now)
    raw = weekly.get("bars")
    try:
        bars = nq._closed(raw, fetched)
    except (TypeError, ValueError, ArithmeticError):
        return unavailable("invalid_weekly_bars", now)
    if not bars:
        return unavailable("no_completed_week", now)
    expected = _expected_week(now)
    next_open = datetime.combine(expected + timedelta(days=6), market_calendar.CME_OPENS, nq.NY)
    if now < next_open:
        return unavailable("unconfirmed_weekly_close", now)
    monday = date.fromisoformat(bars[-1]["at"])
    if monday != expected:
        return unavailable("stale_weekly_bars", now)
    # fetched_at is recorded after the download. A Friday request that returns
    # after the cutoff may still contain a forming week's OHLC.
    if fetched < next_open or len(raw) == len(bars) or nq._monday(raw[len(bars)]["at"]) != monday + nq.WEEK:
        return unavailable("unconfirmed_weekly_close", now)

    current = _current(chart_price, monday, now)
    closes = [bar["close"] for bar in bars]
    bands = indicators.bollinger(closes, nq.BB_WEEKS, nq.BB_MULT)
    rsi = indicators.rsi(closes, nq.RSI_WEEKS)[-1]
    latest = _at(bars, bands, len(bars) - 1)
    evidence = _evidence(bars[-1], latest, rsi, current["price"])
    rebound, recovery = _checks(bars, bands, latest, SOURCE)
    return {
        "status": "available", **root,
        "assumptions": {"series": "NQ=F", "referenceSeries": "NQ1!", "rollover": "unknown",
                        "originalSettings": "unknown", "originalAnchors": "unknown"},
        "week": {"start": monday.isoformat(), "source": SOURCE, "candle": "closed",
                 "closedAt": nq._week_closed_at(monday).astimezone(UTC).isoformat(), "fetchedAt": fetched.isoformat()},
        "current": current, "evidence": evidence, "overlap": _overlap(evidence),
        "rebound": rebound, "recovery": recovery,
    }
