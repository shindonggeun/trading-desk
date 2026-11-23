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

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import httpx

from . import net

log = logging.getLogger(__name__)

KOFIA_URL = "https://freesis.kofia.or.kr/meta/getMetaDataList.do"
FUNDS = "STATSCU0100000060BO"
CREDIT = "STATSCU0100000070BO"
TIMEOUT_SEC = 20.0
HISTORY_DAYS = 400            # calendar days read: a year of business days for the percentile
HOT_PCT = 80                  # percentile of the margin/deposit ratio at or above: overheated
COLD_PCT = 20
FORCED_ALERT_PCT = 2.0        # forced sales at or above this share of unpaid purchases: stress


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


@dataclass
class CreditCache:
    series: list[dict] = field(default_factory=list)
    refreshed_at: str | None = None
    error: str | None = None

    async def refresh(self, client: httpx.AsyncClient | None = None, today: date | None = None) -> bool:
        own = client is None
        client = client or net.async_client(timeout=TIMEOUT_SEC)
        today = today or datetime.now(UTC).date()
        start = today - timedelta(days=HISTORY_DAYS)
        try:
            funds = await fetch_table(client, FUNDS, start, today)
            credit = await fetch_table(client, CREDIT, start, today)
            series = merge(funds, credit)
            if not series:
                raise ValueError("KOFIA tables had no common days")
        except Exception as exc:  # noqa: BLE001 - keep the last series
            self.error = type(exc).__name__
            log.info("KOFIA credit refresh failed: %s", exc)
            return False
        finally:
            if own:
                await client.aclose()
        self.series, self.error = series, None
        self.refreshed_at = datetime.now(UTC).isoformat(timespec="seconds")
        return True

    def view(self, days: int = 90) -> dict:
        """GET /api/market/credit. One snapshot of the series: a refresh swapping it mid-way cannot mix two."""
        refreshed_at, error, series = self.refreshed_at, self.error, self.series
        cut = (date.fromisoformat(series[-1]["date"]) - timedelta(days=days)).isoformat() if series else ""
        rows = [{"date": r["date"], "credit_trillion": round(r["credit"] / 1e6, 2),
                 "deposits_trillion": round(r["deposits"] / 1e6, 2), "ratio": r["ratio"],
                 "forced_sale_eok": round((r.get("forced_sale") or 0) / 100, 1),
                 "forced_sale_pct": r.get("forced_sale_pct")} for r in series if r["date"] >= cut]
        return {"summary": summarise(series), "series": rows, "source": "kofia",
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
