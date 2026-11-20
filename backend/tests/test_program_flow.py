"""#743 program trading in the intraday flow bars and its buy / sell turns as market alerts."""

from datetime import UTC, datetime, timedelta

from desk import alert_tone, flow_intraday, market_alerts, program_flow


def _at(h, m):                       # Tuesday 2026-11-10 KST
    return (datetime(2026, 11, 10, h, m, tzinfo=UTC) - timedelta(hours=9)).isoformat()


def _p(h, m, total, non_arb=None, foreign=-500.0, market="kospi"):
    non_arb = total if non_arb is None else non_arb
    other = "kosdaq" if market == "kospi" else "kospi"
    return {"at": _at(h, m),
            "spot": {market: {"foreign": foreign, "institution": 10.0, "individual": -10.0},
                     other: {"foreign": None, "institution": None, "individual": None}},
            "program": {market: {"total": total, "non_arbitrage": non_arb, "arbitrage": total - non_arb},
                        other: {"total": None, "non_arbitrage": None, "arbitrage": None}}}


def _day(values, start=(9, 0), **kw):
    h, m = start
    out = []
    for i, v in enumerate(values):
        t = h * 60 + m + i
        out.append(_p(t // 60, t % 60, v, **kw))
    return out


SELL_THEN_BUY = [0, -200, -600, -700, -650, -300, 100, 520, 600, 700]     # 09:00 ~ 09:09, the band 500


def test_a_sell_to_buy_turn_is_found_once_with_the_foreign_beside_it():
    got = program_flow.judge(_day(SELL_THEN_BUY))
    assert [(a["rule"], a["who"]) for a in got] == [("buy_turn", "program_total"), ("buy_turn", "program_non_arbitrage")]
    a = got[0]
    assert a["kind"] == "program_alert" and a["market"] == "kospi" and a["severity"] == "caution"
    assert a["t"].startswith("2026-11-10T09:09") and a["value"] == 700.0 and a["prev"] == -500.0
    assert program_flow.text(a) == "🔴 코스피 프로그램 전체 매수 전환 · 09:09 · 누적 +700억 · 외인 현물 −500억 · 엇갈림"


def test_a_wobble_inside_the_band_or_too_short_a_stay_is_no_turn():
    assert program_flow.judge(_day([0, -600, -700, -650, 300, 450, 100, 520, 530, -100, 200])) == []
    # past the band on the other side but for two snapshots only (HOLD is three)
    assert program_flow.judge(_day([0, -600, -700, -650, 520, 600, 100])) == []


def test_no_turn_before_0905_and_none_found_long_ago():
    early = _day([-600, -700, -650, 520, 600, 700], start=(8, 58))       # the turn at 09:03
    assert program_flow.judge(early) == []
    old = _day(SELL_THEN_BUY + [710] * 11)                               # found at 09:09, now 09:20
    assert program_flow.judge(old) == []


def test_kosdaq_has_its_own_band():
    got = program_flow.judge(_day([0, -200, -210, -220, 160, 170, 180], market="kosdaq", foreign=40.0))
    assert [(a["market"], a["rule"]) for a in got] == [("kosdaq", "buy_turn"), ("kosdaq", "buy_turn")]
    assert program_flow.text(got[0]).endswith("외인 현물 +40억 · 둘 다 매수")


def test_bands_wait_for_enough_days():
    few = program_flow.bands([_day([i * 10 for i in range(40)])] * 3)
    assert few["band"] == program_flow.DEFAULT and few["days"][("kospi", "program_total")] == 3
    big = [_day([i * 100 for i in range(60)]) for _ in range(program_flow.MIN_DAYS)]   # +3000 per 30 minutes
    got = program_flow.bands(big)
    assert got["band"][("kospi", "program_total")] == 3000.0
    assert got["band"][("kosdaq", "program_total")] == program_flow.DEFAULT[("kosdaq", "program_total")]


def test_the_spot_bars_carry_the_program_and_the_view_its_turns():
    points = _day(SELL_THEN_BUY)
    bars = flow_intraday.bars(points, "kospi", 5)
    assert [b["program_total"] for b in bars] == [0.0, -300.0, 700.0]          # 09:00 · 09:05 · 09:10 bars
    assert bars[1]["program_total_delta"] == -300.0 and bars[2]["program_total_delta"] == 1000.0
    assert bars[-1]["program_non_arbitrage"] == 700.0 and bars[-1]["program_arbitrage"] == 0.0
    assert flow_intraday.bars(points, "futures", 5) == []                     # no futures in these points
    view = flow_intraday.view(points, 1)
    assert [t["who"] for t in view["program_turns"]] == ["program_total", "program_non_arbitrage"]
    assert [t["who"] for t in view["turns"]] == []                       # the foreign stayed on one side


def test_program_alerts_are_capped_per_rule_and_toned():
    def alert(m, rule="buy_turn", who="program_total"):
        return {"kind": "program_alert", "rule": rule, "market": "kospi", "who": who, "severity": "caution",
                "t": _at(10, m)}

    day = [alert(m) for m in (0, 10, 20)]
    assert not market_alerts.admit(alert(30), day)                       # PROGRAM_MAX buy turns already
    assert market_alerts.admit(alert(30, rule="sell_turn"), day)
    assert market_alerts.admit(alert(30, who="program_non_arbitrage"), day)
    assert market_alerts.admit(alert(1), [alert(0)])                     # a turn skips the cooldown
    assert alert_tone.tone(alert(0)) == "positive" and alert_tone.tone(alert(0, "sell_turn")) == "caution"
    head = market_alerts.text({**alert(0), "value": 800.0, "prev": 300.0})
    assert "코스피 프로그램 전체 매수 전환" in head and "프로그램 매수 유입" in head
    assert market_alerts.TITLE["program_alert"] == "프로그램매매 전환"


def test_program_alert_is_a_push_kind():
    from desk import push

    assert "program_alert" in push.KINDS
