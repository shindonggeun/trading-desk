"""Korean margin debt and investor cash (#155) — 빚투 · 예탁금, like korulive's "빚투" page.

Keyless: the Korea Financial Investment Association statistics portal (freesis.kofia.or.kr) answers a
JSON POST per table. Two daily tables, amounts in million won, posted the next business day:

- STATSCU0100000060BO 증시자금 추이: TMPV2 투자자예탁금 (investor deposits), TMPV3 장내파생상품 거래예수금,
  TMPV4 RP 매도잔고, TMPV5 위탁매매 미수금 (unpaid purchases), TMPV6 실제 반대매매금액 (forced sales),
  TMPV7 미수금 대비 반대매매 비중 (%). KOFIA sends million won; the desk API (/api/market/credit)
  divides by 100 to show forced sales in 억원 (100 million won).
- STATSCU0100000070BO 신용공여 잔고 추이: TMPV2 신용거래융자 합계 (margin loans) = TMPV3 유가증권(KOSPI)
  + TMPV4 코스닥, TMPV5..7 신용거래대주, TMPV8 청약자금대출, TMPV9 예탁증권담보융자.

From them: margin loans against investor deposits (빚투 비율), its 1-week and 1-month change and where
it sits in the last year (percentile), and forced sales. A background loop refreshes ``CACHE``; the
API and runs read it. Read as crowd positioning, not a timing signal.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from time import monotonic

import httpx

from . import net, shared_cache

log = logging.getLogger(__name__)

KOFIA_URL = "https://freesis.kofia.or.kr/meta/getMetaDataList.do"
FUNDS = "STATSCU0100000060BO"
CREDIT = "STATSCU0100000070BO"
TIMEOUT_SEC = 20.0
HISTORY_DAYS = 400            # calendar days read: a year of business days for the percentile
HOT_PCT = 80                  # percentile of the margin/deposit ratio at or above: overheated
COLD_PCT = 20
FORCED_ALERT_PCT = 2.0        # forced sales at or above this share of unpaid purchases: stress
SHARED_NS = "kofia_credit"
SHARED_KEY = "series"
SHARED_TTL = 7 * 86400        # a confirmed observation can be read through a long market closure
LOCAL_TTL = 3.0
_KST = timezone(timedelta(hours=9))
_REQUIRED = ("credit", "deposits", "ratio")
_OPTIONAL = ("credit_kospi", "credit_kosdaq", "forced_sale", "forced_sale_pct")
_CHECKED_RANK_WIDTH = 10**18


async def fetch_table(client: httpx.AsyncClient, obj: str, start: date, end: date) -> list[dict]:
    body = {"dmSearch": {"tmpV40": "1000000", "tmpV41": "1", "tmpV1": "D",
                         "tmpV45": start.strftime("%Y%m%d"), "tmpV46": end.strftime("%Y%m%d"), "OBJ_NM": obj}}
    response = await client.post(KOFIA_URL, json=body, headers={
        "User-Agent": "Mozilla/5.0 (trading-desk personal research)", "Referer": "https://freesis.kofia.or.kr/"})
    response.raise_for_status()
    rows = (response.json() or {}).get("ds1")
    if not isinstance(rows, list):
        raise TypeError(f"KOFIA {obj}: no rows")
    return rows


def _num(value) -> float | None:
    """KOFIA numbers come as numbers or strings ("88533870", "28,771,509", "-", "")."""
    try:
        v = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def merge(funds: list[dict], credit: list[dict]) -> list[dict]:
    """Days present in both tables, oldest first. Amounts stay in million won."""
    by_day: dict[str, dict] = {}
    for r in funds:
        day = str(r.get("TMPV1") or "")
        if len(day) == 8:
            by_day[day] = {"deposits": _num(r.get("TMPV2")), "unpaid": _num(r.get("TMPV5")),
                           "forced_sale": _num(r.get("TMPV6")), "forced_sale_pct": _num(r.get("TMPV7"))}
    out = []
    for r in credit:
        day = str(r.get("TMPV1") or "")
        if day not in by_day:
            continue
        row = by_day[day]
        loans, deposits = _num(r.get("TMPV2")), row["deposits"]
        if not loans or not deposits:
            continue
        out.append({"date": f"{day[:4]}-{day[4:6]}-{day[6:]}", "credit": loans, "credit_kospi": _num(r.get("TMPV3")),
                    "credit_kosdaq": _num(r.get("TMPV4")), **row, "ratio": round(loans / deposits * 100, 2)})
    return sorted(out, key=lambda r: r["date"])


def _back(series: list[dict], days: int) -> dict | None:
    """The row ``days`` calendar days before the last one (or the nearest earlier)."""
    if not series:
        return None
    cut = (date.fromisoformat(series[-1]["date"]) - timedelta(days=days)).isoformat()
    earlier = [r for r in series if r["date"] <= cut]
    return earlier[-1] if earlier else None


def summarise(series: list[dict]) -> dict | None:
    if not series:
        return None
    last = series[-1]
    year = [r["ratio"] for r in series if r["date"] >= (date.fromisoformat(last["date"]) - timedelta(days=365)).isoformat()]
    pct = round(sum(1 for v in year if v <= last["ratio"]) / len(year) * 100)

    def change(key: str, days: int) -> float | None:
        before = _back(series, days)
        return round((last[key] / before[key] - 1) * 100, 1) if before and before.get(key) else None

    def ratio_change(days: int) -> float | None:
        before = _back(series, days)
        return round(last["ratio"] - before["ratio"], 2) if before else None

    level = "hot" if pct >= HOT_PCT else "cold" if pct <= COLD_PCT else "normal"
    stress = (last.get("forced_sale_pct") or 0) >= FORCED_ALERT_PCT
    return {
        "date": last["date"],
        "credit_trillion": round(last["credit"] / 1e6, 2),
        "credit_kospi_trillion": round((last["credit_kospi"] or 0) / 1e6, 2),
        "credit_kosdaq_trillion": round((last["credit_kosdaq"] or 0) / 1e6, 2),
        "deposits_trillion": round(last["deposits"] / 1e6, 2),
        "ratio": last["ratio"],
        "ratio_change_1w": ratio_change(7), "ratio_change_1m": ratio_change(30),
        "credit_change_1w_pct": change("credit", 7), "credit_change_1m_pct": change("credit", 30),
        "deposits_change_1w_pct": change("deposits", 7), "deposits_change_1m_pct": change("deposits", 30),
        "ratio_percentile_1y": pct, "level": level,
        "forced_sale_eok": round((last.get("forced_sale") or 0) / 100, 1),
        "forced_sale_pct": last.get("forced_sale_pct"), "stress": stress,
        "verdict": verdict(level, pct, stress, last),
    }


def verdict(level: str, pct: int, stress: bool, last: dict) -> str:
    top = "1년 최고 수준" if pct >= 100 else f"1년 중 상위 {100 - pct}%"
    low = "1년 최저 수준" if pct <= 1 else f"1년 중 하위 {pct}%"
    head = {"hot": f"빚투 과열 구간({top})", "cold": f"빚투 위축 구간({low})",
            "normal": f"빚투 보통 수준(1년 백분위 {pct})"}[level]
    tail = f" · 반대매매 비중 {last['forced_sale_pct']}% — 투매 압력" if stress else ""
    return head + tail


def _now() -> datetime:
    return datetime.now(UTC)


def _observation_rank(series: list[dict], checked: datetime) -> int:
    source_day = date.fromisoformat(series[-1]["date"])
    span = (source_day - date.fromisoformat(series[0]["date"])).days
    # Within the same statistical date, retain the history required for the changes and annual percentile.
    coverage = (span > 0) + (span >= 7) + (span >= 30) + (span >= 365)
    elapsed_us = (checked - datetime.min.replace(tzinfo=UTC)) // timedelta(microseconds=1)
    return (source_day.toordinal() * 5 + coverage) * _CHECKED_RANK_WIDTH + elapsed_us


def _public_number(value: object) -> bool:
    # Million won and percentage points. The bound also keeps later arithmetic finite.
    return type(value) in (int, float) and -1e30 <= value <= 1e30


def _public_rows(series: object, max_day: date) -> list[dict] | None:
    if not isinstance(series, list) or not 0 < len(series) <= HISTORY_DAYS + 1:
        return None
    rows = []
    # A date safely far enough from date.min for view() and summarise() to subtract a year.
    prior = date(1900, 1, 1)
    for row in series:
        if not isinstance(row, dict) or not isinstance(label := row.get("date"), str):
            return None
        try:
            day = date.fromisoformat(label)
        except ValueError:
            return None
        if not (prior < day <= max_day and label == day.isoformat()):
            return None
        if any(not _public_number(row.get(key)) for key in _REQUIRED):
            return None
        if row["credit"] < 1 or row["deposits"] < 1:
            return None
        if any(row.get(key) is not None and not _public_number(row[key]) for key in _OPTIONAL):
            return None
        rows.append({"date": label, **{key: row.get(key) for key in (*_REQUIRED, *_OPTIONAL)}})
        prior = day
    return rows


def _confirmation(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        checked = datetime.fromisoformat(value)
    except ValueError:
        return None
    return checked if checked.utcoffset() == timedelta(0) else None


@dataclass(frozen=True)
class _Observation:
    series: list[dict]
    checked: datetime

    @property
    def rank(self) -> int:
        return _observation_rank(self.series, self.checked)


def _open_observation(value: object, now: datetime) -> _Observation | None:
    if not isinstance(value, dict) or (checked := _confirmation(value.get("refreshed_at"))) is None:
        return None
    age = (now - checked).total_seconds()
    if not -shared_cache.SKEW_SEC <= age < SHARED_TTL:
        return None
    rows = _public_rows(value.get("series"), checked.astimezone(_KST).date())
    if rows is None or value.get("source_date") != rows[-1]["date"]:
        return None
    return _Observation(rows, checked)


@dataclass
class CreditCache:
    series: list[dict] = field(default_factory=list)
    refreshed_at: str | None = None
    error: str | None = None
    _seen: _Observation | None = field(default=None, init=False, repr=False, compare=False)
    _next_read: float = field(default=0.0, init=False, repr=False, compare=False)
    _read_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False, compare=False)

    async def refresh(self, client: httpx.AsyncClient | None = None, today: date | None = None) -> bool:
        own = client is None
        client = client or net.async_client(timeout=TIMEOUT_SEC)
        today = today or _now().date()
        start = today - timedelta(days=HISTORY_DAYS)
        try:
            funds = await fetch_table(client, FUNDS, start, today)
            credit = await fetch_table(client, CREDIT, start, today)
            series = merge(funds, credit)
            if not series:
                raise ValueError("KOFIA tables had no common days")
            checked = _now()
            source_day = date.fromisoformat(series[-1]["date"])
            if source_day > checked.astimezone(_KST).date():
                raise ValueError("KOFIA tables had a future statistical date")
            rank = _observation_rank(series, checked)
        except Exception as exc:  # noqa: BLE001 - keep the last series
            self.error = type(exc).__name__
            log.info("KOFIA credit refresh failed: %s", exc)
            return False
        finally:
            if own:
                await client.aclose()
        current_rank = self._local_rank()
        if rank >= current_rank:
            self.series, self.refreshed_at, self.error = series, checked.isoformat(), None
        elif (
            ((previous := _confirmation(self.refreshed_at)) is None or checked >= previous)
            and rank // _CHECKED_RANK_WIDTH < current_rank // _CHECKED_RANK_WIDTH
        ):
            # A newly checked data gap should retry promptly; an earlier in-flight completion is harmless.
            self.error = "IncompleteHistory"
            return False
        return True

    def _local_rank(self) -> int:
        if not self.series:
            return 0
        checked = _confirmation(self.refreshed_at) or datetime.min.replace(tzinfo=UTC)
        return _observation_rank(self.series, checked)

    async def refresh_and_publish(self, client: httpx.AsyncClient | None = None, today: date | None = None) -> bool:
        previous = self.refreshed_at
        if not await self.refresh(client, today):
            return False
        if not shared_cache.enabled() or self.refreshed_at == previous:
            return True
        checked = _confirmation(self.refreshed_at)
        rows = _public_rows(self.series, checked.astimezone(_KST).date()) if checked is not None else None
        if rows is not None:
            value = {"series": rows, "source_date": rows[-1]["date"], "refreshed_at": self.refreshed_at}

            def rank(candidate: object) -> int | None:
                valid = _open_observation(candidate, _now())
                return None if valid is None else valid.rank

            await shared_cache.aput_newer(SHARED_NS, SHARED_KEY, value, SHARED_TTL, rank=rank)
        return True

    async def shared_view(self, days: int = 90) -> dict:
        if self._seen is not None and (_now() - self._seen.checked).total_seconds() >= SHARED_TTL:
            self._seen = None
        if shared_cache.enabled() and monotonic() >= self._next_read:
            async with self._read_lock:
                if monotonic() >= self._next_read:
                    hit = await shared_cache.aget(SHARED_NS, SHARED_KEY)
                    now = _now()
                    candidate = _open_observation(hit[0], now) if hit is not None else None
                    if candidate is not None and (self._seen is None or candidate.rank > self._seen.rank):
                        self._seen = candidate
                    ttl = min(LOCAL_TTL, hit[2]) if candidate is not None else LOCAL_TTL
                    self._next_read = monotonic() + ttl
        seen = self._seen
        if seen is not None and seen.rank > self._local_rank():
            return _view(seen.series, seen.checked.isoformat(), None, days)
        return self.view(days)

    def view(self, days: int = 90) -> dict:
        """GET /api/market/credit. One snapshot of the series: a refresh swapping it mid-way cannot mix two."""
        refreshed_at, error, series = self.refreshed_at, self.error, self.series
        return _view(series, refreshed_at, error, days)


def _view(series: list[dict], refreshed_at: str | None, error: str | None, days: int) -> dict:
    source_date = series[-1]["date"] if series else None
    cut = (date.fromisoformat(source_date) - timedelta(days=days)).isoformat() if source_date else ""
    rows = [{"date": r["date"], "credit_trillion": round(r["credit"] / 1e6, 2),
             "deposits_trillion": round(r["deposits"] / 1e6, 2), "ratio": r["ratio"],
             "forced_sale_eok": round((r.get("forced_sale") or 0) / 100, 1),
             "forced_sale_pct": r.get("forced_sale_pct")} for r in series if r["date"] >= cut]
    return {"summary": summarise(series), "series": rows, "source": "kofia", "source_date": source_date,
            "refreshed_at": refreshed_at, "error": error}


CACHE = CreditCache()


def is_korean(ticker: str) -> bool:
    return ticker.upper().endswith((".KS", ".KQ"))


def prompt_note(cache: CreditCache | None = None) -> str | None:
    """The agents' "Korean margin debt" block for a Korean ticker, or None before the first refresh."""
    s = summarise((cache or CACHE).series)
    if not s:
        return None
    lines = [
        ("Korean retail leverage (KOFIA, as of the last posted day; desk-computed). Margin loans against investor "
         "deposits show how leveraged the crowd is: an overheated reading means forced selling can deepen a drop, "
         "a cold one means less fuel either way. Crowd positioning, not a timing signal."),
        (f"- {s['date']}: margin loans {s['credit_trillion']}tn KRW (KOSPI {s['credit_kospi_trillion']}, KOSDAQ "
         f"{s['credit_kosdaq_trillion']}), investor deposits {s['deposits_trillion']}tn, ratio {s['ratio']}% "
         f"({s['ratio_percentile_1y']}th percentile of the past year - {s['level']})"),
    ]
    moves = (s["ratio_change_1w"], s["ratio_change_1m"], s["credit_change_1m_pct"], s["deposits_change_1m_pct"])
    if None not in moves:
        lines.append(f"- Change: ratio {moves[0]:+} pt over 1 week, {moves[1]:+} pt over 1 month; margin loans "
                     f"{moves[2]:+}% and deposits {moves[3]:+}% over 1 month")
    share = f", {s['forced_sale_pct']}% of unpaid purchases" if s["forced_sale_pct"] is not None else ""
    lines.append(f"- Forced sales {s['forced_sale_eok'] / 10:g}bn KRW{share}" + (" - STRESS" if s["stress"] else ""))
    return "\n".join(lines)
