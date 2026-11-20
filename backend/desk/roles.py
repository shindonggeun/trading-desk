"""#688 which process runs which background job: ``DESK_ROLE=all|api|collector|analyst``.

The backend is one image run as up to three processes, each with its own event loop and GIL:

- ``api``       - the screens: HTTP, SSE, login, and what they read straight from this process: the KIS live
                  stream and the prewarm that fills this process's memory caches (until those caches are shared,
                  a job that fills them runs where they are read);
- ``collector`` - the feeds and alerts: the broker listing, futures / market flow snapshots, NQ and line alerts,
                  credit, news, calendars, KRX history, 13F, and periodic upkeep;
- ``analyst``   - the model work: LLM runs (a later step of #688), the morning brief, reports, the day call
                  and plan scoring.

``all`` (the default) runs every job in one process - exactly the desk before #688.

``JOBS`` is the one list of background jobs: its name (``desk.jobs`` / ``GET /api/status``), label, source,
parent (a part read inside its parent's pass) and the role that runs it. A new job is one line here; the
lifespan in ``desk.api`` starts a job only when ``runs(role, name)`` - and its own switch - says so.

Production stays on ``all`` until the later steps of #688 land, because these still live in one process's memory:
- broker tokens: the Toss token (``broker/toss.py``), the KIS REST token (``futures_flow.KisRest``) and the KIS
  approval key (``broker/kis_stream.py``) - a second process would mint its own and revoke the first one's
  (with ``api`` keeping the KIS stream and the prewarm, that step decides who mints for whom);
- caches read both by the screens and by a run's notes: credit, the economic calendar, macro news, market /
  futures flow, tips (prewarm) and ``calibration.TABLE``'s live record (scoring);
- the KIS live quotes (``kis_stream``, api) that ``nq_fast`` (collector) reads for QQQ;
- the run gate's ``spare_slot`` the news digest, the brief and the reports wait on, and ``runner.Hub``.
"""

from __future__ import annotations

ROLES = ("all", "api", "collector", "analyst")

API, COLLECTOR, ANALYST = "api", "collector", "analyst"

# (name, label, source, parent, role). A part (parent set) runs inside its parent's pass, so it has its parent's role.
JOBS: tuple[tuple[str, str, str, str | None, str], ...] = (
    ("listing", "종목 목록", "토스", None, COLLECTOR),
    ("futures_snapshot", "코스피200 선물 수급 스냅숏", "한투(KIS)", None, COLLECTOR),
    ("nq_watch", "나스닥 선물 이상 징후", "야후", None, COLLECTOR),
    ("nq_fast", "나스닥 선물 1분 판정", "QQQ 실시간 · 바이낸스 추정", None, COLLECTOR),
    ("line_signals", "채널 · 추세선 신호", "야후 · 토스", None, COLLECTOR),
    ("credit", "빚투 · 예탁금", "금융투자협회(KOFIA)", None, COLLECTOR),
    ("news", "뉴스 흐름", "GDELT · 구글 뉴스", None, COLLECTOR),
    ("macro_news", "거시 · 정책 뉴스", "GDELT · 구글 뉴스", "news", COLLECTOR),
    ("sector_news", "섹터 · 인물 이슈", "GDELT · 구글 뉴스", "news", COLLECTOR),
    ("calendar", "일정", "FOMC · BLS · 나스닥", None, COLLECTOR),
    ("economic_calendar", "경제 일정(FOMC · BLS)", "연준 · BLS", "calendar", COLLECTOR),
    ("earnings", "실적 일정", "나스닥", "calendar", COLLECTOR),
    ("prewarm", "대시보드 미리 읽기", "야후 · 토스 · 한투", None, API),
    ("briefs", "아침 시황", "ChatGPT · 데스크", None, ANALYST),
    ("day_call", "오늘 방향", "야후", None, ANALYST),
    ("day_calls", "오늘 방향 판정", "야후", "day_call", ANALYST),
    ("day_call_scoring", "오늘 방향 채점", "야후", "day_call", ANALYST),
    ("krx", "KRX 일별 이력", "KRX OPEN API", None, COLLECTOR),
    ("reports", "정기 리포트", "ChatGPT · 야후", None, ANALYST),
    ("funds", "13F 기관 보유", "SEC EDGAR", None, COLLECTOR),
    ("scoring", "분석 채점", "야후", None, ANALYST),
    ("purge", "오래된 분석 정리", "DB", None, COLLECTOR),
    ("kis_stream", "실시간 시세 스트림", "한투(KIS)", None, API),
)

_ROLE_OF = {name: role for name, _label, _source, _parent, role in JOBS}


def parse(raw: str | None) -> str:
    """``DESK_ROLE`` as one of ``ROLES`` (empty = ``all``). A typo stops the start instead of quietly becoming
    ``all``: a second process running every job would also mint its own broker tokens (#688)."""
    role = (raw or "").strip().lower() or "all"
    if role not in ROLES:
        raise ValueError(f"DESK_ROLE must be one of {', '.join(ROLES)}")
    return role


def runs(role: str, name: str) -> bool:
    """Whether a process of ``role`` runs the job ``name``. An unlisted job is an error, not a silent skip."""
    own = _ROLE_OF.get(name)
    if own is None:     # checked before the role: a job switched off still needs its line (CI catches it)
        raise KeyError(f"background job {name!r} has no line in desk.roles.JOBS")
    return role == "all" or own == role
