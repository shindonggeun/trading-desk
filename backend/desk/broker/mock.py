from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta, timezone

from .base import INSTITUTION_KINDS, INVESTORS, Broker, Fill, FlowRecord, Holding, IndexPrice, Listing

_KST = timezone(timedelta(hours=9))
# Rough size of one day's trading value per side (KRW) and of each class's share of it.
_DAY_VALUE = {"KOSPI": 10_000_000_000_000, "KOSDAQ": 8_000_000_000_000}
_DAYS_PER = {"1d": 1, "1w": 5, "1mo": 21, "1y": 250}
_SHARE = {"individual": 0.48, "foreigner": 0.30, "institution": 0.19, "other_corporation": 0.03}


def _step_back(day: date, interval: str) -> date:
    if interval == "1d":
        day -= timedelta(days=1)
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        return day
    if interval == "1w":
        return day - timedelta(days=7)
    if interval == "1mo":
        return (day.replace(day=1) - timedelta(days=1)).replace(day=1)
    return day.replace(year=day.year - 1, month=1, day=1)


def _sample_record(market: str, interval: str, day: date, now: datetime) -> FlowRecord:
    """Stable made-up numbers that keep Toss' invariants: net buying sums to zero across the four
    classes (so buys total = sells total), and the institution total = the sum of its seven kinds.
    Each buy is a base share plus the absolute net, so no sell ever goes negative."""
    rng = random.Random(f"{market}:{interval}:{day.isoformat()}")
    side = _DAY_VALUE[market] * _DAYS_PER[interval]
    nets = {"individual": int(rng.uniform(-0.04, 0.04) * side), "foreigner": int(rng.uniform(-0.05, 0.05) * side),
            "institution": int(rng.uniform(-0.03, 0.03) * side)}
    nets["other_corporation"] = -sum(nets.values())
    # institution: spread its net over the seven kinds, the rounding remainder on the last
    raw = [rng.uniform(-1, 1) for _ in INSTITUTION_KINDS]
    total = sum(abs(x) for x in raw) or 1.0
    kind_nets = [int(nets["institution"] * abs(x) / total) for x in raw]
    kind_nets[-1] += nets["institution"] - sum(kind_nets)
    weights = [rng.uniform(0.2, 1.0) for _ in INSTITUTION_KINDS]
    base = side * _SHARE["institution"]
    breakdown = {}
    for kind, w, net in zip(INSTITUTION_KINDS, weights, kind_nets, strict=True):
        buy = int(base * w / sum(weights)) + abs(net)
        breakdown[kind] = (buy, buy - net)
    investors = {}
    for name in INVESTORS:
        if name == "institution":
            investors[name] = (sum(b for b, _ in breakdown.values()), sum(s for _, s in breakdown.values()))
        else:
            buy = int(side * _SHARE[name]) + abs(nets[name])
            investors[name] = (buy, buy - nets[name])
    today = now.astimezone(_KST).date()
    updated = now if day == today else datetime.combine(day, datetime.min.time(), _KST) + timedelta(hours=18, minutes=10)
    return FlowRecord(date=day.isoformat(), updated_at=updated.astimezone(_KST).isoformat(timespec="seconds"),
                      investors=investors, institution_breakdown=breakdown)


# A handful of listings so the name search works without Toss keys (dev, tests, and the
# server until TOSS_* is set). Shape matches GET /api/v1/stocks/all.
SAMPLE_LISTINGS = [
    Listing("NVDA", "엔비디아", "NASDAQ", "STOCK"),
    Listing("AAPL", "애플", "NASDAQ", "STOCK"),
    Listing("MSFT", "마이크로소프트", "NASDAQ", "STOCK"),
    Listing("TSLA", "테슬라", "NASDAQ", "STOCK"),
    Listing("PLTR", "팔란티어", "NASDAQ", "STOCK"),
    Listing("TQQQ", "프로셰어즈 울트라프로 QQQ", "NASDAQ", "ETF"),
    Listing("SOXL", "디렉시온 반도체 불 3X", "AMEX", "ETF"),
    Listing("NVDL", "그래닛셰어즈 엔비디아 2X 롱", "NASDAQ", "ETF"),
    Listing("TSLL", "디렉시온 테슬라 불 2X", "NASDAQ", "ETF"),
    Listing("BRK.B", "버크셔 해서웨이 B", "NYSE", "STOCK"),
    Listing("005930", "삼성전자", "KOSPI", "STOCK"),
    Listing("000660", "SK하이닉스", "KOSPI", "STOCK"),
    Listing("035420", "NAVER", "KOSPI", "STOCK"),
    Listing("247540", "에코프로비엠", "KOSDAQ", "STOCK"),
    Listing("196170", "알테오젠", "KOSDAQ", "STOCK"),
    Listing("122630", "KODEX 레버리지", "KOSPI", "ETF"),
    Listing("233740", "KODEX 코스닥150레버리지", "KOSPI", "ETF"),
    Listing("252670", "KODEX 200선물인버스2X", "KOSPI", "ETF"),
]


class MockBroker(Broker):
    """Fixed sample book and listing for development."""

    name = "mock"

    async def holdings(self) -> list[Holding]:
        return [
            Holding("NVDA", "NVDA", "NVIDIA", "US", "USD", 12, 168.4, 181.2),
            Holding("005930", "005930.KS", "삼성전자", "KR", "KRW", 40, 71200, 74800),
        ]

    async def list_stocks(self, market: str) -> list[Listing]:
        return [listing for listing in SAMPLE_LISTINGS if listing.market == market]

    async def fills(self, start: date, end: date) -> list[Fill]:
        """Sample executions dated relative to today, so the review page always has rows."""
        today = date.today()

        def at(days_ago: int, hhmm: str) -> str:
            return f"{(today - timedelta(days=days_ago)).isoformat()}T{hhmm}:00+09:00"

        sample = [
            Fill("m-1", "NVDA", "BUY", 12, 168.4, 0.5, "USD", at(40, "23:41")),
            Fill("m-2", "005930", "BUY", 40, 71200, 1068, "KRW", at(35, "09:12")),
            Fill("m-3", "NVDA", "SELL", 5, 182.0, 0.4, "USD", at(12, "00:15")),
            Fill("m-4", "005930", "SELL", 10, 74800, 2805, "KRW", at(5, "14:02")),
        ]
        return [f for f in sample if start <= date.fromisoformat(f.filled_at[:10]) <= end]

    async def investor_flow(self, market: str, interval: str, count: int,
                            until: date | None = None) -> tuple[list[FlowRecord], str | None]:
        """Sample records ending today (KST), newest first, with Toss' paging shape."""
        now = datetime.now(UTC)
        day = until or now.astimezone(_KST).date()
        if interval == "1d":
            while day.weekday() >= 5:
                day -= timedelta(days=1)
        elif interval == "1mo":
            day = day.replace(day=1)
        elif interval == "1y":
            day = day.replace(month=1, day=1)
        out = []
        for _ in range(min(max(count, 1), 100)):
            out.append(_sample_record(market, interval, day, now))
            day = _step_back(day, interval)
        return out, day.isoformat()

    async def index_prices(self, symbols: list[str]) -> list[IndexPrice]:
        levels = {"KOSPI": 2812.45, "KOSDAQ": 845.12}
        at = datetime.now(_KST).isoformat(timespec="seconds")
        return [IndexPrice(s, levels[s], at) for s in symbols if s in levels]

