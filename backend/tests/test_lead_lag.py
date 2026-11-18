"""#645 lead / lag of the intraday flows: planted relations come out as planted, nothing read early."""

import random
from datetime import UTC, date, datetime, time, timedelta

import pytest

from desk import lead_lag as ll
from desk.market_calendar import KST

FIRST = date(2026, 9, 1)


def _days(n: int, seed: int = 1, bucket: int = 5, rho: float = 0.0) -> dict[date, list[dict]]:
    """Synthetic minute snapshots 08:45-15:45: 5-minute bucket returns r[b]; foreign futures move with
    the NEXT bucket's return (lead), institution futures with the same bucket's (sync), individual
    futures with the bucket before's (lag), the spot foreigners at random, updated every 5 minutes."""
    rng = random.Random(seed)
    out, d = {}, FIRST
    while len(out) < n:
        if d.weekday() >= 5:
            d += timedelta(days=1)
            continue
        start = datetime.combine(d, time(8, 45), KST)
        minutes = 7 * 60
        nb = minutes // bucket + 2
        r = [rng.gauss(0, 0.002)]
        for _ in range(nb + 1):                                # rho: the bucket returns' autocorrelation
            r.append(rho * r[-1] + rng.gauss(0, 0.002))
        price, rows = 400.0, []
        fut = {"foreign": 0.0, "institution": 0.0, "individual": 0.0}
        spot = 0.0
        for m in range(minutes + 1):
            b, inb = divmod(m, bucket)
            if inb == 0 and m:
                done = b - 1                                   # the bucket that just ended
                fut["foreign"] += 1e5 * r[done + 1] + rng.gauss(0, 30)
                fut["institution"] += 1e5 * r[done] + rng.gauss(0, 30)
                fut["individual"] += 1e5 * r[done - 1] + rng.gauss(0, 30) if done else 0.0
                spot += rng.gauss(0, 50)
            rows.append({"at": (start + timedelta(minutes=m)).astimezone(UTC), "trade_date": d.isoformat(),
                         "price": round(price, 4), "market_basis": 0.5,
                         "foreign_contracts": round(fut["foreign"]), "institution_contracts": round(fut["institution"]),
                         "individual_contracts": round(fut["individual"]), "kospi_foreign_100m": round(spot, 1),
                         "kospi_institution_100m": None, "kospi_individual_100m": None})
            price *= 1 + r[b] / bucket                         # the minute after this snapshot moves it
        out[d] = rows
        d += timedelta(days=1)
    return out


@pytest.fixture(scope="module")
def days():
    return _days(25)


def _row(rows, indicator, target="futures", bucket=5):
    return next(r for r in rows if r["indicator"] == indicator and r["target"] == target and r["bucket_min"] == bucket)


def test_planted_lead_sync_and_lag_come_out_as_planted(days):
    rows = ll.table(days, buckets=(5,))
    lead = _row(rows, "foreign_futures")
    assert lead["verdict"] == "선행" and lead["lead"]["corr"] > 0.5 and lead["hit_rate"] > lead["momentum_hit_rate"]
    assert _row(rows, "institution_futures")["verdict"] == "동행"
    assert _row(rows, "individual_futures")["verdict"] == "후행"
    assert _row(rows, "foreign_spot")["verdict"] == "관계 없음"
    assert _row(rows, "momentum")["verdict"] == "기준선"
    assert _row(rows, "foreign_futures", "kospi200")["verdict"] == "선행"   # price - basis moves the same
    assert _row(rows, "basis", "kospi200")["verdict"] == "정의상 연동"     # the index is price - basis
    empty = _row(rows, "institution_spot")
    assert empty["samples"] == 0 and empty["verdict"] == "표본 부족"


def test_few_days_are_not_judged():
    rows = ll.table(_days(12), buckets=(5,))
    assert {r["verdict"] for r in rows} == {"표본 부족", "기준선", "정의상 연동"}


def test_a_flow_that_only_moves_with_a_mean_reverting_price_is_not_leading():
    # #645 review: with reverting returns, a same-bucket flow correlates with the next bucket through the
    # price alone - net of momentum it tells nothing, so it is "동행", never "선행"
    for seed in (1, 2, 3):
        rows = ll.table(_days(25, seed=seed, rho=-0.3), buckets=(5,))
        assert _row(rows, "institution_futures")["verdict"] == "동행"
        assert _row(rows, "foreign_futures")["verdict"] == "선행"           # the planted lead still is
        assert _row(rows, "individual_futures")["verdict"] == "후행"        # and the planted lag


def test_noise_is_rarely_called_anything():
    calls = 0
    for seed in range(1, 6):
        rows = ll.table(_days(25, seed=seed), buckets=(5,))
        calls += sum(_row(rows, "foreign_spot", t)["verdict"] != "관계 없음" for t in ll.TARGETS)
    assert calls <= 1


def test_the_grid_reads_each_value_as_of_its_time_and_a_gap_is_missing():
    day = date(2026, 9, 1)
    at = lambda h, m, s=0: datetime.combine(day, time(h, m, s), KST)
    rows = [{"at": at(8, 59, 30), "x": 1}, {"at": at(9, 0, 30), "x": 2}, {"at": at(9, 4, 0), "x": 3},
            {"at": at(9, 20), "x": 4}]
    g = dict(ll.grid(rows, day, 5))
    assert g[at(9, 0)]["x"] == 1                       # 09:00:30 is not known at 09:00
    assert g[at(9, 5)]["x"] == 3
    assert g[at(9, 10)] is None                        # the last one (09:04) is 6 minutes old
    assert g[at(9, 20)]["x"] == 4 and len(g) == (6 * 60 + 20) // 5 + 1


def test_a_contrarian_signal_is_read_the_other_way():
    days = _days(25, seed=3)
    for rows in days.values():
        for r in rows:
            r["foreign_contracts"] = -r["foreign_contracts"]
    got = ll.measure(days, "foreign_futures", "futures", 5)
    assert got["lead"]["corr"] < -0.5 and got["hit_side"] == "opposite" and got["verdict"] == "선행"


def test_cadence_is_how_often_a_value_really_changes(days):
    c = ll.cadence(days)
    assert c["kospi_foreign_100m"]["median_update_sec"] == 300
    assert c["_snapshot_spacing_sec"]["median"] == 60
    assert c["kospi_institution_100m"]["median_update_sec"] is None


def test_the_report_shape(days):
    rep = ll.report(r for rows in days.values() for r in rows)
    assert rep["days"] == 25 and rep["from"] == FIRST.isoformat()
    assert len(rep["rows"]) == len(ll.BUCKETS) * len(ll.TARGETS) * (len(ll.INDICATORS) + 1)


def test_freshness_says_the_real_delay_when_the_source_has_a_time():
    from desk import freshness

    now = datetime(2026, 11, 18, 1, 30, tzinfo=UTC)                        # 10:30 KST
    got = freshness.stamp("2026-11-18T10:29:30+09:00", now, 60, live=True,
                          source_at="2026-11-18T10:26:00+09:00", source="toss_investors")
    assert got["age_sec"] == 30 and got["source_delay_sec"] == 240 and got["label"] == "약 4분 지연"
    kis = freshness.stamp("2026-11-18T10:29:30+09:00", now, 30, live=True, source="kis_futures_investors")
    assert kis["source_delay_sec"] is None and kis["label"] is None             # interval not measured yet
    freshness.PUBLISH_SEC["kis_futures_investors"] = 60
    try:
        kis = freshness.stamp("2026-11-18T10:29:30+09:00", now, 30, live=True, source="kis_futures_investors")
        assert kis["source_delay_sec"] == 60 and kis["label"] == "약 1분 지연"   # age 30 + half of 60
    finally:
        freshness.PUBLISH_SEC["kis_futures_investors"] = None
    closed = freshness.stamp(None, now, 600, live=False, source_at="2026-11-18T15:35:00+09:00")
    assert closed["label"] == "장 마감 · 마지막 값" and closed["age_sec"] is None and closed["source_delay_sec"] is None


def test_the_cli_reads_the_stored_snapshots(db_url, monkeypatch, capsys, tmp_path, days):
    from sqlalchemy import insert

    from desk import cli
    from desk.db import futures_snapshots, make_engine, migrate

    migrate(db_url)
    engine = make_engine(db_url)
    try:
        cols = {c.name for c in futures_snapshots.columns}
        rows = [{k: v for k, v in r.items() if k in cols} | {"code": "A01612"}
                for d in sorted(days)[:3] for r in days[d]]
        with engine.begin() as conn:
            conn.execute(insert(futures_snapshots), rows)
    finally:
        engine.dispose()
    monkeypatch.setenv("DESK_DATABASE_URL", db_url)
    monkeypatch.setattr(ll, "MIN_DAYS", 2)
    thinned = {"at": datetime(2026, 8, 3, 6, 45, tzinfo=UTC), "trade_date": "2026-08-03", "code": "A01609",
               "price": 380.0}                                       # a day thinned to its last row: no day
    engine2 = make_engine(db_url)
    with engine2.begin() as conn:
        conn.execute(insert(futures_snapshots), [thinned])
    engine2.dispose()
    out = tmp_path / "ll.json"
    assert cli.main(["lead-lag", "--days", "4000", "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "3 day(s)" in text and "foreign_futures" in text and out.exists()
