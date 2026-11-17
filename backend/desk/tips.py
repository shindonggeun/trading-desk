"""Rate · dollar regimes and the trading tips that go with them (#330).

"Don't touch gold miners while rates go up" is checked against data, not repeated: each tip names
the regime it is about (``condition``), the instruments it applies to, a proxy whose history is the
evidence, and what is known to break it (``exceptions``). ``desk.tips_bt`` measures every tip from
2000 on - the proxy's forward 20 / 60-session return on the days the regime held vs on all days,
split into a train and a held-out period - and writes ``data/backtest/tips.json``. A tip whose
regime days were not clearly worse than all days in both periods is ``weak`` (shown, but said so).

The regime now comes from four free Yahoo daily series:

- the Fed's direction: the 13-week bill (``^IRX``) over 63 sessions (≈ 3 months) - a bill yield
  that rose a quarter point is a hiking cycle priced in, one that fell is a cutting one;
- the 10-year yield (``^TNX``) over 63 sessions (trend) and over 21 sessions (a spike);
- the dollar index (``DX-Y.NYB``) over 63 sessions;
- the Nasdaq-100 (``QQQ``) over 63 sessions: a market that went nowhere is the sideways regime
  where daily-reset leveraged ETFs bleed.

The 10-year real yield is not in it: there is no key-less daily series of it on Yahoo.
Pure functions apart from ``Cache`` (a worker-thread Yahoo fetch through ``risk_regime.fetch``).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

SYMBOLS = ("^IRX", "^TNX", "DX-Y.NYB", "QQQ")
HISTORY_BARS = 100
TREND = 63                 # sessions: about three months
SPIKE = 21                 # sessions: about a month
RATE_STEP = 0.25           # percentage points of the 13-week bill: a quarter-point move
YIELD_TREND = 0.40         # 10-year, percentage points over TREND
YIELD_SPIKE = 0.40         # 10-year, percentage points over SPIKE
DOLLAR_MOVE = 3.0          # dollar index, % over TREND
SIDEWAYS = 5.0             # QQQ, |%| over TREND
EVIDENCE_PATH = Path(__file__).with_name("data") / "backtest" / "tips.json"

Series = dict[str, list[dict]]


def _closes(series: Series, symbol: str) -> list[float]:
    return [r["close"] for r in series.get(symbol) or [] if r.get("close") is not None]


def _diff(closes: list[float], n: int) -> float | None:
    return closes[-1] - closes[-1 - n] if len(closes) > n else None


def _pct(closes: list[float], n: int) -> float | None:
    if len(closes) <= n or not closes[-1 - n]:
        return None
    return (closes[-1] / closes[-1 - n] - 1) * 100


def regime(series: Series) -> dict:
    """The regime on the last bar of ``series``; a part whose series is missing or short is None."""
    irx, tnx = _closes(series, "^IRX"), _closes(series, "^TNX")
    dxy, qqq = _closes(series, "DX-Y.NYB"), _closes(series, "QQQ")
    rate_d, tnx_d, tnx_s = _diff(irx, TREND), _diff(tnx, TREND), _diff(tnx, SPIKE)
    dxy_p, qqq_p = _pct(dxy, TREND), _pct(qqq, TREND)
    out = {
        "rates": None if rate_d is None else "hiking" if rate_d >= RATE_STEP else "cutting" if rate_d <= -RATE_STEP else "hold",
        "yield10": None if tnx_d is None else "rising" if tnx_d >= YIELD_TREND else "falling" if tnx_d <= -YIELD_TREND else "flat",
        "yield_spike": None if tnx_s is None else tnx_s >= YIELD_SPIKE,
        "dollar": None if dxy_p is None else "strong" if dxy_p >= DOLLAR_MOVE else "weak" if dxy_p <= -DOLLAR_MOVE else "flat",
        "sideways": None if qqq_p is None else abs(qqq_p) <= SIDEWAYS,
        "values": {"bill_3m_change_pp": _r(rate_d), "yield10_3m_change_pp": _r(tnx_d), "yield10_1m_change_pp": _r(tnx_s),
                   "dollar_3m_pct": _r(dxy_p), "nasdaq_3m_pct": _r(qqq_p),
                   "bill": _r(irx[-1] if irx else None), "yield10": _r(tnx[-1] if tnx else None)},
        "as_of": max((rows[-1]["day"] for rows in series.values() if rows), default=None),
    }
    return out


def _r(v: float | None, nd: int = 2) -> float | None:
    return None if v is None else round(v, nd)


@dataclass(frozen=True)
class Tip:
    id: str
    title: str
    condition: str                                  # Korean, shown as is
    holds: Callable[[dict], bool | None]            # the regime -> applies now (None: cannot tell)
    tickers: frozenset[str]
    proxy: str                                      # whose history is the evidence
    metric: str = "return"                          # "return" | "decay" (leveraged 3x vs 3x the index)
    exceptions: tuple[str, ...] = field(default_factory=tuple)


def _all(*parts: bool | None) -> bool | None:
    if any(p is False for p in parts):
        return False
    return None if any(p is None for p in parts) else True


GOLD_MINERS = frozenset({"GDX", "GDXJ", "NUGT", "JNUG", "GDXU", "RING", "SIL", "SILJ", "NEM", "GOLD", "AEM", "KGC",
                         "AU", "WPM", "FNV"})
COMMODITIES = frozenset({"DBC", "GSG", "PDBC", "USO", "UCO", "BNO", "BOIL", "UNG", "AGQ", "SLV", "UGL", "DBA",
                         "DBB", "CPER", "XME"})
EMERGING = frozenset({"EEM", "VWO", "EDC", "EWZ", "EWY", "KORU", "FXI", "YINN", "INDA", "EWT", "MCHI"})
GROWTH = frozenset({"QQQ", "TQQQ", "QLD", "ARKK", "SOXL", "TECL", "FNGU", "NVDA", "TSLA", "AMD", "PLTR", "NVDL",
                    "TSLL", "SOXX", "SMH", "IGV", "WCLD"})
LEVERAGED = frozenset({"TQQQ", "SQQQ", "QLD", "SOXL", "SOXS", "UPRO", "SPXU", "SPXL", "SPXS", "TECL", "TNA", "TZA",
                       "FNGU", "NVDL", "TSLL", "LABU", "NUGT", "JNUG", "GDXU", "UCO", "BOIL", "AGQ", "KORU", "YINN", "EDC"})

TIPS: tuple[Tip, ...] = (
    Tip("gold-miners-tightening", "금리 인상 · 10년물 상승기엔 금광주 · 금광 레버리지를 피한다",
        "13주물이 3개월에 0.25%p 이상 오르고(인상기) 10년물도 0.4%p 이상 오를 때",
        lambda g: _all(g["rates"] == "hiking" if g["rates"] else None, g["yield10"] == "rising" if g["yield10"] else None),
        GOLD_MINERS, "^XAU",
        exceptions=("위기 · 전쟁처럼 안전자산 수요가 금리보다 셀 때는 금광주도 오른다",
                    "인상 막바지(시장이 인하를 미리 반영)에는 반등이 먼저 온다")),
    Tip("commodities-strong-dollar", "달러 강세기엔 원자재 · 원자재 레버리지를 줄인다",
        "달러 지수가 3개월에 3% 이상 오를 때",
        lambda g: None if g["dollar"] is None else g["dollar"] == "strong",
        COMMODITIES, "DBC",
        exceptions=("공급 충격(산유국 감산 · 전쟁)이면 달러가 강해도 에너지는 오른다 — 2022년 상반기",)),
    Tip("emerging-strong-dollar", "달러 강세기엔 신흥국(한국 포함) ETF 를 줄인다",
        "달러 지수가 3개월에 3% 이상 오를 때",
        lambda g: None if g["dollar"] is None else g["dollar"] == "strong",
        EMERGING, "EEM",
        exceptions=("그 나라만의 호재(반도체 사이클 등)가 있으면 지수와 다르게 간다",)),
    Tip("growth-yield-spike", "10년물 급등기엔 고밸류 성장주 · 나스닥 레버리지 비중을 줄인다",
        "10년물이 한 달에 0.4%p 이상 오를 때",
        lambda g: g["yield_spike"],
        GROWTH, "QQQ",
        exceptions=("실적이 금리보다 크게 놀라면(AI 사이클 초기처럼) 금리가 올라도 오른다",)),
    Tip("leverage-decay-sideways", "횡보장엔 일일 레버리지 ETF 를 오래 들고 있지 않는다",
        "나스닥100 이 3개월 동안 ±5% 안에서 움직일 때",
        lambda g: g["sideways"],
        LEVERAGED, "QQQ", metric="decay",
        exceptions=("짧게(며칠) 들고 방향이 맞으면 감쇠보다 방향 수익이 크다",)),
)


def load_evidence(path: Path = EVIDENCE_PATH) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("tips evidence %s missing or unreadable", path)
        return {}


def _why(tip: Tip, g: dict) -> str:
    v = g.get("values") or {}
    parts = {
        "gold-miners-tightening": f"13주물 3개월 {v.get('bill_3m_change_pp')}%p · 10년물 3개월 {v.get('yield10_3m_change_pp')}%p",
        "commodities-strong-dollar": f"달러 지수 3개월 {v.get('dollar_3m_pct')}%",
        "emerging-strong-dollar": f"달러 지수 3개월 {v.get('dollar_3m_pct')}%",
        "growth-yield-spike": f"10년물 1개월 {v.get('yield10_1m_change_pp')}%p",
        "leverage-decay-sideways": f"나스닥100 3개월 {v.get('nasdaq_3m_pct')}%",
    }
    return parts.get(tip.id, "")


def view(g: dict, evidence: dict, ticker: str | None = None) -> dict:
    """The answer of /api/tips: the regime and every tip (or only those ``ticker`` is in)."""
    ticker = (ticker or "").upper() or None
    rows = []
    for tip in TIPS:
        if ticker and ticker not in tip.tickers:
            continue
        ev = (evidence.get("tips") or {}).get(tip.id)
        holds = tip.holds(g)
        rows.append({
            "id": tip.id, "title": tip.title, "condition": tip.condition,
            "active": holds is True, "known": holds is not None,
            "why": _why(tip, g), "proxy": tip.proxy, "metric": tip.metric,
            "applies_to": sorted(tip.tickers),
            "evidence": ev,
            "weak": True if ev is None else bool(ev.get("weak")),
            "exceptions": list(tip.exceptions),
        })
    return {"regime": {k: g[k] for k in ("rates", "yield10", "yield_spike", "dollar", "sideways", "values")},
            "tips": rows, "as_of": g.get("as_of"), "evidence_as_of": evidence.get("as_of"),
            "basis": evidence.get("basis")}


def note_line(row: dict) -> str | None:
    """One English line for the agents on an active tip with its evidence; None for a weak one."""
    ev = row.get("evidence") or {}
    held = (ev.get("held_out") or {}).get("60") or {}
    if not row["active"] or row["weak"] or not held:
        return None
    what = "3x daily-reset vs 3x the index (decay)" if row["metric"] == "decay" else f"{row['proxy']} forward return"
    return (f"- {row['id']}: regime now ({row['why']}). In this regime since {ev.get('since')}, {what} over 60 sessions: "
            f"median {held.get('median')}% vs {held.get('all_median')}% on all days, win rate {held.get('win')}% "
            f"(held-out {ev.get('split')}+, n={held.get('n')} days). Exceptions: {'; '.join(row['exceptions'])}")


def prompt_note(ticker: str, g: dict | None, evidence: dict) -> str | None:
    if not g:
        return None
    lines = [n for n in (note_line(r) for r in view(g, evidence, ticker)["tips"]) if n]
    if not lines:
        return None
    head = ("Rate / dollar regime tips that apply to this instrument now (desk-computed from Yahoo "
            "^IRX ^TNX DX-Y.NYB QQQ; evidence is a backtest since 2000, a tendency, not a rule):")
    return "\n".join([head, *lines])


class Cache:
    """The regime for ``ttl_sec`` (thread-safe; a failed fetch is remembered briefly)."""

    def __init__(self, fetcher: Callable[[tuple[str, ...]], Series], ttl_sec: float = 3 * 3600.0,
                 fail_ttl_sec: float = 300.0, clock=time.monotonic, evidence: dict | None = None) -> None:
        self._fetch, self.ttl, self.fail_ttl, self._clock = fetcher, ttl_sec, fail_ttl_sec, clock
        self._lock = threading.Lock()
        self._hit: tuple[float, dict | None] | None = None
        self.evidence = evidence if evidence is not None else load_evidence()

    def _fresh(self, ahead: float) -> bool:
        hit = self._hit
        return bool(hit) and self._clock() - hit[0] < (self.ttl if hit[1] else self.fail_ttl) - ahead

    def regime(self, ahead: float = 0.0) -> dict | None:
        """``ahead`` (#536, the prewarm loop): refetch a value that expires within that many seconds.
        A fresh value is read without the lock, so a page never waits behind that refetch."""
        if self._fresh(ahead):
            return self._hit[1]
        with self._lock:
            if self._fresh(ahead):               # fetched while this one waited
                return self._hit[1]
            try:
                series = self._fetch(SYMBOLS)
                g = regime(series) if series else None
            except Exception as exc:  # noqa: BLE001 - nothing this time, asked again soon
                log.warning("tips regime fetch failed: %s", exc)
                series, g = {}, None
            old = self._hit
            if g is None and old and old[1] and self._clock() - old[0] < self.ttl:
                return old[1]                    # #536: a failed refetch-ahead keeps the regime still in date
            # a partial answer (a symbol failed) is kept only as long as a failure, then asked again
            partial = g is not None and not all(series.get(s) for s in SYMBOLS)
            self._hit = (self._clock() - (max(self.ttl - self.fail_ttl, 0.0) if partial else 0.0), g)
            return g

    def view(self, ticker: str | None = None) -> dict | None:
        g = self.regime()
        return view(g, self.evidence, ticker) if g else None

    def note(self, ticker: str) -> str | None:
        try:
            return prompt_note(ticker, self.regime(), self.evidence)
        except Exception:  # never fails a run
            log.exception("tips note for %s failed", ticker)
            return None
