"""#514: the halving cycles from daily closes - the peak (within two years of the halving), the bottom after it,
the days, the partial 2012 cycle, where today stands against the past cycles, and the API."""

from datetime import date, timedelta
from itertools import pairwise

from desk import halving as hv


def _line(start: date, prices: list[float]) -> list[tuple[date, float]]:
    return [(start + timedelta(days=i), p) for i, p in enumerate(prices)]


def _cycle_prices(days: int, peak_day: int, bottom_day: int, peak=100.0, bottom=20.0, base=10.0, late=None):
    out = []
    for i in range(days):
        if i <= peak_day:
            p = base + (peak - base) * i / peak_day
        elif i <= bottom_day:
            p = peak + (bottom - peak) * (i - peak_day) / (bottom_day - peak_day)
        else:
            p = bottom + ((late or bottom * 2) - bottom) * (i - bottom_day) / max(1, days - bottom_day - 1)
        out.append(round(p, 4))
    return out


def test_a_cycles_peak_bottom_and_days():
    start, end = date(2016, 7, 9), date(2020, 5, 11)
    closes = _line(start, _cycle_prices((end - start).days, 525, 889))
    c = hv.cycle(closes, start, end)
    assert c["partial"] is False and c["end"] == "2020-05-10"
    assert c["peak"] == {"date": "2017-12-16", "price": 100.0, "days_from_halving": 525}
    assert c["bottom"]["days_from_halving"] == 889 and c["bottom"]["days_from_peak"] == 364
    assert c["bottom"]["drawdown_pct"] == -80.0


def test_the_rally_into_the_next_halving_is_not_the_peak():
    start, end = date(2020, 5, 11), date(2024, 4, 20)
    closes = _line(start, _cycle_prices((end - start).days, 546, 924, late=150.0))   # ends above the 2021 top
    c = hv.cycle(closes, start, end)
    assert c["peak"]["days_from_halving"] == 546 and c["bottom"]["days_from_halving"] == 924


def test_a_cycle_whose_prices_start_late_has_no_peak_and_says_so():
    start, end = date(2012, 11, 28), date(2016, 7, 9)
    first = date(2014, 9, 17)
    closes = _line(first, [400 - i * 0.5 if i < 119 else 340 + i * 0.1 for i in range((end - first).days)])
    c = hv.cycle(closes, start, end)
    assert c["partial"] is True and c["peak"] is None and c["data_from"] == "2014-09-17"
    assert c["bottom"]["date"] == "2015-01-13" and c["bottom"]["days_from_halving"] == 776
    assert c["bottom"]["days_from_peak"] is None and c["bottom"]["drawdown_pct"] is None and "자료 부족" in c["note"]
    assert hv.cycle([], start, end)["note"] == "이 사이클의 가격 자료가 없어요."


def _history(today_day=941):
    closes = []
    starts = [h[2] for h in hv.HALVINGS]
    shapes = {1: (None, 777), 2: (525, 889), 3: (546, 924)}
    for i, s in enumerate(starts[:-1]):
        n = (starts[i + 1] - s).days
        peak, bottom = shapes[i + 1]
        prices = _cycle_prices(n, peak or 300, bottom)
        closes += _line(s, prices)
    closes = [(d, p) for d, p in closes if d >= date(2014, 9, 17)]
    closes += _line(starts[-1], _cycle_prices(today_day + 1, 534, 801, late=55.0))
    return closes


def test_today_against_the_past_cycles_with_the_limits_said():
    v = hv.view(_history())
    assert [c["bottom"]["days_from_halving"] for c in v["cycles"]] == [777, 889, 924]
    assert v["cycles"][0]["peak"] is None
    r = v["ranges"]
    assert r["bottom_days_from_halving"] == {"min": 777, "max": 924, "cycles": ["2012-11-28", "2016-07-09", "2020-05-11"]}
    assert r["bottom_days_from_peak"]["cycles"] == ["2016-07-09", "2020-05-11"]           # 2012 has no peak
    cur = v["current"]
    assert cur["days_since_halving"] == 941 and cur["high"]["days_from_halving"] == 534
    assert cur["low_after_high"]["days_from_halving"] == 801 and cur["from_low_pct"] > 0
    assert r["now"]["days_since_halving"] == "after" and r["now"]["from_high_pct"] == "shallower"
    assert v["halvings"][-1] == {"n": 5, "block": 1_050_000, "date": "2028-03-30", "reward_after": 1.5625,
                                 "estimated": True}
    assert any("3개뿐" in n for n in v["notes"]) and any("2014-09-17" in n for n in v["notes"])
    first = v["series"]["by_halving"][0]
    # x counts from the halving even when prices start later (#514 contract): 2014-09-17 is day 658
    assert first["halving"] == "2012-11-28" and first["base_date"] == "2014-09-17" and first["points"][0][1] == 1.0
    assert first["points"][0][0] == (date(2014, 9, 17) - date(2012, 11, 28)).days
    assert all(x % hv.STEP_DAYS == 0 for x, _ in first["points"][1:-1])
    late = hv._series(_line(date(2014, 9, 20), [1.0, 2.0, 3.0] * 10), date(2012, 11, 28), None, date(2012, 11, 28))[0]
    assert late[0] == [661, 1.0] and all(x % hv.STEP_DAYS == 0 for x, _ in late[1:-1])   # 661 is not a multiple of 7
    now = v["series"]["by_halving"][-1]["points"]
    assert all(b[0] - a[0] == hv.STEP_DAYS for a, b in pairwise(now[:-1])) and now[-1][0] == 941
    peak = v["series"]["by_peak"][0]
    assert peak["halving"] == "2016-07-09" and [0, 1.0] in peak["points"] and peak["points"][0][0] < 0
    for series in v["series"]["by_peak"] + v["series"]["by_halving"]:
        xs = [x for x, _ in series["points"]]
        assert xs == sorted(set(xs))                                      # one point a day, in order


def test_one_close_a_day_and_a_peak_on_the_last_day_has_no_bottom_yet():
    start, end = date(2016, 7, 9), date(2020, 5, 11)
    closes = _line(start, [float(i + 1) for i in range(400)])            # still rising: the peak is the last day
    c = hv.cycle(closes, start, end)
    assert c["peak"]["days_from_halving"] == 399 and c["bottom"] is None
    twice = _history() + [(_history()[-1][0], 1.0)]                       # a second row for the last day
    v = hv.view(twice, today=_history()[-1][0] + timedelta(days=1))
    assert v["current"]["price"] == 1.0 and v["current"]["days_since_halving"] == 942
    assert v["current"]["days_since_high"] == 942 - 534


def test_the_cache_keeps_a_day_and_retries_a_failure_soon():
    t = [0.0]
    calls = []

    def fetch():
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError
        return _history()
    cache = hv.Cache(fetch, clock=lambda: t[0])
    assert cache.get() is None and cache.error == "TimeoutError"
    t[0] += hv.FAIL_TTL_SEC + 1
    assert cache.get()["current"]["days_since_halving"] == 941
    t[0] += 3600
    cache.get()
    assert len(calls) == 2


def test_the_api_gives_the_cycles(db_url):
    from tests.test_api import _client

    with _client(db_url) as client:
        got = client.get("/api/crypto/halving")
        assert got.status_code == 200
        body = got.json()
        assert len(body["cycles"]) == 3 and body["current"]["halving"] == "2024-04-20"
        assert set(body["series"]) == {"by_halving", "by_peak"}
        client.cookies.clear()
        assert client.get("/api/crypto/halving").status_code == 401


def test_null_only_when_the_prices_are_cut_short():
    # nothing since the last halving: no current cycle (the past cycles still answer)
    before = [(d, p) for d, p in _history() if d < date(2024, 4, 20)]
    v = hv.view(before)
    assert v["current"] is None and v["ranges"]["now"] == {"days_since_halving": None, "days_since_high": None,
                                                         "from_high_pct": None}
    assert v["ranges"]["bottom_days_from_halving"]["cycles"] == ["2012-11-28", "2016-07-09", "2020-05-11"]
    # prices only from the current cycle: no past bottom to compare with - every range and its verdict null
    recent = [(d, p) for d, p in _history() if d >= date(2024, 4, 20)]
    v = hv.view(recent)
    assert v["current"]["days_since_halving"] == 941
    assert all(v["ranges"][k] is None for k in ("bottom_days_from_halving", "bottom_days_from_peak", "bottom_drawdown_pct"))
    assert v["ranges"]["now"] == {"days_since_halving": None, "days_since_high": None, "from_high_pct": None}
    assert all(c["bottom"] is None and c["peak"] is None for c in v["cycles"])
    # a high on the last day: nothing after it yet
    rising = _line(date(2024, 4, 20), [float(i + 1) for i in range(300)])
    cur = hv.view(rising)["current"]
    assert cur["low_after_high"] is None and cur["from_low_pct"] is None and cur["days_since_high"] == 0
