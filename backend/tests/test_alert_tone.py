"""#641 an alert's tone - by direction, beside its size: the table, the wording, the daily cap, the switches."""

import pytest

from desk import alert_tone as at
from desk import market_alerts, push

CASES = [
    # (rule, who, severity) -> tone, on every market (spot and futures alike)
    ("buy_turn", "foreign", "caution", "positive"), ("buy_turn", "institution", "caution", "positive"),
    ("buy_turn", "individual", "caution", "neutral"),
    ("sell_turn", "foreign", "caution", "warning"), ("sell_turn", "institution", "caution", "warning"),
    ("sell_turn", "individual", "caution", "neutral"),
    ("buy_grow", "foreign", "caution", "positive"), ("buy_grow", "institution", "strong", "positive"),
    ("buy_grow", "foreign", "info", "neutral"),
    ("sell_grow", "foreign", "caution", "caution"), ("sell_grow", "institution", "strong", "warning"),
    ("sell_grow", "foreign", "info", "neutral"),
    # a shrink is always "info" by size (desk.flow_shift._pace) - its direction is the news
    ("buy_shrink", "foreign", "info", "caution"), ("buy_shrink", "institution", "info", "caution"),
    ("sell_shrink", "foreign", "info", "positive"), ("sell_shrink", "institution", "info", "positive"),
    ("buy_shrink", "individual", "info", "neutral"),
    ("open_sell_reversal", "foreign", "caution", "positive"), ("open_buy_reversal", "institution", "caution", "warning"),
    ("sell_grow", "individual", "strong", "neutral"),
]


@pytest.mark.parametrize("market", ["kospi", "kosdaq", "futures"])
@pytest.mark.parametrize(("rule", "who", "severity", "want"), CASES)
def test_the_tone_table(market, rule, who, severity, want):
    alert = {"kind": "flow_shift", "rule": rule, "market": market, "who": who, "severity": severity}
    assert at.tone(alert) == want
    assert at.kind_of(alert) == f"tone_{want}"


def test_a_foreign_turn_and_nq_alarms():
    assert at.tone({"kind": "flow_turn", "rule": "buy_turn", "who": "foreign", "severity": "caution"}) == "positive"
    assert at.tone({"kind": "flow_turn", "rule": "sell_turn", "who": "foreign", "severity": "caution"}) == "warning"
    nq = {"kind": "nq_alert", "who": "all"}
    assert at.tone({**nq, "rule": "nq_drop", "severity": "caution"}) == "warning"
    assert at.tone({**nq, "rule": "nq_from_high", "severity": "caution"}) == "caution"


def test_the_headline_puts_the_mark_first_and_the_reading_after():
    alert = {"kind": "flow_shift", "rule": "buy_grow", "who": "foreign", "severity": "caution"}
    assert at.headline(alert, "🔴 외인 코스피 현물 매수 확대 · 10:42 · 최근 30분 +1,250억") == (
        "🟢 호재 · 외인 코스피 현물 매수 확대 · 10:42 · 최근 30분 +1,250억\n외인 매수 유입 — 지수에 우호적")
    indiv = {**alert, "who": "individual"}
    assert at.headline(indiv, "개인 선물 매수 확대").startswith("⚪ 참고 · 개인 선물 매수 확대\n개인 흐름")
    shrink = {**alert, "rule": "sell_shrink"}
    assert at.reading(shrink) == "외인 매도 압력 완화 — 지수에 우호적"
    assert at.reading({**alert, "rule": "sell_grow", "severity": "strong"}) == "외인 강한 매도 — 하락 압력 경계"


def _alert(t, rule="sell_grow", who="institution", severity="caution"):
    return {"kind": "flow_shift", "rule": rule, "market": "kospi", "who": who, "severity": severity,
            "t": f"2026-11-18T{t}:00+09:00"}


def test_past_the_daily_cap_a_warning_still_goes_through():
    # DAILY_MAX pushed alerts already: a caution waits, a warning (buying turned to selling) goes
    day = [_alert(f"{9 + i // 2:02d}:{(i % 2) * 30:02d}", rule=f"r{i}") for i in range(market_alerts.DAILY_MAX)]
    late = "15:05"
    assert not market_alerts.admit(_alert(late, "sell_grow"), day)
    assert market_alerts.admit(_alert(late, "open_buy_reversal"), day)


def test_the_channels_and_devices_can_turn_a_tone_off():
    assert set(at.KINDS) <= set(push.KINDS)


def test_flow_shift_gives_shrinks_info_so_the_tone_does_not_lean_on_severity():
    import inspect

    from desk import flow_shift

    src = inspect.getsource(flow_shift._pace)
    assert '_alert("buy_shrink", market, who, at, recent, prev, "info")' in src
    assert '_alert("sell_shrink", market, who, at, recent, prev, "info")' in src


def test_a_resubscribe_that_leaves_out_a_tone_keeps_it(db_url):
    from tests.test_push import _store_with_sub

    engine = _store_with_sub(db_url)                                   # the owner, one device subscribed
    store = push.PushStore(engine)
    sub = store.for_kind("flow")[0]
    url, keys, user = sub["endpoint"], sub["keys"], sub["user_id"]
    store.subscribe(user, url, keys, "폰", {"tone_positive": False})
    got = store.subscribe(user, url, keys, "폰", {"flow_kospi": False})     # a page that knows no tone switch
    assert got["kinds"]["tone_positive"] is False and got["kinds"]["flow_kospi"] is False
    assert got["kinds"]["tone_warning"] is True and got["kinds"]["flow"] is True
    assert store.subscribe(user, url, keys, "폰", {"tone_positive": True})["kinds"]["tone_positive"] is True
    engine.dispose()
