"""#682 the report's candle charts: four timeframes' bars and bands, and the places several timeframes share."""

from desk import report_charts as rc
from desk import reports as rp


def _z(price, name="선"):
    return {"price": price, "low": price - 0.5, "high": price + 0.5, "kind": "support", "name": name,
            "distance_pct": 0.0, "near": True}


def test_lines_of_two_timeframes_close_together_are_one_place_and_a_lone_one_is_not():
    levels = [("1d", _z(100.0, "일봉 채널 하단")), ("60m", _z(100.6, "최근 저점")), ("240m", _z(100.9)),
              ("60m", _z(110.0)), ("1d", _z(90.0)), ("1d", _z(90.2))]       # 90 / 90.2: one timeframe twice
    got = rc.confluence(levels, close=105.0, tol=1.0)
    assert len(got) == 1
    c = got[0]
    assert c["frames"] == ["1d", "240m", "60m"] and c["count"] == 3 and c["kind"] == "support"
    assert (c["low"], c["high"]) == (99.5, 101.4)
    assert c["label"] == "일봉 · 4시간 · 1시간이 모두 가리키는 지지 99.50~101.40"
    assert c["names"][:2] == ["일봉 채널 하단", "최근 저점"]


def test_a_snapshot_keeps_four_timeframes_bars_and_bands():
    snap = rc.snapshot("^KS11", rc.fake_bars)
    assert snap and set(snap["frames"]) == set(rc.FRAMES) and snap["note"]
    for tf, f in snap["frames"].items():
        assert 30 <= len(f["bars"]) <= rc.KEEP, tf
        at, o, h, lo, c = f["bars"][-1]
        assert h >= max(o, c) and lo <= min(o, c) and isinstance(at, str)
        for z in f["zones"]:
            assert z["low"] < z["price"] < z["high"]
            assert (z["kind"] == "resistance") == (z["price"] > f["price"]), (tf, z)
    for c in snap["confluence"]:
        assert c["count"] >= 2 and c["frames"] == [tf for tf in rc.FRAMES if tf in c["frames"]]


def test_hourly_bars_are_fetched_once_and_a_missing_one_leaves_the_daily():
    asked = []

    def some(symbol, tf, count):
        asked.append(tf)
        return rc.fake_bars(symbol, tf, count)

    snap = rc.snapshot("NQ=F", some)
    assert sorted(asked) == ["1d", "60m"] and set(snap["frames"]) == set(rc.FRAMES)

    def no_hours(symbol, tf, count):
        asked.append(tf)
        if tf == "60m":
            raise RuntimeError("down")
        return rc.fake_bars(symbol, tf, count)

    asked.clear()
    snap = rc.snapshot("NQ=F", no_hours)
    assert snap and list(snap["frames"]) == ["1d"] and asked.count("60m") == 1     # not asked again for 2h / 4h
    assert rc.snapshot("NQ=F", lambda s, tf, n: []) is None


def test_hours_fold_within_an_exchange_day():
    bars = [{"at": f"2026-11-1{d}T{h:02d}:30:00+00:00", "open": 1.0 + h, "high": 2.0 + h, "low": 0.5 + h,
             "close": 1.5 + h, "volume": 1} for d in (8, 9) for h in (14, 15, 16)]
    got = rc.fold(bars, 2, lambda at: at[:10])
    assert [b["at"][:13] for b in got] == ["2026-11-18T14", "2026-11-18T16", "2026-11-19T14", "2026-11-19T16"]
    assert got[0]["open"] == 15.0 and got[0]["close"] == 16.5 and got[0]["high"] == 17.0 and got[0]["volume"] == 2


def test_the_stored_report_keeps_the_charts_of_the_symbols_its_sections_draw(db_url):
    from desk.db import make_engine, migrate

    migrate(db_url)
    engine = make_engine(db_url)
    desk = rp.Reports(engine, rp.fake_fetch, every_days=0)
    got = desk.make(kind="daily")
    assert got is not None
    drawn = rc.symbols_of(got)
    assert drawn and set(got["charts"]) == set(drawn)
    again = desk.get(got["id"])
    assert again["charts"][drawn[0]]["frames"]["1d"]["bars"] == got["charts"][drawn[0]]["frames"]["1d"]["bars"]
    # the list reads only what it shows - never the charts
    listed = desk.list()["items"][0]
    assert listed["id"] == got["id"] and listed["title"] == got["title"] and listed["subject"] == got["subject"]
    assert "charts" not in listed and listed["summary"] == got.get("summary")
    assert got.get("slot") is None and desk.recent_subjects()                # the rotation still reads the kind
    # a chart failing for every symbol still keeps the report, without charts
    broken = rp.Reports(engine, rp.fake_fetch, every_days=0, bars=lambda s, tf, n: (_ for _ in ()).throw(RuntimeError("x")))
    plain = broken.make(kind="daily")
    assert plain is not None and plain["charts"] == {} and "charts" not in broken.get(plain["id"])
    engine.dispose()


def test_only_the_symbols_a_section_draws_and_at_most_three():
    sections = [{"chart": {"kind": "levels", "symbol": s}} for s in ("A", "B", "A", "C", "D")]
    sections.append({"chart": {"kind": "none", "symbol": "E"}})
    assert rc.symbols_of({"sections": sections}) == ["A", "B", "C"]
