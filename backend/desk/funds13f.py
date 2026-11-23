"""Well-known funds' quarterly 13F holdings from SEC EDGAR, as new · increased · reduced · sold out (#483).

An institutional manager with over $100M in US equities files a 13F within 45 days of each quarter's
end: every position's issuer, CUSIP, shares (or a bond's principal) and value on the last day of the
quarter. For each fund in ``FUNDS`` (CIKs checked on EDGAR, 2026-11-17) this reads:

- ``data.sec.gov/submissions/CIK##########.json``: the 13F filings per period of report.
- A period's table: the latest original 13F-HR (or a 13F-HR/A whose cover says RESTATEMENT), with
  every later NEW HOLDINGS amendment added on top - Berkshire has revealed positions kept confidential
  that way. A period with only a 13F-NT ("another manager reports for us"): the cover page names that
  manager, whose 13F-HR for the same period is read instead (``reported_by``). Pershing Square has filed
  13F-NT for 2026Q2-Q3 naming its parent by CIK. A cover that names the manager only by name and 13F file
  number (Oaktree 2026Q3 → Brookfield Corp, #599) is followed through the fund's ``reporters`` (file
  number → CIK, checked on EDGAR); when that manager's table mixes many advisers' positions, only the
  rows tagged with the fund's own sequence number (its cover's included managers) are read - the fund's
  own 13F file number only, never a sibling adviser's: for Oaktree, Oaktree Capital Management (028-05050,
  Brookfield's sequence 7) and not Oaktree Fund Advisors (028-21340, sequence 8), the same scope as
  Oaktree's own 13F-HRs up to 2026Q2 (#607). ``reported_by.scope`` says so on the page. Such a table is
  cached under ``SHARE_RULE``: a change to the rule reads it again.
- The information table XML of each filing (``www.sec.gov/Archives/edgar/data/<cik>/<accession>/``),
  merged per CUSIP and option side. Values are dollars for filings made from 2023-01-03 (SEC's 2023
  amendment), thousands before.

Compared with the quarter before: new · increase · decrease · unchanged · sold; ``change`` is None when
the quarter before could not be read. Weights and the total are over the stock and bond rows: an
option row's value is the underlying's notional (``options_value``). A bond row (``share_type`` PRN)
carries its principal in ``shares``. Tickers come from OpenFIGI's free mapping (CUSIP → ticker) for the
largest and the most changed positions, a bounded number per pass, kept in a cache file - the coverage
grows pass by pass; a CUSIP that does not map keeps its name.

SEC asks for at most ten requests a second and a User-Agent naming the requester with a contact
(``SEC_EDGAR_USER_AGENT``, desk.events_calendar.user_agent, the same as desk.filings - never logged or
returned here); requests here are ``MIN_GAP_SEC`` apart and follow no redirects. With no contact the
Archives answer 403 and the table is reported unavailable, not worked around. Remote identifiers are
checked before they go into a URL or a file name; a document is capped at ``MAX_BYTES``, parsed row by
row and refused if it carries a DTD. One fetch runs at a time: a request that finds another one fetching
is told to come back (it never queues worker threads behind SEC). Tables never change, so each is cached
by accession on disk (the backend's volume, ``CACHE_DIR``) and the latest few in memory.
"""

from __future__ import annotations

import io
import json
import logging
import re
import tempfile
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree

import httpx

from . import net
from .events_calendar import user_agent

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Reporter:
    """A manager that reports a fund's holdings in its own 13F-HR (the fund files a 13F-NT naming it).
    #599: SEC has no JSON lookup from a 13F file number to a CIK and a name search is ambiguous
    (Brookfield has many filers), so the ones a cover names without a CIK are written down here.
    ``own_rows``: its table holds other advisers' positions too - only the fund's own rows are read.
    ``scope``: what those rows are, said with ``reported_by``."""
    file_number: str
    cik: str
    own_rows: bool = False
    scope: str | None = None


@dataclass(frozen=True)
class Fund:
    id: str
    name: str
    manager: str
    cik: str
    reporters: tuple[Reporter, ...] = ()


FUNDS = (
    Fund("berkshire", "버크셔 해서웨이", "워런 버핏", "0001067983"),
    Fund("pershing", "퍼싱스퀘어", "빌 애크먼", "0001336528"),
    Fund("bridgewater", "브리지워터", "레이 달리오", "0001350694"),
    Fund("citadel", "시타델 어드바이저스", "켄 그리핀", "0001423053"),
    # 2026Q3 13F-NT: Brookfield Corp (028-14349, CIK 0001001085, EDGAR 2026-11-18) reports Oaktree's
    # positions - sequence 7 among the eight advisers of its combination report. Sequence 8 (Oaktree Fund
    # Advisors, 028-21340) is left out: Oaktree's own 13F-HRs never included it
    Fund("oaktree", "오크트리 캐피털", "하워드 막스", "0000949509",
         reporters=(Reporter("028-14349", "0001001085", own_rows=True,
                             scope="Oaktree Capital Management 몫만 — Oaktree Fund Advisors 는 빼서 "
                                   "오크트리가 직접 낸 13F 와 같은 범위"),)),
)
BY_ID = {f.id: f for f in FUNDS}


class FundsUnavailable(LookupError):
    """A reason the desk can say as it is (SEC refused, a filing not there yet, a document refused)."""


class NotFiled(FundsUnavailable):
    """No 13F of that fund for that quarter (the API's 404)."""


class FundsBusy(FundsUnavailable):
    """Another request is fetching from SEC right now - come back in a moment."""


SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/"
FIGI_URL = "https://api.openfigi.com/v3/mapping"
ACC_RE = re.compile(r"\d{10}-\d{2}-\d{6}")
CIK_RE = re.compile(r"\d{1,10}")
FILE_NO_RE = re.compile(r"\d{2,3}-\d{1,6}")       # a 13F file number, 028-05050
XML_NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,100}\.xml", re.IGNORECASE)
MIN_GAP_SEC = 0.2            # SEC: at most ten a second - five here
TIMEOUT_SEC = 30.0
MAX_BYTES = 150 * 1024 * 1024
DUE_DAYS = 45                # a 13F is due within 45 days of the quarter's end (the next business day)
WINDOW_DAYS = 14             # around the due date the funds are asked every WINDOW_EVERY_SEC
WINDOW_EVERY_SEC = 3 * 3600
DAILY_SEC = 24 * 3600
RETRY_SEC = 30 * 60          # a failed pass is tried again this soon (not for a reason that will not change)
LASTING = ("운용사를 찾지 못했습니다", "몫을 가려낼 수 없습니다")   # ... such as these: until a new filing
SHARE_RULE = 1               # how a fund's rows are picked from a reporter's table (#607): change it → read again
FAIL_TTL_SEC = 10 * 60       # a document that failed is not asked again before this
KEEP_TABLES = 16             # tables kept in memory (5 funds x the latest two quarters, and a few more)
KEEP_VIEWS = 12
MAP_PER_PASS = 120           # CUSIPs sent to OpenFIGI per refresh pass (the cache keeps growing)
MAP_PER_REQUEST = 20         # ... and per page request (a quarter nobody asked for in a pass)
FIGI_BATCH = 10              # OpenFIGI without a key: ten jobs a request, 25 requests a minute
FIGI_GAP_SEC = 2.5
# #512 a foreign issuer's CINS (a letter first, e.g. Alcon H01301128) has no OpenFIGI CUSIP mapping at all: its
# name is searched among US-listed common shares and depositary receipts instead - kept only when exactly one
# listing has the same name (legal suffixes aside), else no ticker (never a guess). OpenFIGI's search without a
# key allows 5 requests a minute: a few per pass, each CINS searched once
FIGI_SEARCH_URL = "https://api.openfigi.com/v3/search"
SEARCH_PER_PASS = 3
SEARCH_GAP_SEC = 15.0
SEARCH_TYPES = ("Common Stock", "Depositary Receipt")
_SUFFIXES = {"AG", "SA", "NV", "PLC", "LTD", "LIMITED", "INC", "CORP", "CORPORATION", "CO", "SE", "AB", "ASA",
             "OYJ", "SPA", "BV", "HOLDING", "HOLDINGS", "GROUP", "THE", "ADR", "ADS", "SPONSORED", "REG", "ORD",
             "SHS", "NEW"}
DOLLARS_FILED_FROM = date(2023, 1, 3)
HR_FORMS = ("13F-HR", "13F-HR/A")
NT_FORMS = ("13F-NT", "13F-NT/A")
CACHE_DIR = Path.home() / ".tradingagents" / "desk-cache" / "funds13f"   # the backend's volume


# ------------------------------------------------------------------ quarters

def quarter_of(period: date) -> str:
    return f"{period.year}Q{(period.month - 1) // 3 + 1}"


def period_of(quarter: str) -> date:
    m = re.fullmatch(r"(\d{4})Q([1-4])", quarter or "", re.ASCII)
    if not m or int(m.group(1)) < 1990:
        raise ValueError("quarter must look like 2026Q3")
    y, q = int(m.group(1)), int(m.group(2))
    return {1: date(y, 3, 31), 2: date(y, 6, 30), 3: date(y, 9, 30), 4: date(y, 12, 31)}[q]


def previous_quarter(quarter: str) -> str:
    p = period_of(quarter)
    y, q = p.year, (p.month - 1) // 3 + 1
    return f"{y - 1}Q4" if q == 1 else f"{y}Q{q - 1}"


def _next_quarter(quarter: str) -> str:
    p = period_of(quarter)
    y, q = p.year, (p.month - 1) // 3 + 1
    return f"{y + 1}Q1" if q == 4 else f"{y}Q{q + 1}"


def due_date(period: date) -> date:
    """45 days after the quarter's end, moved off a weekend (holidays are not known here)."""
    d = period + timedelta(days=DUE_DAYS)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def last_quarter_end(today: date) -> date:
    """The latest quarter end on or before ``today``."""
    for month, day in ((12, 31), (9, 30), (6, 30), (3, 31)):
        end = date(today.year, month, day)
        if end <= today:
            return end
    return date(today.year - 1, 12, 31)


def next_due(today: date) -> date:
    """The next filing deadline from ``today`` (the latest quarter's, or the one after)."""
    last = last_quarter_end(today)
    d = due_date(last)
    return d if d >= today else due_date(period_of(_next_quarter(quarter_of(last))))


def check_every(today: date) -> int:
    """Seconds between passes: every few hours within WINDOW_DAYS of a due date, else daily."""
    last = last_quarter_end(today)
    dues = (due_date(last), due_date(period_of(_next_quarter(quarter_of(last)))))
    return WINDOW_EVERY_SEC if any(abs((today - d).days) <= WINDOW_DAYS for d in dues) else DAILY_SEC


# ------------------------------------------------------------------ parsing

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el, name: str):
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _text(el, *path: str) -> str | None:
    for name in path:
        el = _child(el, name) if el is not None else None
    return el.text.strip() if el is not None and el.text else None


def _num(text: str | None) -> int:
    try:
        return int(float((text or "0").replace(",", "")))
    except ValueError:
        return 0


def _no_dtd(xml: bytes) -> None:
    """A 13F needs no DTD: a document with one is refused before parsing (no entity games)."""
    head = xml[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise FundsUnavailable("SEC 문서에 DTD 가 있어 읽지 않았습니다")


def root_name(xml: bytes) -> str | None:
    """The document's root element name, read from its first tag only."""
    _no_dtd(xml)
    try:
        for _event, el in ElementTree.iterparse(io.BytesIO(xml), events=("start",)):
            return _local(el.tag)
    except ElementTree.ParseError:
        return None
    return None


def parse_info_table(xml: bytes, dollars: bool = True, managers: set[int] | None = None) -> list[dict]:
    """The information table's rows: issuer, class, CUSIP, option side, shares (or principal, PRN),
    value in dollars. Read row by row (each row cleared): a large fund's table has many thousand rows.
    ``managers``: only the rows whose other-manager numbers ("7", "1,7") include one of these."""
    _no_dtd(xml)
    scale = 1 if dollars else 1000
    rows = []
    for _event, el in ElementTree.iterparse(io.BytesIO(xml), events=("end",)):
        if _local(el.tag) != "infoTable":
            continue
        if managers is not None and not managers & {int(n) for n in re.findall(r"\d+", _text(el, "otherManager") or "")}:
            el.clear()
            continue
        put_call = (_text(el, "putCall") or "").upper() or None
        rows.append({"name": _text(el, "nameOfIssuer") or "", "title": _text(el, "titleOfClass") or "",
                     "cusip": (_text(el, "cusip") or "").upper(), "put_call": put_call,
                     "shares": _num(_text(el, "shrsOrPrnAmt", "sshPrnamt")),
                     "share_type": (_text(el, "shrsOrPrnAmt", "sshPrnamtType") or "SH").upper(),
                     "value": _num(_text(el, "value")) * scale})
        el.clear()
    return rows


def merge(rows: list[dict]) -> list[dict]:
    """One row per CUSIP and option side (a holding split across managers or accounts adds up)."""
    out: dict[tuple, dict] = {}
    for r in rows:
        key = (r["cusip"], r["put_call"])
        if key in out:
            out[key]["shares"] += r["shares"]
            out[key]["value"] += r["value"]
        else:
            out[key] = dict(r)
    return list(out.values())


def _manager(el) -> dict:
    cik, number = _text(el, "cik"), _text(el, "form13FFileNumber")
    return {"cik": f"{int(cik):010d}" if cik and CIK_RE.fullmatch(cik) else None, "name": _text(el, "name"),
            "file_number": number if number and FILE_NO_RE.fullmatch(number) else None}


def cover(xml: bytes) -> dict:
    """A cover page (primary_doc.xml): the amendment type, the filer's own 13F file number, the other
    managers a 13F-NT names (``others``, in order; ``other`` the first) and the managers a combination
    report includes by sequence number (``included``, the numbers its table rows carry)."""
    _no_dtd(xml)
    root = ElementTree.fromstring(xml)
    out: dict = {"amendment_type": None, "other": None, "others": [], "file_number": None, "included": {}}
    listed = set()
    for el in root.iter():
        if _local(el.tag) == "otherManager2":
            seq, m = _text(el, "sequenceNumber"), _child(el, "otherManager")
            if m is not None:
                listed.add(id(m))
                if seq and seq.isdigit():
                    out["included"][int(seq)] = _manager(m)
    for el in root.iter():
        name = _local(el.tag)
        if name == "amendmentType" and el.text:
            out["amendment_type"] = el.text.strip().upper()
        elif name == "coverPage":
            number = _text(el, "form13FFileNumber")
            out["file_number"] = number if number and FILE_NO_RE.fullmatch(number) else None
        elif name in ("otherManager", "otherManagerInfo") and id(el) not in listed:
            m = _manager(el)
            if m["cik"] or m["name"] or m["file_number"]:
                out["others"].append(m)
    if out["others"]:
        out["other"] = {"cik": out["others"][0]["cik"], "name": out["others"][0]["name"]}
    return out


def reporter_of(others: list[dict], reporters: tuple[Reporter, ...]) -> tuple[str, str, bool] | None:
    """The manager a 13F-NT's cover names that reports for the fund: (CIK, name, own rows only). One of
    the fund's known reporters anywhere on the list (Oaktree names Brookfield, then two sub-advisers),
    else the first one named with a CIK; None when neither."""
    for o in others:
        for r in reporters:
            if (o["file_number"] and o["file_number"] == r.file_number) or (o["cik"] and o["cik"] == r.cik):
                return r.cik, o["name"] or r.cik, r.own_rows
    if others and others[0]["cik"]:
        return others[0]["cik"], others[0]["name"] or others[0]["cik"], False
    return None


# ------------------------------------------------------------------ comparing

def compare(current: list[dict], previous: list[dict] | None) -> list[dict]:
    """Holdings by value with weight and the change from the quarter before, sold-out rows at the end.
    Weights are over the stock and bond rows; an option row's weight is None (its value is notional).
    With no quarter before, ``change`` is None."""
    total = sum(r["value"] for r in current if not r["put_call"]) or 1
    prev = {(r["cusip"], r["put_call"]): r for r in previous or []}
    rows = []
    for r in sorted(current, key=lambda r: (r["put_call"] is not None, -r["value"])):
        p = prev.get((r["cusip"], r["put_call"]))
        if previous is None:
            change, diff, prev_shares = None, None, None
        elif p is None:
            change, diff, prev_shares = "new", r["shares"], 0
        else:
            prev_shares = p["shares"]
            diff = r["shares"] - prev_shares
            change = "increase" if diff > 0 else "decrease" if diff < 0 else "unchanged"
        rows.append({**_row(r), "shares": r["shares"], "value": r["value"],
                     "weight": None if r["put_call"] else round(100 * r["value"] / total, 2),
                     "change": change, "change_shares": diff, "prev_shares": prev_shares,
                     "change_pct": round(100 * diff / prev_shares, 1) if prev_shares and diff is not None else None})
    if previous is not None:
        kept = {(r["cusip"], r["put_call"]) for r in current}
        for p in sorted(previous, key=lambda r: -r["value"]):
            if (p["cusip"], p["put_call"]) not in kept:
                rows.append({**_row(p), "shares": 0, "value": 0, "weight": None if p["put_call"] else 0.0,
                             "change": "sold", "change_shares": -p["shares"], "prev_shares": p["shares"],
                             "change_pct": -100.0})
    return rows


def _row(r: dict) -> dict:
    return {"ticker": r.get("ticker"), "name": r["name"], "cusip": r["cusip"], "title": r["title"],
            "put_call": r["put_call"], "share_type": r.get("share_type") or "SH"}


# ------------------------------------------------------------------ words

def won_style(dollars: int) -> str:
    """$2,348,000,000 → "23억 4,800만 달러": to the nearest 100만 from 1억 up, to the 만 below."""
    a = abs(dollars)
    v = round(a / 1_000_000) * 1_000_000 if a >= 100_000_000 else round(a / 10_000) * 10_000
    eok, man = divmod(v, 100_000_000)
    man //= 10_000
    parts = ([f"{eok:,}억"] if eok else []) + ([f"{man:,}만"] if man else [])
    text = " ".join(parts) or f"{a:,}"
    return ("-" if dollars < 0 else "") + text + " 달러"


def _label(h: dict) -> str:
    return h["ticker"] or h["name"].title()


def _stock(h: dict) -> bool:
    return not h["put_call"] and h["share_type"] == "SH"


def _price(h: dict) -> float:
    return h["value"] / h["shares"] if h["shares"] else 0.0


def summary(fund: Fund, quarter: str, holdings: list[dict]) -> tuple[str, list[str]]:
    """The headline and one line each for the largest new, increased, reduced and sold stock positions."""
    q = int(quarter[-1])
    stocks = [h for h in holdings if _stock(h)]
    new = sorted((h for h in stocks if h["change"] == "new"), key=lambda h: -h["value"])
    up = sorted((h for h in stocks if h["change"] == "increase"), key=lambda h: -h["change_shares"] * _price(h))
    down = sorted((h for h in stocks if h["change"] == "decrease"), key=lambda h: h["change_shares"] * _price(h))
    sold = sorted((h for h in stocks if h["change"] == "sold"), key=lambda h: -(h["prev_shares"] or 0))
    head = f"{fund.manager} {fund.name} {q}분기 포트폴리오"
    if new:
        head += f" — {_label(new[0])} {won_style(new[0]['value'])} 신규 매수"
    elif up:
        head += f" — {_label(up[0])} 비중 확대"
    bullets = []
    if new:
        bullets.append("신규: " + ", ".join(f"{_label(h)} {h['shares']:,}주({won_style(h['value'])}, {h['weight']:.1f}%)"
                                           for h in new[:3]))
    if up:
        bullets.append("늘림: " + ", ".join(f"{_label(h)} +{h['change_shares']:,}주" for h in up[:3]))
    if down:
        bullets.append("줄임: " + ", ".join(f"{_label(h)} {h['change_shares']:,}주" for h in down[:3]))
    if sold:
        bullets.append("전량 매도: " + ", ".join(_label(h) for h in sold[:5]))
    return head, bullets


# ------------------------------------------------------------------ EDGAR

# Headers that describe the body as it came over the wire: the rebuilt response carries the decoded body,
# so keeping them made httpx decode a gzip body a second time (DecodingError on every SEC answer).
_WIRE_HEADERS = ("content-encoding", "content-length", "transfer-encoding")


def _capped_get(url: str, headers: dict, transport: httpx.BaseTransport | None = None) -> httpx.Response:
    """GET with no redirects and at most MAX_BYTES of (decoded) body."""
    with net.client(transport=transport, timeout=TIMEOUT_SEC, follow_redirects=False) as client, \
            client.stream("GET", url, headers=headers) as r:
        body = bytearray()
        for chunk in r.iter_bytes():         # decoded (gzip / deflate) as it streams
            body += chunk
            if len(body) > MAX_BYTES:
                raise FundsUnavailable("SEC 문서가 너무 커서 읽지 않았습니다")
        kept = [(k, v) for k, v in r.headers.multi_items() if k.lower() not in _WIRE_HEADERS]
        return httpx.Response(r.status_code, headers=kept, content=bytes(body), request=r.request)


class Edgar:
    """SEC requests MIN_GAP_SEC apart (thread-safe), with SEC's User-Agent, no redirects."""

    def __init__(self, get: Callable[..., httpx.Response] | None = None, agent: Callable[[], str] = user_agent,
                 clock=time.monotonic, sleep=time.sleep) -> None:
        self._get = get or _capped_get
        self._agent, self._clock, self._sleep = agent, clock, sleep
        self._lock = threading.Lock()
        self._last = 0.0

    def fetch(self, url: str) -> bytes:
        with self._lock:
            wait = self._last + MIN_GAP_SEC - self._clock()
            if wait > 0:
                self._sleep(wait)
            self._last = self._clock()
        r = self._get(url, headers={"User-Agent": self._agent(), "Accept-Encoding": "gzip, deflate"})
        if r.status_code == 403 and b"Undeclared Automated Tool" in r.content[:3000]:
            raise FundsUnavailable("SEC 가 연락처 없는 User-Agent 를 거절했습니다 — SEC_EDGAR_USER_AGENT 를 설정하세요")
        if 300 <= r.status_code < 400:
            raise FundsUnavailable(f"SEC 가 다른 주소로 보냈습니다(HTTP {r.status_code})")
        if r.status_code == 404:
            raise FundsUnavailable("SEC 에 그 문서가 없습니다")
        r.raise_for_status()
        return r.content

    def json(self, url: str) -> dict:
        return json.loads(self.fetch(url))


def filings(submissions: dict) -> dict[str, list[dict]]:
    """period of report → its 13F filings, oldest first (accession numbers checked)."""
    recent = (submissions.get("filings") or {}).get("recent") or {}
    out: dict[str, list[dict]] = {}
    for i, form in enumerate(recent.get("form") or []):
        acc = recent["accessionNumber"][i]
        if form not in HR_FORMS + NT_FORMS or not ACC_RE.fullmatch(acc or ""):
            continue
        try:
            period = date.fromisoformat(recent["reportDate"][i]).isoformat()
            filed = date.fromisoformat(recent["filingDate"][i]).isoformat()
        except (TypeError, ValueError):
            continue
        out.setdefault(period, []).append({"form": form, "accession": acc, "filed_at": filed, "period": period})
    for rows in out.values():
        rows.sort(key=lambda r: (r["filed_at"], r["accession"]))
    return out


def _shown(fs: list[dict]) -> dict:
    """The filing a period is shown by: its latest one."""
    return fs[-1]


# ------------------------------------------------------------------ the desk's view

class Funds:
    """Each fund's 13F periods (from its submissions) and the tables read so far (memory + disk)."""

    def __init__(self, edgar: Edgar | None = None, cache_dir: str | Path | None = CACHE_DIR,
                 figi: Callable[[list[str]], dict[str, str | None]] | None = None,
                 search: Callable[[str], str | None] | None = None,
                 today=lambda: datetime.now(UTC).date(), clock=time.monotonic, sleep=time.sleep) -> None:
        self.edgar = edgar or Edgar()
        self.dir = Path(cache_dir) if cache_dir else None
        self.figi = figi or _openfigi
        self.search = search or _openfigi_search
        self._today, self._clock, self._sleep = today, clock, sleep
        self._net = threading.RLock()                        # one SEC / OpenFIGI fetch at a time
        self._save_lock = threading.Lock()
        self.periods: dict[str, dict[str, list[dict]]] = {}  # fund id → period → its filings
        self.errors: dict[str, str | None] = {}
        self._lasting: dict[str, bool] = {}                  # an error that a retry will not fix
        self.checked_at: str | None = None
        self._tables: OrderedDict[str, list[dict]] = OrderedDict()   # accession → merged rows
        self._views: OrderedDict[tuple, dict] = OrderedDict()
        self._latest: dict[str, dict] = {}                   # fund id → its latest quarter's view
        self._fails: dict[str, tuple[float, str]] = {}
        self._kinds: dict[str, str | None] = self._load_json("amendments.json") or {}
        self._tickers: dict[str, str | None] = self._load_json("cusip_tickers.json") or {}
        self._searched: set[str] = set(self._load_json("cusip_searched.json") or [])   # CINS names tried once
        self._search_at = 0.0
        self._figi_until = 0.0

    # --- cache files
    def _load_json(self, name: str):
        if not self.dir:
            return None
        try:
            return json.loads((self.dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _save_json(self, name: str, value) -> None:
        if not self.dir:
            return
        with self._save_lock:
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
                text = json.dumps(value, ensure_ascii=False)
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.dir, delete=False,
                                                 suffix=".tmp") as tmp:
                    tmp.write(text)
                Path(tmp.name).replace(self.dir / name)
            except OSError as exc:
                log.warning("funds13f cache %s not written: %s", name, exc)

    @contextmanager
    def _fetching(self, wait: bool) -> Iterator[None]:
        if not self._net.acquire(blocking=wait):
            raise FundsBusy("다른 요청이 SEC 에서 공시를 받는 중입니다 — 잠시 뒤 다시 보세요")
        try:
            yield
        finally:
            self._net.release()

    def _failed(self, key: str) -> None:
        hit = self._fails.get(key)
        if hit and self._clock() - hit[0] < FAIL_TTL_SEC:
            raise FundsUnavailable(hit[1])

    def _remember_fail(self, key: str, exc: Exception) -> None:
        self._fails[key] = (self._clock(), _short(exc))
        if len(self._fails) > 256:
            self._fails.clear()

    # --- refresh
    def refresh(self) -> int:
        """Ask every fund's submissions and read its latest quarter; returns how many answered. A
        failure keeps the last answer and says why."""
        ok = 0
        for fund in FUNDS:
            try:
                with self._fetching(wait=True):
                    got = filings(self.edgar.json(SUBMISSIONS_URL.format(cik=fund.cik)))
                self.periods[fund.id] = got
                self.errors[fund.id], self._lasting[fund.id] = None, False
                ok += 1
            except Exception as exc:  # noqa: BLE001 - one fund down leaves the others
                log.warning("13F submissions for %s failed: %s", fund.id, _short(exc))
                self.errors[fund.id], self._lasting[fund.id] = _short(exc), False
                continue
            if not got:              # no 13F in the recent list: nothing to read
                continue
            try:
                v = self.view(fund.id, wait=True, map_budget=MAP_PER_PASS, search_budget=SEARCH_PER_PASS)
                if v["prev_quarter"] is None and self._has_previous(fund.id, v["period_of_report"]):
                    # the quarter before is filed but could not be read: keep a compared view, retry soon
                    self.errors[fund.id] = "전 분기 공시를 읽지 못해 변화를 계산하지 못했습니다"
                    if fund.id not in self._latest:
                        self._latest[fund.id] = v
                else:
                    self._latest[fund.id] = v
            except Exception as exc:  # noqa: BLE001 - the list still shows the filing, without its table
                log.warning("13F table for %s failed: %s", fund.id, _short(exc))
                self.errors[fund.id] = _short(exc)
                self._lasting[fund.id] = isinstance(exc, FundsUnavailable) and any(w in str(exc) for w in LASTING)
        self.checked_at = datetime.now(UTC).isoformat(timespec="seconds")
        return ok

    def _has_previous(self, fund_id: str, period: str) -> bool:
        prev = period_of(previous_quarter(quarter_of(date.fromisoformat(period)))).isoformat()
        return prev in (self.periods.get(fund_id) or {})

    def next_delay(self) -> int:
        failed = any(self.errors.get(f.id) and not self._lasting.get(f.id) for f in FUNDS)
        return RETRY_SEC if failed else check_every(self._today())

    # --- documents
    def _kind(self, cik: str, filing: dict) -> str | None:
        """An HR/A's amendment type (RESTATEMENT or NEW HOLDINGS); None for an original filing."""
        if filing["form"] != "13F-HR/A":
            return None
        acc = filing["accession"]
        if acc not in self._kinds:
            kind = cover(self.edgar.fetch(ARCHIVE_URL.format(cik=int(cik), acc=acc.replace("-", ""))
                                          + "primary_doc.xml"))["amendment_type"]
            self._kinds[acc] = kind
            self._save_json("amendments.json", dict(self._kinds))
        return self._kinds[acc]

    def _table(self, cik: str, filing: dict, only: tuple[str, str | None] | None = None,
               strict: bool = True) -> list[dict]:
        """One filing's merged rows (memory, disk, then SEC); with ``only`` (a fund's CIK and 13F file
        number) just that fund's rows of a combination report, kept apart from the whole table - an
        amendment (``strict`` False) with none of them adds nothing."""
        acc = filing["accession"]
        key = acc if only is None else f"{acc}-for-{only[0]}-r{SHARE_RULE}"
        if key in self._tables:
            self._tables.move_to_end(key)
            return self._tables[key]
        cached = self._load_json(f"table-{key}.json")
        if cached is None:
            self._failed(key)
            try:
                cached = {"rows": self._fetch_table(cik, filing, only, strict)}
            except Exception as exc:
                self._remember_fail(key, exc)
                raise
            self._save_json(f"table-{key}.json", cached)
        self._tables[key] = cached["rows"]
        while len(self._tables) > KEEP_TABLES:
            self._tables.popitem(last=False)
        return cached["rows"]

    def _fetch_table(self, cik: str, filing: dict, only: tuple[str, str | None] | None = None,
                     strict: bool = True) -> list[dict]:
        base = ARCHIVE_URL.format(cik=int(cik), acc=filing["accession"].replace("-", ""))
        managers = None
        if only is not None:
            # the fund's sequence number among the managers this report includes (by CIK or file number)
            included = cover(self.edgar.fetch(base + "primary_doc.xml"))["included"]
            managers = {seq for seq, m in included.items()
                        if (m["cik"] and m["cik"] == only[0]) or (m["file_number"] and m["file_number"] == only[1])}
            if not managers:
                if not strict:           # another adviser's amendment: the fund's table stands as it was
                    return []
                raise FundsUnavailable("보고한 운용사의 공시에서 이 기관 몫을 가려낼 수 없습니다")
        index = self.edgar.json(base + "index.json")
        names = [i.get("name", "") for i in (index.get("directory") or {}).get("item") or []]
        names = [n for n in names if XML_NAME_RE.fullmatch(n) and n.lower() != "primary_doc.xml"]
        dollars = date.fromisoformat(filing["filed_at"]) >= DOLLARS_FILED_FROM
        for name in names:
            xml = self.edgar.fetch(base + name)
            if root_name(xml) != "informationTable":
                continue
            rows = merge(parse_info_table(xml, dollars, managers))
            if not rows and not (managers and not strict):
                raise FundsUnavailable("공시의 보유 표가 비어 있습니다")
            return rows
        raise FundsUnavailable("공시에 보유 표(information table)가 없습니다")

    def _period_rows(self, cik: str, fs: list[dict], reporters: tuple[Reporter, ...] = (), nested: bool = False,
                     only: tuple[str, str | None] | None = None) -> tuple[list[dict], dict | None]:
        """A period's merged rows: the latest original or restatement plus later NEW HOLDINGS amendments;
        a 13F-NT followed (once) to the manager that reports (``only``: the reported fund's own rows)."""
        hr = [f for f in fs if f["form"] in HR_FORMS]
        if hr:
            base = 0
            for i, f in enumerate(hr):
                if f["form"] == "13F-HR" or self._kind(cik, f) != "NEW HOLDINGS":
                    base = i
            rows = list(self._table(cik, hr[base], only))
            for f in hr[base + 1:]:
                rows += self._table(cik, f, only, strict=False)   # every later one is a NEW HOLDINGS amendment
            return merge(rows), None
        if nested:
            raise FundsUnavailable("보고한 운용사도 13F-NT 만 냈습니다")
        nt = fs[-1]
        key = "nt-" + nt["accession"]
        self._failed(key)
        try:
            page = cover(self.edgar.fetch(ARCHIVE_URL.format(cik=int(cik), acc=nt["accession"].replace("-", ""))
                                          + "primary_doc.xml"))
            found = reporter_of(page["others"], reporters)
            if found is None:
                named = page["other"]["name"] if page["other"] and page["other"]["name"] else ""
                raise FundsUnavailable("13F-NT: 보유를 보고한 운용사를 찾지 못했습니다" + (f"({named})" if named else ""))
            their_cik, their_name, own_rows = found
            theirs = filings(self.edgar.json(SUBMISSIONS_URL.format(cik=their_cik))).get(nt["period"])
            if not theirs:
                raise FundsUnavailable(f"13F-NT: {their_name} 의 같은 분기 공시가 아직 없습니다")
            rows, _ = self._period_rows(their_cik, theirs, nested=True,
                                        only=(cik, page["file_number"]) if own_rows else None)
        except Exception as exc:
            self._remember_fail(key, exc)
            raise
        scope = next((r.scope for r in reporters if r.cik == their_cik), None) if own_rows else None
        return rows, {"name": their_name, "cik": their_cik, "own_rows": own_rows, "scope": scope}

    # --- tickers
    def _map(self, holdings: list[dict], budget: int, search_budget: int = 0) -> None:
        """Map the largest and the most changed unmapped CUSIPs, ``budget`` at most."""
        if budget <= 0 or self._clock() < self._figi_until:
            return
        order = sorted(holdings, key=lambda h: -max(h["value"], abs(h["change_shares"] or 0) * (_price(h) or 1)))
        want = list(dict.fromkeys(h["cusip"] for h in order if h["cusip"] and h["cusip"] not in self._tickers))[:budget]
        if want:
            try:
                got = self.figi(want)
            except Exception as exc:  # noqa: BLE001 - names stand without tickers; not asked again for a while
                log.info("OpenFIGI mapping failed: %s", _short(exc))
                self._figi_until = self._clock() + FAIL_TTL_SEC
                return
            self._tickers.update({c: (t.replace("/", "-").upper() if t else None) for c, t in got.items()})
            self._save_json("cusip_tickers.json", dict(self._tickers))
        if search_budget > 0:             # the refresh pass only: a page never waits on the search's pace
            self._search_foreign(order, search_budget)

    def _search_foreign(self, order: list[dict], budget: int) -> None:
        """#512 the unmapped foreign CINS by name (the largest first), each once, ``budget`` at most, every
        search ``SEARCH_GAP_SEC`` after the one before (across funds)."""
        todo = [h for h in order if h["cusip"] and h["cusip"][0].isalpha() and h.get("name")
                and h["cusip"] in self._tickers and self._tickers[h["cusip"]] is None
                and h["cusip"] not in self._searched]
        todo = list({h["cusip"]: h for h in todo}.values())[:budget]
        for h in todo:
            wait = self._search_at + SEARCH_GAP_SEC - self._clock()
            if self._search_at and wait > 0:
                self._sleep(wait)
            self._search_at = self._clock()
            try:
                ticker = self.search(h["name"])
            except Exception as exc:  # noqa: BLE001 - tried again on a later pass
                log.info("OpenFIGI search failed: %s", _short(exc))
                self._figi_until = self._clock() + FAIL_TTL_SEC
                break
            self._searched.add(h["cusip"])
            if ticker:
                self._tickers[h["cusip"]] = ticker.replace("/", "-").upper()
        if todo:
            self._save_json("cusip_tickers.json", dict(self._tickers))
            self._save_json("cusip_searched.json", sorted(self._searched))

    # --- answers
    def list(self) -> dict:
        rows = []
        for fund in FUNDS:
            periods = self.periods.get(fund.id) or {}
            latest = max(periods) if periods else None
            f = _shown(periods[latest]) if latest else None
            v = self._latest.get(fund.id)
            v = v if v and f and v["period_of_report"] == f["period"] else None
            top_new = sorted((h for h in (v or {}).get("holdings", []) if h["change"] == "new" and _stock(h)),
                             key=lambda h: -h["value"])[:3]
            rows.append({"id": fund.id, "name": fund.name, "manager": fund.manager, "cik": fund.cik,
                         "latest": {"quarter": quarter_of(date.fromisoformat(f["period"])), "period_of_report": f["period"],
                                    "filed_at": f["filed_at"], "form": f["form"],
                                    "count": v["count"] if v else None, "total_value": v["total_value"] if v else None,
                                    "summary": v["summary"] if v else None,
                                    "top_new": [{"ticker": h["ticker"], "name": h["name"], "value": h["value"]} for h in top_new]}
                         if f else None,
                         "next_due": next_due(self._today()).isoformat(), "error": self.errors.get(fund.id)})
        return {"funds": rows, "checked_at": self.checked_at}

    def view(self, fund_id: str, quarter: str | None = None, wait: bool = False,
             map_budget: int = MAP_PER_REQUEST, search_budget: int = 0) -> dict:
        """The holdings of ``quarter`` (the latest when None) against the quarter before.
        KeyError: no such fund · ValueError: a bad quarter · NotFiled: nothing filed for it ·
        FundsBusy: another fetch is running (``wait`` False) · FundsUnavailable: SEC / the filing."""
        fund = BY_ID[fund_id]
        periods = self.periods.get(fund.id) or {}
        if not periods:
            raise FundsUnavailable(self.errors.get(fund.id) or "아직 공시 목록을 받지 못했습니다")
        period = max(periods) if quarter is None else period_of(quarter).isoformat()
        if period not in periods:
            raise NotFiled(f"{quarter} 공시가 없습니다")
        q = quarter_of(date.fromisoformat(period))
        prev_q = previous_quarter(q)
        prev_period = period_of(prev_q).isoformat()
        key = (fund.id, period, tuple(f["accession"] for f in periods[period]),
               tuple(f["accession"] for f in periods.get(prev_period, [])))
        hit = self._views.get(key)
        if hit is not None and (hit["prev_quarter"] or prev_period not in periods):
            self._views.move_to_end(key)
            return hit
        with self._fetching(wait):
            rows, reported_by = self._period_rows(fund.cik, periods[period], fund.reporters)
            prev_rows = None
            if prev_period in periods:
                try:
                    prev_rows, _ = self._period_rows(fund.cik, periods[prev_period], fund.reporters)
                except Exception as exc:  # noqa: BLE001 - the quarter stands without its comparison
                    log.info("13F %s %s (previous) failed: %s", fund.id, prev_q, _short(exc))
            holdings = compare(rows, prev_rows)
            self._map(holdings, map_budget, search_budget)
        for h in holdings:
            h["ticker"] = self._tickers.get(h["cusip"])
        head, bullets = summary(fund, q, holdings)
        live = [h for h in holdings if h["change"] != "sold"]
        shown = _shown(periods[period])
        v = {"id": fund.id, "name": fund.name, "manager": fund.manager, "quarter": q, "period_of_report": period,
             "filed_at": shown["filed_at"], "form": shown["form"], "reported_by": reported_by,
             "prev_quarter": prev_q if prev_rows is not None else None,
             "quarters": [quarter_of(date.fromisoformat(p)) for p in sorted(periods, reverse=True)],
             "total_value": sum(h["value"] for h in live if not h["put_call"]),
             "options_value": sum(h["value"] for h in live if h["put_call"]),
             "count": sum(1 for h in live if not h["put_call"]),
             "holdings": holdings, "summary": head, "bullets": bullets}
        self._views[key] = v
        while len(self._views) > KEEP_VIEWS:
            self._views.popitem(last=False)
        return v

    def changes(self, tickers: list[str]) -> dict:
        """Each ticker's change in every fund's latest quarter (the views read in the last pass - no fetch),
        keyed by the ticker as asked (BRK.B, BRK-B and FIGI's BRK/B are one share class)."""
        want = {_class_key(t): t for t in tickers}
        out: dict[str, list[dict]] = {}
        for fund in FUNDS:
            periods = self.periods.get(fund.id) or {}
            view = self._latest.get(fund.id)
            if not periods or not view or view["period_of_report"] != max(periods):
                continue
            for h in view["holdings"]:
                asked = want.get(_class_key(h["ticker"] or ""))
                if (asked and _stock(h)                                     # stock positions, not options or bonds
                        and h["change"] not in (None, "unchanged")):
                    out.setdefault(asked, []).append(
                        {"fund": fund.id, "name": fund.name, "quarter": view["quarter"], "change": h["change"],
                         "change_shares": h["change_shares"], "shares": h["shares"], "weight": h["weight"]})
        return {"items": out}


def _class_key(ticker: str) -> str:
    """One spelling for a share class: BRK.B · BRK-B · BRK/B → BRK-B."""
    return re.sub(r"[./]", "-", ticker.strip().upper())


def _openfigi(cusips: list[str]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for i in range(0, len(cusips), FIGI_BATCH):
        chunk = cusips[i:i + FIGI_BATCH]
        if i:
            time.sleep(FIGI_GAP_SEC)
        r = httpx.post(FIGI_URL, json=[{"idType": "ID_CUSIP", "idValue": c, "exchCode": "US"} for c in chunk],
                       headers={"Content-Type": "application/json"}, timeout=TIMEOUT_SEC, follow_redirects=False)
        r.raise_for_status()
        for c, res in zip(chunk, r.json()):
            data = (res or {}).get("data") or []
            out[c] = data[0].get("ticker") if data else None
    return out


def core_name(name: str) -> str:
    """An issuer's name without punctuation and legal suffixes: "ALCON AG" and "ALCON INC" are both "ALCON"."""
    words = re.sub(r"[^A-Z0-9 ]", " ", name.upper().replace("&", " AND ")).split()
    return " ".join(w for w in words if w not in _SUFFIXES)


def pick_listing(name: str, data: list[dict]) -> str | None:
    """The one US-listed common share or depositary receipt whose name is the issuer's; None when none or
    more than one ticker matches (a guess is worse than no ticker)."""
    want = core_name(name)
    if not want:
        return None
    found = {d.get("ticker") for d in data
             if d.get("ticker") and d.get("securityType2") in SEARCH_TYPES and core_name(d.get("name") or "") == want}
    return found.pop() if len(found) == 1 else None


def _openfigi_search(name: str) -> str | None:
    query = core_name(name)
    if not query:
        return None
    r = httpx.post(FIGI_SEARCH_URL, json={"query": query, "exchCode": "US", "marketSecDes": "Equity"},
                   headers={"Content-Type": "application/json"}, timeout=TIMEOUT_SEC, follow_redirects=False)
    r.raise_for_status()
    return pick_listing(name, r.json().get("data") or [])


def _short(exc: Exception) -> str:
    """A reason for the page: the desk's own words as they are, anything else by its class only."""
    if isinstance(exc, FundsUnavailable):
        return str(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        return f"SEC 응답 오류(HTTP {exc.response.status_code})"
    return f"공시를 받지 못했습니다({type(exc).__name__})"
