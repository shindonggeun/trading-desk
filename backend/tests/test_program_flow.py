"""#743 program trading in the intraday flow bars and its buy / sell turns as market alerts."""

from datetime import UTC, date, datetime, timedelta

from desk import alert_tone, flow_intraday, market_alerts, program_flow


def _at(h, m):                       # Tuesday 2026-11-10 KST
    return (datetime(2026, 11, 10, h, m, tzinfo=UTC) - timedelta(hours=9)).isoformat()


def _p(h, m, total, non_arb=None, foreign=-500.0, market="kospi"):
    non_arb = total if non_arb is None else non_arb
    other = "kosdaq" if market == "kospi" else "kospi"
    prog = None if total is None else {"total": total, "non_arbitrage": non_arb, "arbitrage": total - non_arb}
    return {"at": _at(h, m),
            "spot": {market: {"foreign": foreign, "institution": None if foreign is None else 10.0,
                              "individual": None if foreign is None else -10.0},
                     other: {"foreign": None, "institution": None, "individual": None}},
            "program": {market: prog or {"total": None, "non_arbitrage": None, "arbitrage": None},
                        other: {"total": None, "non_arbitrage": None, "arbitrage": None}}}


def _day(values, start=(9, 0), non_arb=None, **kw):
    h, m = start
    out = []
    for i, v in enumerate(values):
        t = h * 60 + m + i
        out.append(_p(t // 60, t % 60, v, None if non_arb is None else non_arb[i], **kw))
    return out


SELL_THEN_BUY = [0, -200, -600, -700, -650, -300, 100, 520, 600, 700]     # 09:00 ~ 09:09, the band 500


def test_the_total_and_the_non_arbitrage_turning_together_are_one_alert():
    got = program_flow.judge(_day(SELL_THEN_BUY))
    assert len(got) == 1
    a = got[0]
    assert (a["kind"], a["rule"], a["market"], a["who"], a["severity"]) == (
        "program_alert", "buy_turn", "kospi", "prog_total", "caution")
    assert a["both"] and a["t"].startswith("2026-11-10T09:09") and a["value"] == 700.0 and a["prev"] == -500.0
    assert program_flow.text(a) == ("🔴 코스피 프로그램 전체 · 비차익 매수 전환 · 09:09 · 누적 +700억 · "
                                    "외인 현물 −500억 · 외인과 반대")


def test_the_non_arbitrage_alone_goes_under_its_short_code():
    flat = [0, -100, -200, -300, -200, -100, 0, 100, 200, 300]                 # the total never past 500
    got = program_flow.judge(_day(flat, non_arb=SELL_THEN_BUY))
    assert [(a["who"], a["rule"]) for a in got] == [("prog_nonarb", "buy_turn")]
    assert "프로그램 비차익 매수 전환" in program_flow.text(got[0])


def test_alert_codes_fit_the_market_alerts_column():
    from desk.db import market_alerts as table

    limit = table.c.who.type.length
    assert all(len(code) <= limit for code in program_flow.ALERT_WHO.values())   # PostgreSQL enforces it


def test_a_wobble_inside_the_band_or_too_short_a_stay_is_no_turn():
    assert program_flow.judge(_day([0, -600, -700, -650, 300, 450, 100, 520, 530, -100, 200])) == []
    assert program_flow.judge(_day([0, -600, -700, -650, 520, 600, 100])) == []   # two past the band only


def test_no_turn_before_0905_and_none_found_long_ago():
    assert program_flow.judge(_day([-600, -700, -650, 520, 600, 700], start=(8, 58))) == []   # at 09:03
    assert program_flow.judge(_day(SELL_THEN_BUY + [710] * 11)) == []          # found 09:09, now 09:20


def test_a_minute_without_toss_still_counts_and_one_without_kis_neither_breaks_nor_counts():
    points = _day([0, -600, -700, -650, 520, 530, 540, 560])
    points[5] = _p(9, 5, 530, foreign=None)                                    # Toss missed 09:05: KIS' figure stays
    got = program_flow.judge(points)
    assert [a["t"][11:16] for a in got] == ["09:06"]
    points = _day([0, -600, -700, -650, 520, 530, 540, 560])
    points[5] = _p(9, 5, None)                                                 # KIS missed 09:05
    assert [a["t"][11:16] for a in program_flow.judge(points)] == ["09:07"]
    bars = flow_intraday.bars(_day([0, 100]) + [_p(9, 2, 150, foreign=None)], "kospi", 1)
    assert [b["program_total"] for b in bars] == [0.0, 100.0, 150.0] and bars[-1]["foreign"] is None


def test_kosdaq_has_its_own_band():
    got = program_flow.judge(_day([0, -200, -210, -220, 160, 170, 180], market="kosdaq", foreign=40.0))
    assert [(a["market"], a["rule"], a.get("both")) for a in got] == [("kosdaq", "buy_turn", True)]
    assert program_flow.text(got[0]).endswith("외인 현물 +40억 · 외인과 같은 방향")


def test_bands_wait_for_enough_days_and_keep_a_floor():
    few = program_flow.bands([_day([i * 10 for i in range(40)])] * 3)
    assert few["band"] == program_flow.DEFAULT and few["days"][("kospi", "program_total")] == 3
    big = [_day([i * 100 for i in range(60)]) for _ in range(program_flow.MIN_DAYS)]     # +3000 per 30 minutes
    got = program_flow.bands(big)
    assert got["band"][("kospi", "program_total")] == 3000.0
    assert got["band"][("kosdaq", "program_total")] == program_flow.DEFAULT[("kosdaq", "program_total")]
    calm = [_day([i for i in range(60)]) for _ in range(program_flow.MIN_DAYS)]          # +30 per 30 minutes
    assert program_flow.bands(calm)["band"][("kospi", "program_total")] == 250.0         # half the default


def test_the_chart_marks_what_the_alerts_send_on_the_days_band():
    points = _day([0, -350, -360, -370, -100, 350, 360, 370, 380])
    band = {**program_flow.DEFAULT, ("kospi", "program_total"): 300.0, ("kospi", "program_non_arbitrage"): 300.0}
    alerts = program_flow.judge(points, band)
    view = flow_intraday.view(points, 1, band)
    assert [a["t"] for a in alerts] == [t["t"] for t in view["program_turns"] if t["who"] == "program_total"]
    assert flow_intraday.view(points, 1)["program_turns"] == []                 # the default band: no turn
    late = _day([0, -600, -700, -650, 520, 600, 700], start=(15, 15))           # the turn at 15:20 (UNTIL)
    assert flow_intraday.view(late, 1)["program_turns"] == [] and program_flow.judge(late) == []


def test_bands_for_reads_once_a_day_and_logs_it(caplog):
    import logging

    calls = []

    class History:
        def daily(self, n):
            calls.append(n)
            return [{"date": "2026-11-09"}, {"date": "2026-11-10"}]

        def intraday(self, d):
            return _day([i * 10 for i in range(40)])

    program_flow._cache.clear()
    with caplog.at_level(logging.INFO, logger="desk.program_flow"):
        got = program_flow.bands_for(History(), date(2026, 11, 10))
        assert program_flow.bands_for(History(), date(2026, 11, 10)) is got
    assert calls == [program_flow.HISTORY_DAYS + 1] and got["days"][("kospi", "program_total")] == 1
    assert "program bands 2026-11-10: kospi total 500(default, 1 days)" in caplog.text
    for d in range(11, 16):
        program_flow.bands_for(History(), date(2026, 11, d))
    assert len(program_flow._cache) <= program_flow.KEEP_CACHED
    program_flow._cache.clear()


def test_the_spot_bars_carry_the_program_and_the_view_its_turns():
    points = _day(SELL_THEN_BUY)
    bars = flow_intraday.bars(points, "kospi", 5)
    assert [b["program_total"] for b in bars] == [0.0, -300.0, 700.0]          # 09:00 · 09:05 · 09:10 bars
    assert bars[1]["program_total_delta"] == -300.0 and bars[2]["program_total_delta"] == 1000.0
    assert bars[-1]["program_non_arbitrage"] == 700.0 and bars[-1]["program_arbitrage"] == 0.0
    assert flow_intraday.bars(points, "futures", 5) == []
    view = flow_intraday.view(points, 1)
    assert [t["who"] for t in view["program_turns"]] == ["program_total", "program_non_arbitrage"]
    assert view["turns"] == []                                                 # the foreign stayed on one side


def test_program_alerts_are_capped_per_rule_and_toned():
    def alert(m, rule="buy_turn", who="prog_total"):
        return {"kind": "program_alert", "rule": rule, "market": "kospi", "who": who, "severity": "caution",
                "t": _at(10, m)}

    day = [alert(m) for m in (0, 10, 20)]
    assert not market_alerts.admit(alert(30), day)                             # PROGRAM_MAX buy turns already
    assert market_alerts.admit(alert(30, rule="sell_turn"), day)
    assert market_alerts.admit(alert(30, who="prog_nonarb"), day)
    assert market_alerts.admit(alert(1), [alert(0)])                           # a turn skips the cooldown
    assert not market_alerts.admit(alert(0), [alert(0)])                       # the same moment once
    assert alert_tone.tone(alert(0)) == "positive" and alert_tone.tone(alert(0, "sell_turn")) == "caution"
    head = market_alerts.text({**alert(0), "value": 800.0, "prev": 300.0})
    assert "코스피 프로그램 전체 매수 전환" in head and "프로그램 매수 유입" in head
    assert market_alerts.TITLE["program_alert"] == "프로그램매매 전환"


def test_program_alerts_are_recorded_once_and_a_refused_one_costs_no_other(db_url, monkeypatch):
    from desk.db import make_engine, migrate

    migrate(db_url)
    engine = make_engine(db_url)
    sent = []
    monkeypatch.setattr(market_alerts.notify, "send", lambda *a, **k: sent.append(k["payload"]["body"]))
    alerts = market_alerts.MarketAlerts(engine)
    nonarb = program_flow.judge(_day([0, -100, -200, -300, -200, -100, 0, 100, 200, 300], non_arb=SELL_THEN_BUY))
    both = program_flow.judge(_day(SELL_THEN_BUY))
    assert len(alerts.dispatch(nonarb + both)) == 2                            # the non-arbitrage code fits (PG too)
    assert alerts.dispatch(nonarb + both) == []                                # judged again a minute later: nothing
    assert {(a["who"], a["kind"]) for a in alerts.list("2026-11-10")} == {("prog_nonarb", "program_alert"),
                                                                           ("prog_total", "program_alert")}
    assert len(sent) == 2
    # a row the database refuses (here: a who past the column) is logged and skipped, the next one goes in
    shift = {"kind": "flow_shift", "rule": "buy_grow", "market": "kospi", "who": "foreign", "severity": "caution",
             "t": _at(11, 0), "value": 900.0, "prev": 100.0}
    calls = []
    real = market_alerts.insert

    def refusing(table):
        calls.append(1)
        if len(calls) == 1:
            from sqlalchemy.exc import DataError

            raise DataError("INSERT", {}, Exception("value too long for type character varying(16)"))
        return real(table)

    monkeypatch.setattr(market_alerts, "insert", refusing)
    bad = {**nonarb[0], "t": _at(10, 30), "rule": "sell_turn"}
    assert [a["kind"] for a in alerts.dispatch([bad, shift])] == ["flow_shift"]
    engine.dispose()


def test_program_alert_is_a_push_kind():
    from desk import push

    assert "program_alert" in push.KINDS
