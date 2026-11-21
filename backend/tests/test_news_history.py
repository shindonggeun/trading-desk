"""#390: the news themes' past from GDELT, collected politely into files and replayed into
desk.direction's macro part the way a live run reads desk.macro_news - from the days before the plan."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from desk import backtest, direction, news_history, playbook
from desk.macro_news import THEMES

TODAY = date(2026, 11, 16)


class Response:
    def __init__(self, status, payload):
        self.status_code = status
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def _timeline(points):
    return {"timeline": [{"series": "x", "data": [{"date": d.replace("-", "") + "T000000Z", "value": v}
                                                  for d, v in points.items()]}]}


class Clock:
    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


def test_collect_fetches_each_year_once_politely_and_stops_at_a_refusal(tmp_path):
    calls = []

    def get(url, params, timeout):
        calls.append((params["mode"], params["startdatetime"][:4]))
        if len(calls) == 3:
            return Response(429, "Please limit requests to one every 5 seconds")
        return Response(200, _timeline({f"{params['startdatetime'][:4]}-03-02": 1.5}))

    clock = Clock()
    theme = THEMES[:1]
    got = news_history.collect(tmp_path, theme, first_year=2025, get=get, sleep=clock.sleep, clock=clock, today=TODAY)
    # tone 2025, tone 2026, then vol 2025 refused: nothing more is asked
    assert calls == [("TimelineTone", "2025"), ("TimelineTone", "2026"), ("TimelineVolRaw", "2025")]
    assert got["fetched"] == 2 and got["refused"] and len(got["missing"]) == 2
    assert clock.slept == [news_history.REQUEST_GAP_SEC] * 2               # 20 s between requests, none before the first
    loaded = news_history.load(tmp_path, theme, first_year=2025)
    assert loaded[theme[0].id]["tone"] == {"2025-03-02": 1.5, "2026-03-02": 1.5} and loaded[theme[0].id]["vol"] == {}
    assert news_history.thin(loaded) == [theme[0].id]
    # the next run keeps what it has and asks only for what is missing
    calls.clear()
    again = news_history.collect(tmp_path, theme, first_year=2025, get=get, sleep=clock.sleep, clock=clock, today=TODAY)
    assert [c[0] for c in calls] == ["TimelineVolRaw", "TimelineVolRaw"] and again["kept"] == 2


def test_a_plain_text_limit_note_is_a_refusal_too(tmp_path):
    got = news_history.collect(tmp_path, THEMES[:1], first_year=2026, get=lambda *a, **k: Response(200, "Please limit"),
                               sleep=lambda s: None, today=TODAY)
    assert got["fetched"] == 0 and got["refused"].startswith("Please limit")
    assert not list(tmp_path.glob("*.json"))                                 # nothing half-written


def test_an_invalid_cache_file_is_fetched_again(tmp_path):
    theme = THEMES[0]
    damaged = news_history.path_for(tmp_path, theme.id, "tone", 2025)
    damaged.write_text("{", encoding="utf-8")
    for mode, year in (("tone", 2026), ("vol", 2025), ("vol", 2026)):
        news_history.path_for(tmp_path, theme.id, mode, year).write_text("{}", encoding="utf-8")
    calls = []

    def get(url, params, timeout):
        calls.append((params["mode"], params["startdatetime"][:4]))
        return Response(200, _timeline({"2025-03-02": 1.5}))

    result = news_history.collect(tmp_path, (theme,), first_year=2025, get=get, today=TODAY)
    assert calls == [("TimelineTone", "2025")]
    assert result == {"fetched": 1, "kept": 3, "missing": [], "refused": None}
    assert json.loads(damaged.read_text(encoding="utf-8")) == {"2025-03-02": 1.5}


def test_the_brief_reads_only_the_days_before_the_plan():
    tone = {f"2024-03-{d:02d}": float(d) for d in range(1, 16)}
    vol = {f"2024-03-{d:02d}": 10.0 for d in range(1, 16)}
    vol["2024-03-09"] = 40.0                    # the day before the plan: a spike
    vol["2024-03-10"] = 999.0                   # the plan's own day: must not be read
    tone["2024-03-10"] = -50.0
    history = {"chips": {"tone": tone, "vol": vol}}
    brief = news_history.brief_at(history, "SOXL", "2024-03-10")
    row = next(r for r in brief["themes"] if r["id"] == "chips")
    assert row["tone"] == pytest.approx((9 + 8 + 7) / 3, abs=0.01)
    assert row["spike"] == 4.0 and row["articles_24h"] == 40
    assert row["relevance"] == 2 and row["stale"] is False        # chips are about SOXL
    assert news_history.brief_at(None, "SOXL", "2024-03-10") is None
    assert news_history.brief_at(history, "SOXL", "2019-01-01") is None   # before the files: no brief


def test_a_replayed_brief_drives_the_same_macro_part_as_a_live_one():
    history = {"chips": {"tone": {"2024-03-07": -3.0, "2024-03-08": -3.0, "2024-03-09": -3.0},
                         "vol": {**{f"2024-03-0{d}": 10.0 for d in range(3, 9)}, "2024-03-09": 50.0}}}
    part = direction._macro_part(news_history.brief_at(history, "SOXL", "2024-03-10"))
    assert part["id"] == "macro" and part["value"] < 0


def _write(root, theme_id, first: date, days: int, tone: float, vol: float):
    by_year: dict[int, dict] = {}
    for mode, value in (("tone", tone), ("vol", vol)):
        by_year.clear()
        for i in range(days):
            d = first + timedelta(days=i)
            by_year.setdefault(d.year, {})[d.isoformat()] = value * (5 if i % 9 == 8 and mode == "vol" else 1)
        for year, series in by_year.items():
            news_history.path_for(root, theme_id, mode, year).write_text(json.dumps(series), encoding="utf-8")


def test_the_backtest_replays_the_macro_part_only_when_given_the_files(tmp_path, monkeypatch):
    from tests.test_backtest import _bars, _fetch

    daily = _bars(330)
    first = date.fromisoformat(daily[0]["at"][:10]) - timedelta(days=20)
    _write(tmp_path, "chips", first, 520, tone=-4.0, vol=10.0)
    seen: list[dict | None] = []
    build = playbook.build

    def spy(*a, **kw):
        seen.append(kw.get("macro"))
        return build(*a, **kw)

    monkeypatch.setattr(playbook, "build", spy)
    plain = backtest.run(["SOXL"], "swing", fetch=_fetch(daily), vix=None)
    assert seen and all(m is None for m in seen)
    assert any("실시간에만 있는 것" in n and "macro" in n.split("·")[-1] for n in plain.notes)
    seen.clear()
    replay = backtest.run(["SOXL"], "swing", fetch=_fetch(daily), vix=None, news_dir=str(tmp_path), macro_weight=0.5)
    assert direction.WEIGHTS["macro"] == 0.0                                  # put back after the run
    assert seen and all(m and m["as_of"] for m in seen)
    assert any("거시 항목(#390) 가중치 0.5" in n for n in replay.notes)
    assert any("뉴스 테마 과거 일부 없음" in n for n in replay.notes)          # only chips was written
    assert any(n.startswith("방향 판정 항목 중 이 백테스트가 재현한 것") and "macro" in n.split("·")[0]
               for n in replay.notes)


def test_without_files_the_results_are_as_before(tmp_path):
    from tests.test_backtest import _bars, _fetch

    daily = _bars(330)
    plain = backtest.run(["SOXL"], "swing", fetch=_fetch(daily), vix=None)
    empty = backtest.run(["SOXL"], "swing", fetch=_fetch(daily), vix=None, news_dir=str(tmp_path))
    assert any("뉴스 테마 과거 파일 없음" in n for n in empty.notes)
    assert (empty.baseline, empty.by_setup) == (plain.baseline, plain.by_setup)


def test_the_cache_dir_comes_from_the_flag_else_desk_gdelt_cache(monkeypatch):
    from desk import cli

    monkeypatch.delenv("DESK_GDELT_CACHE", raising=False)
    assert cli.gdelt_cache_dir(None) is None and cli.gdelt_cache_dir("x") == "x"
    monkeypatch.setenv("DESK_GDELT_CACHE", "/cache/gdelt")
    assert cli.gdelt_cache_dir(None) == "/cache/gdelt" and cli.gdelt_cache_dir("x") == "x"


def test_the_command_waits_out_a_refusal_and_carries_on(tmp_path, monkeypatch, capsys):
    import argparse

    from desk import cli

    results = [{"fetched": 3, "kept": 0, "missing": ["a", "b"], "refused": "429"},
               {"fetched": 2, "kept": 3, "missing": [], "refused": None}]
    dirs, waits = [], []
    monkeypatch.setattr(news_history, "collect", lambda root, **kw: dirs.append(root) or results.pop(0))
    monkeypatch.setenv("DESK_GDELT_CACHE", str(tmp_path))
    args = argparse.Namespace(out_dir=None, first_year=2017, retry_after_min=16.0, max_rounds=3)
    assert cli._news_history(args, sleep=waits.append) == 0
    assert dirs == [str(tmp_path)] * 2 and waits == [960.0]
    assert "전체 5" in capsys.readouterr().out

    # without --retry-after-min the first refusal ends the run (exit 2), as before
    results[:] = [{"fetched": 1, "kept": 0, "missing": ["a"], "refused": "429"}]
    args.retry_after_min = 0.0
    assert cli._news_history(args, sleep=waits.append) == 2 and waits == [960.0]


def test_a_short_retry_respects_the_request_gap_between_batches(tmp_path, monkeypatch):
    import argparse

    from desk import cli

    clock = Clock()
    calls = []
    collect = news_history.collect

    def get(url, params, timeout):
        calls.append(clock())
        if len(calls) == 1:
            return Response(429, "Please limit requests to one every 5 seconds")
        return Response(200, _timeline({"2026-03-02": 1.5}))

    def collect_offline(root, **kwargs):
        return collect(root, themes=THEMES[:1], get=get, sleep=clock.sleep, clock=clock, today=TODAY, **kwargs)

    monkeypatch.setattr(news_history, "collect", collect_offline)
    args = argparse.Namespace(out_dir=str(tmp_path), first_year=2026, retry_after_min=0.1, max_rounds=1)
    assert cli._news_history(args, sleep=clock.sleep) == 0
    assert calls == [0.0, 20.0, 40.0]


def test_a_batch_stops_at_max_files_without_asking_more(tmp_path):
    asked = []

    def get(url, params=None, timeout=None):
        asked.append(params)
        return Response(200, _timeline({f"{params['startdatetime'][:4]}-03-02": 1.0}))

    clock = Clock()
    got = news_history.collect(tmp_path, themes=THEMES[:1], first_year=2020, get=get, sleep=clock.sleep,
                               clock=clock, today=date(2023, 6, 1), max_files=3)
    assert got["fetched"] == 3 and len(asked) == 3 and got["refused"] is None
    assert len(got["missing"]) == 2 * 4 - 3                 # 2 modes x 2020..2023, the rest left for later
    again = news_history.collect(tmp_path, themes=THEMES[:1], first_year=2020, get=get, sleep=clock.sleep,
                                 clock=clock, today=date(2023, 6, 1), max_files=3)
    assert again["kept"] == 3 and again["fetched"] == 3      # the next batch carries on


def test_the_hourly_loop_waits_for_its_minute_and_runs_until_nothing_is_missing(tmp_path, monkeypatch, capsys):
    import argparse
    from datetime import datetime

    from desk import cli

    assert cli.seconds_to_minute(datetime(2026, 11, 17, 10, 5), 17) == 12 * 60
    assert cli.seconds_to_minute(datetime(2026, 11, 17, 10, 17), 17) == 3600          # just ran: the next hour
    results = [{"fetched": 0, "kept": 0, "missing": ["a", "b"], "refused": "429"},     # refused: wait an hour
               {"fetched": 1, "kept": 0, "missing": ["b"], "refused": None},           # batch full: wait an hour
               {"fetched": 1, "kept": 1, "missing": [], "refused": None}]
    sizes, waits = [], []
    monkeypatch.setattr(news_history, "collect", lambda root, **kw: sizes.append(kw["max_files"]) or results.pop(0))
    args = argparse.Namespace(out_dir=str(tmp_path), first_year=2017, retry_after_min=0.0, max_rounds=12,
                              max_files=8, hourly_at=17)
    assert cli._news_history(args, sleep=waits.append, clock=lambda: datetime(2026, 11, 17, 10, 5)) == 0
    assert sizes == [8, 8, 8] and waits == [720.0] * 3
    out = capsys.readouterr().out
    assert "GDELT 거절" in out and "이번 시간 8개" in out


def test_an_hourly_batch_respects_the_gap_when_the_previous_batch_ends_near_its_minute(tmp_path, monkeypatch):
    import argparse
    from datetime import datetime

    from desk import cli

    clock = Clock()
    start = datetime(2026, 11, 17, 10, 16, 55)
    results = [{"fetched": 2, "kept": 0, "missing": ["a"], "refused": None},
               {"fetched": 1, "kept": 2, "missing": [], "refused": None}]

    def collect(root, **kwargs):
        if len(results) == 2:
            clock.now += 3595
        return results.pop(0)

    monkeypatch.setattr(news_history, "collect", collect)
    args = argparse.Namespace(out_dir=str(tmp_path), first_year=2017, max_files=2, hourly_at=17)
    assert cli._news_history(args, sleep=clock.sleep, clock=lambda: start + timedelta(seconds=clock())) == 0
    assert clock.slept == [5.0, 20.0]
