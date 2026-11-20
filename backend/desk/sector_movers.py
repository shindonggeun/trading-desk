"""Which stocks moved a sector the most today - the "culprits" of a big day (#680).

"Philadelphia semis −3.4%: AMD −10.0% did the most of it." For each sector of ``data/sector_weights.json``
(SOX, the Nasdaq 100's top names, the Korean semis) the move of each member since the last close is
weighed by its share of the sector:

    contribution = weight / (sum of the listed weights) x return

so the listed members' contributions add up to about the sector's move (the index's own move is what
the sentence says; the members listed are its largest, so the sum falls a little short of it).

- ``movers`` (pure): the sector's move, every member's return and contribution, the three that pulled
  most down and up, and, when the sector moved ``BIG_PCT`` or more, the sentence naming the three that
  pulled that way ("급락 주범" / "급등 주역"), each with why if the desk knows it (``causes``: an earnings
  report that day before the open, or the day before after the close - else "원인 미확인").
- ``alerts``: a big day as a ``market_alerts`` alert (kind ``sector_mover``, market = the sector key),
  once a session per sector and direction; tone ``warning`` for a slump, ``positive`` for a jump.

The weights are approximate, written by hand from the indices' methods with their date (``as_of``):
they move with prices and the quarterly rebalances, so they are revisited each quarter.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .futures_flow import KST

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
WEIGHTS_FILE = Path(__file__).parent / "data" / "sector_weights.json"
KIND = "sector_mover"
BIG_PCT = 2.0                      # the sector's move (%) that makes a big day
STRONG_PCT = 3.0                   # and a strong one
TOP_N = 3
BARS = 5
PARALLEL = 8                       # bar reads at once (desk.api BATCH_PARALLEL_BARS)
NO_CAUSE = "원인 미확인"


def load(path: Path = WEIGHTS_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


TABLE = load()
SECTORS: dict[str, dict] = TABLE["sectors"]


def market_tz(sector: dict) -> ZoneInfo:
    return KST if sector.get("market") == "KR" else ET


def market_date(at: str, tz: ZoneInfo) -> date:
    """A daily bar's market date. Yahoo stamps it at the exchange's midnight, the broker (Toss) at UTC
    midnight of the date - read in New York that is the evening before: half a day on lands both on it."""
    return (datetime.fromisoformat(at).astimezone(tz) + timedelta(hours=12)).date()


def change(bars: list[dict], day: date | None, tz: ZoneInfo) -> tuple[date, float] | None:
    """(the bar's market date, its return over the bar before) for the last bar - or the bar of ``day``."""
    rows = [b for b in bars or [] if b.get("close")]
    if day is not None:
        rows = [b for b in rows if market_date(b["at"], tz) <= day]
    if len(rows) < 2 or not rows[-2]["close"]:
        return None
    return market_date(rows[-1]["at"], tz), rows[-1]["close"] / rows[-2]["close"] - 1


def _pct(x: float, digits: int = 1) -> str:
    return f"{x:+.{digits}f}%".replace("-", "−")


def movers(key: str, bars: dict[str, list[dict]], causes: dict[str, str] | None = None,
           name_of=None, day: date | None = None) -> dict:
    """{sector, name, label, market, date, move_pct, index_pct, members[], down[], up[], big, sentence,
    weights_as_of} - ``bars`` maps the index and each member to its daily bars (oldest first); the date is the
    index's last bar's (or ``day``'s) market date, and a member whose last bar is another day is left out."""
    sector = SECTORS[key]
    tz = market_tz(sector)
    idx = change(bars.get(sector["index"]) or [], day, tz)
    when = idx[0] if idx else None
    total = sum(sector["members"].values())
    rows = []
    for t, w in sector["members"].items():
        got = change(bars.get(t) or [], day if day is not None else when, tz)
        if got is None or (when is not None and got[0] != when):
            continue
        ret = got[1]
        rows.append({"ticker": t, "name": (name_of(t) if name_of else None) or t, "weight": round(w / total * 100, 2),
                     "return_pct": round(ret * 100, 2), "contribution_pp": round(w / total * ret * 100, 3)})
    summed = sum(r["contribution_pp"] for r in rows)
    move = idx[1] * 100 if idx else summed
    by = sorted(rows, key=lambda r: r["contribution_pp"])
    down = [r for r in by if r["contribution_pp"] < 0][:TOP_N]
    up = [r for r in reversed(by) if r["contribution_pp"] > 0][:TOP_N]
    down = [{**r, "cause": (causes or {}).get(r["ticker"]) or NO_CAUSE} for r in down]
    up = [{**r, "cause": (causes or {}).get(r["ticker"]) or NO_CAUSE} for r in up]
    big = abs(move) >= BIG_PCT and bool(down if move < 0 else up)       # someone to name
    out = {"sector": key, "name": sector["name"], "label": sector["label"], "market": sector["market"],
           "index": sector["index"], "date": when.isoformat() if when else None, "move_pct": round(move, 2),
           "index_pct": round(idx[1] * 100, 2) if idx else None, "members_pct": round(summed, 2),
           "members": by, "down": down, "up": up, "big": big, "sentence": None,
           "weights_as_of": TABLE.get("as_of"), "method": sector.get("method")}
    if big:
        out["sentence"] = sentence(out)
    return out


def sentence(m: dict) -> str:
    """e.g. "미국 반도체 급락(−3.4%) 주범: AMD −10.0%(기여 −0.86%p · 전날 실적 발표) · MU −6.0%(기여 −0.26%p · 원인 미확인)"."""
    falling = m["move_pct"] < 0
    who = m["down"] if falling else m["up"]
    head = f"{m['label']} {'급락' if falling else '급등'}({_pct(m['move_pct'])}) {'주범' if falling else '주역'}"
    parts = [f"{r['name']} {_pct(r['return_pct'])}(기여 {_pct(r['contribution_pp'], 2)}p · {r['cause']})" for r in who]
    return f"{head}: " + " · ".join(parts) if parts else head


def causes_from_earnings(rows_by_day: dict[str, list[dict]], tickers: set[str], day: date) -> dict[str, str]:
    """Why a member moved, if the desk knows it: an earnings report that moved this session - the day
    before after the close, or that day before the open (``when`` of desk.earnings; "unknown" counts either
    way). A report after this session's close has not moved it yet."""
    out = {}
    for d, label, whens in ((day - timedelta(days=1), "전날 실적 발표", ("after", "unknown")),
                            (day, "실적 발표", ("pre", "unknown"))):
        for r in rows_by_day.get(d.isoformat()) or []:
            if r.get("ticker") in tickers and (r.get("when") or "unknown") in whens:
                out[r["ticker"]] = label
    return out


def tickers_of(key: str) -> list[str]:
    s = SECTORS[key]
    return [s["index"], *s["members"]]


async def read(key: str, prices, causes=None, name_of=None, day: date | None = None) -> dict:
    """``movers`` on desk.prices' daily bars (each ticker on its own; one failing is left out).
    ``causes(tickers, day)`` -> {ticker: why}."""
    gate = asyncio.Semaphore(PARALLEL)

    async def one(t: str):
        try:
            async with gate:
                return t, (await prices.candles(t, "1d", BARS)).get("bars") or []
        except Exception as exc:  # noqa: BLE001 - that ticker only
            log.info("sector movers %s: no bars for %s: %s", key, t, type(exc).__name__)
            return t, []

    bars = dict(await asyncio.gather(*(one(t) for t in tickers_of(key))))
    first = movers(key, bars, None, name_of, day)
    if causes is None or not first["date"]:
        return first
    try:
        why = await asyncio.to_thread(causes, set(SECTORS[key]["members"]), date.fromisoformat(first["date"]))
    except Exception as exc:  # noqa: BLE001 - no causes: the numbers still stand
        log.info("sector movers %s: no causes: %s", key, type(exc).__name__)
        why = {}
    return movers(key, bars, why, name_of, day)


def alerts(m: dict, now: datetime | None = None) -> list[dict]:
    """A big day of ``m`` as a desk.market_alerts alert (the minute it was seen), or none."""
    if not m.get("big") or not m.get("sentence"):
        return []
    now = now or datetime.now(UTC)
    return [{"kind": KIND, "rule": rule_of(m["move_pct"]), "market": m["sector"], "who": "all",
             "severity": "strong" if abs(m["move_pct"]) >= STRONG_PCT else "caution",
             "t": now.astimezone(KST).replace(second=0, microsecond=0).isoformat(timespec="seconds"),
             "value": m["move_pct"], "prev": None, "sentence": m["sentence"]}]


def session_day(alert: dict) -> str:
    """The sector's own market date of an alert (a US session runs over the KST midnight)."""
    sector = SECTORS.get(alert["market"]) or {}
    return datetime.fromisoformat(alert["t"]).astimezone(market_tz(sector)).date().isoformat()


def text(alert: dict) -> str:
    """The alert's words: the sentence (desk.market_alerts adds the tone's mark and the reading)."""
    if alert.get("sentence"):
        return alert["sentence"]
    sector = SECTORS.get(alert["market"]) or {}
    return f"{sector.get('label', alert['market'])} {'급락' if alert['rule'] == 'sector_drop' else '급등'}" \
           f"({_pct(alert.get('value') or 0)})"


def rule_of(move_pct: float) -> str:
    return "sector_drop" if move_pct < 0 else "sector_jump"


async def big_day(key: str, prices, causes=None, name_of=None, now: datetime | None = None,
                  said: set[tuple[str, str, str]] | frozenset = frozenset()) -> dict | None:
    """The sector's movers when its index moved BIG_PCT or more since the last close today, else None - the
    index alone is read first, so a quiet day costs one request (the loop runs every SCAN_SEC).
    - the index's last bar must be today's (the market's date of ``now``): a holiday the calendar missed,
      or the minutes before the day's first bar, would say the last session's move again;
    - ``said`` holds (sector, date, rule) already alerted: that side of the day is not read again."""
    sector = SECTORS[key]
    tz = market_tz(sector)
    try:
        got = change((await prices.candles(sector["index"], "1d", BARS)).get("bars") or [], None, tz)
    except Exception as exc:  # noqa: BLE001 - no index: nothing to say this pass
        log.info("sector movers %s: no index bars: %s", key, type(exc).__name__)
        return None
    if got is None or abs(got[1] * 100) < BIG_PCT:
        return None
    if got[0] != (now or datetime.now(UTC)).astimezone(tz).date():
        return None
    if (key, got[0].isoformat(), rule_of(got[1])) in said:
        return None
    m = await read(key, prices, causes, name_of, got[0])
    return m if m["big"] else None


SCAN_SEC = 300
