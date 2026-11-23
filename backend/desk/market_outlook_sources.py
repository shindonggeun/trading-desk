"""Curated public outlook notes and the cached EDGAR context for #903."""

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta

from desk.funds13f import Funds

DUQUESNE = "duquesne"
VIEWS_STALE_DAYS = 90
FILINGS_CHECK_MAX_AGE = timedelta(days=2)
VIEWS = (
    {
        "id": "druckenmiller-tomorrowx-2026",
        "expert": "스탠리 드라켄밀러",
        "organization": "Duquesne Family Office",
        "source": "iConnections · TomorrowX Summit",
        "source_url": "https://www.youtube.com/watch?v=6r3D-CMJkuI",
        "date_source_url": "https://tomorrowxsummit.com/",
        "spoken_on": "2026-11-17",
        "published_on": None,
        "stance": "cautious",
        "horizon": "향후 약 12개월",
        "summary": "향후 12개월의 방향을 가늠하기 어려워 적극적으로 노출을 늘리기보다 관망한다는 관점입니다.",
        "basis": "전망의 불확실성이 큰 상황을 근거로 제시했습니다.",
        "change": None,
        "positions": [{"asset": "구리", "side": "short", "status": "unverified", "as_of": None}],
    },
    {
        "id": "snider-us-equities-2027",
        "expert": "벤 스나이더",
        "organization": "Goldman Sachs Research",
        "source": "Goldman Sachs · Insights",
        "source_url": "https://www.goldmansachs.com/insights/articles/"
                      "us-stocks-predicted-to-extend-historic-rally-as-gains-moderate",
        "date_source_url": None,
        "spoken_on": None,
        "published_on": "2026-11-19",
        "stance": "bullish",
        "horizon": "2027년 말까지",
        "summary": "기업 이익이 미국 주식의 상승을 뒷받침하되 상승의 속도는 둔해질 수 있다는 관점입니다.",
        "basis": "이익 증가를 긍정 요인으로 보고, 이후 이익 전망 하향과 대형주 집중을 위험으로 짚었습니다.",
        "change": None,
        "positions": [],
    },
)


def read_views(today: date, entries=VIEWS) -> list[dict]:
    """Keep distinct speakers and sources; use a separate publication and speech date."""
    seen = set()
    out = []
    for item in entries:
        dates = [date.fromisoformat(item[key]) for key in ("spoken_on", "published_on") if item[key]]
        if not item["source_url"] or (dates and max(dates) > today):
            continue
        key = (item["expert"], item["source_url"])
        if key in seen:
            continue
        seen.add(key)
        age = (today - min(dates)).days if dates else None
        out.append({
            **deepcopy(item),
            "market": "us",
            "age_days": age,
            "status": "unverified" if age is None else "stale" if age > VIEWS_STALE_DAYS else "available",
        })
    return sorted(out, key=lambda item: item["published_on"] or item["spoken_on"] or "", reverse=True)


def read_duquesne(funds: Funds, now: datetime | None = None) -> dict:
    """Present the existing background EDGAR snapshot without starting a request to SEC."""
    snapshot = funds.latest_snapshot(DUQUESNE)
    filing = snapshot["filing"]
    detail = snapshot["detail"]
    checked = snapshot["checked_at"]
    failed = snapshot["failed"]
    now = now if now is not None else datetime.now(UTC)
    overdue = checked is not None and now - datetime.fromisoformat(checked) > FILINGS_CHECK_MAX_AGE
    if not filing:
        return {
            "source": "SEC EDGAR", "checked_at": checked,
            "status": "error" if failed else "missing",
            "reason": "refresh_failed" if failed else "no_filing", "items": [],
        }
    return {
        "source": "SEC EDGAR",
        "checked_at": checked,
        "status": "stale" if failed or overdue else "available",
        "reason": "refresh_incomplete" if failed else "check_overdue" if overdue else None,
        "items": [{
            **filing,
            "publication_lag_days": (
                date.fromisoformat(filing["filed_at"]) - date.fromisoformat(filing["period_of_report"])
            ).days,
            "holdings_status": "available" if detail is not None else "missing",
            "previous_quarter": detail["prev_quarter"] if detail is not None else None,
            "holdings": detail["holdings"] if detail is not None else [],
        }],
    }
