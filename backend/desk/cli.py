"""Account and database chores. There is no sign-up route; the owner is made here.

    python -m desk.cli migrate
    python -m desk.cli create-user <username>        # asks for the password twice
    python -m desk.cli set-password <username>       # also signs every browser out
    python -m desk.cli revoke-sessions <username>
    python -m desk.cli disable-otp <username>        # phone lost: turns OTP off, signs everyone out
    python -m desk.cli otp-key                       # prints a new DESK_OTP_KEY value
    python -m desk.cli backtest SOXL --horizon swing --from 2018-01-01 [--sweep] [--out report.json]
    python -m desk.cli backtest --basket             # the reference basket (desk.backtest.BASKET)
    python -m desk.cli backtest --basket --triggers  # touch entry vs the lower-bar trigger (#149)
    python -m desk.cli backtest --leveraged          # SOXL · TQQQ on the underlying's plans (#239)
    python -m desk.cli backtest --basket --features  # R by the chart at the plan, train / held-out (#284)
    python -m desk.cli timing [--last 20]            # where recent analyses spent their time (#171)
    python -m desk.cli calibrate [--horizon swing]   # rebuild desk/data/playbook_calibration.json (#147)
    python -m desk.cli entry-backtest [--horizon swing]  # the entry-now pick vs the score pick; desk/data/entry_reach.json (#312)
    python -m desk.cli weekly-lines [--out lines.json]  # compare the weekly map's trend-line pairs (#216)
    python -m desk.cli news-history [--out-dir data/gdelt]  # GDELT's past per news theme, resumable (#390)
    python -m desk.cli news-history export --dir data/gdelt  # the days the live macro_news pass keeps (#485)
    python -m desk.cli backtest --basket --news-dir data/gdelt [--macro-weight 0.5]  # replay the macro part (#390)
    python -m desk.cli backtest --basket --allow-missing  # go on without a ticker Yahoo had no bars for (#555)

In Docker: ``docker compose exec backend python -m desk.cli create-user <name>``.
For scripts, DESK_NEW_PASSWORD supplies the password instead of the prompt.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError

from . import otp
from .auth import MIN_PASSWORD, Auth
from .db import make_engine, migrate, now
from .settings import load


def _password() -> str:
    given = os.getenv("DESK_NEW_PASSWORD")
    if given:
        return given
    first = getpass.getpass(f"password (min {MIN_PASSWORD} chars): ")
    if first != getpass.getpass("again: "):
        sys.exit("passwords differ")
    return first


def _timing(engine, last: int) -> int:
    from . import timing
    from .store import Store

    store, found, offset = Store(engine), [], 0
    try:
        while len(found) < last and offset < 2000:      # scan back past failed and pre-#171 runs
            page = store.list_runs(limit=100, offset=offset)
            if not page:
                break
            found += [r["plan"]["timing"] for r in page
                      if r["status"] == "done" and (r.get("plan") or {}).get("timing")]
            offset += 100
    finally:
        engine.dispose()
    report = timing.summarize(found[:last])
    def fmt(s: dict) -> str:
        if s["n"] == 0:
            return "—"
        return f"{s['mean']:>6}s avg  {s['median']:>6}s med  {s['max']:>6}s max  (n={s['n']})"

    print(f"{report['runs']} runs  total {fmt(report['total'])}")
    for name, s in report["stages"].items():
        print(f"  {name:<22}{fmt(s)}")
    print("  per turn (the first serial agent's first turn includes the wait for every parallel node):")
    for name, s in report["agents"].items():
        print(f"    {name:<24}{fmt(s)}")
    print("  per run (an agent's turns added up):")
    for name, s in report["agents_per_run"].items():
        print(f"    {name:<24}{fmt(s)}")
    return 0


def _calibrate(args) -> int:
    """The prior of desk.calibration: every settled basket trade per horizon. The reliability check
    builds the table from the training dates only and scores it on the held-out ones."""
    from . import backtest, calibration, horizons

    cells: dict = {}
    recent: dict = {}
    windows: dict = {}
    checks: dict = {}
    for name in args.horizon or ["swing"]:
        if name not in horizons.HORIZONS:
            sys.exit(f"horizon must be one of {', '.join(horizons.HORIZONS)}")
        got: dict = {}
        backtest.run(list(backtest.BASKET), name, args.start, risk=True,
                     progress=lambda msg: print(msg, file=sys.stderr),
                     on_trades=lambda trades, cut, got=got: got.update(trades=trades, cut=cut))
        trades, cut = got.get("trades") or [], got.get("cut")
        cells.update(calibration.from_trades(trades, name))
        # #286: the last twelve months up to the backtest's last plan
        last = max((t.at for t in trades), default=None)
        if last:
            since = calibration.recent_since(last)
            recent.update(calibration.from_trades(trades, name, since=since))
            windows[name] = {"since": since, "as_of": str(last)[:10]}
        train = [t for t in trades if cut is None or t.at < cut]
        valid = [t for t in trades if cut is not None and t.at >= cut]
        bands = calibration.reliability(calibration.from_trades(train, name), valid, name)
        checks[name] = {"cut": cut, "bands": bands}
        print(f"{name}: {len(trades)} plans, cut {cut}")
        for row in bands:
            print(f"  band {row['band']:>2}+  n {row['n']:>5}  predicted {row['predicted']:.3f}  "
                  f"actual {row['actual']:.3f}  gap {row['gap']:+.3f}")
    calibration.write(cells, {"source": "backtest", "tickers": list(backtest.BASKET), "start": args.start,
                              "costs_bps": backtest.COST_BPS, "reliability": checks, "recent_window": windows},
                      recent=recent)
    print(f"written: {calibration.PATH}")
    for row in calibration.drifted(cells, recent):
        print(f"  drift {row['drift']:8} {row['key']}: long {row['long_exp_r']:+.3f}R (n {row['long_n']}) "
              f"recent {row['recent_exp_r']:+.3f}R (n {row['recent_n']})")
    return 0


def _entry_backtest(args) -> int:
    """#312: the expected-R pick (desk.entry_now) against the score pick on the basket, train / held-out,
    and the fill-by-distance table the live page reads (every date, like the calibration prior)."""
    from . import backtest, entry_now, horizons

    reach = entry_now.Reach.load()
    for name in args.horizon or ["swing"]:
        if name not in horizons.HORIZONS:
            sys.exit(f"horizon must be one of {', '.join(horizons.HORIZONS)}")
        h = horizons.get(name)
        tickers = list(backtest.BASKET)
        snaps, notes = entry_now.gather(tickers, h, args.start, progress=lambda msg: print(msg, file=sys.stderr))
        rows = entry_now.replay(snaps, h, progress=lambda msg: print(msg, file=sys.stderr))
        report = {"version": entry_now.VERSION, "horizon": name, "tickers": tickers, "start": args.start,
                  "costs_bps": backtest.COST_BPS, **entry_now.compare(rows, h), "notes": notes}
        out = entry_now.REPORT_DIR / f"entry_now_{name}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8", newline="\n") as f:      # LF on Windows too
            f.write(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
        print(f"{name}: {len(rows)} plans, cut {report.get('cut')} -> {out}")
        for arm, parts in (report.get("arms") or {}).items():
            for part, a in parts.items():
                print(f"  {arm:<9} {part:<5} taken {a['taken']:>5}/{a['plans']:<5} fill {a['fill_rate']}  "
                      f"T1 {a['t1_first_rate']}  R/filled {a['expectancy_r']}  R/plan {a['expectancy_per_plan_r']}  "
                      f"maxDD {a['max_dd_r']}")
        reach.cells = {k: v for k, v in reach.cells.items() if not k.startswith(f"{name}|")}
        reach.cells.update(entry_now.full_reach(rows, h).cells)
    reach.write({"source": "backtest", "tickers": list(backtest.BASKET), "start": args.start})
    print(f"written: {entry_now.REACH_PATH}")
    return 0


def _flow_history(args) -> int:
    """#389: the broker from the settings (.env) - its keys are read there, never printed."""
    import asyncio
    from datetime import date

    from . import flow_history
    from .broker import make_broker
    from .market_flow import KST

    settings = load()
    if settings.broker != "toss":
        print(f"broker is {settings.broker!r}: the mock's sample flow is not history - set DESK_BROKER=toss")
        return 1
    markets = flow_history.MARKETS if args.market.lower() == "all" else (args.market.upper(),)
    start, today = date.fromisoformat(args.start), datetime.now(KST).date()

    async def go() -> None:
        broker = make_broker(settings)
        for market in markets:
            path = flow_history.path_for(args.out_dir, market)
            data = await flow_history.collect(broker, market, start, path, today=today)
            days = sorted(data["rows"])
            print(f"{market}: {len(days)}일 {days[0] if days else '-'} ~ {days[-1] if days else '-'} -> {path}")

    asyncio.run(go())
    return 0


def gdelt_cache_dir(given: str | None) -> str | None:
    """#390: the news-history directory - the flag, else DESK_GDELT_CACHE, else None."""
    return given or os.getenv("DESK_GDELT_CACHE") or None


def seconds_to_minute(now: datetime, minute: int) -> float:
    """Until the next wall-clock hh:``minute`` (strictly after ``now``)."""
    target = now.replace(minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(hours=1)
    return (target - now).total_seconds()


def _news_history_export(args) -> int:
    """#485: copy the tone · article-count files the live macro_news refresh keeps (desk.macro_history)
    to ``--dir`` - on the server: docker exec <backend> python -m desk.cli news-history export --dir /tmp/gdelt,
    then docker cp, then ``backtest --news-dir`` on the PC."""
    from . import macro_history, macro_news

    src = args.src or str(macro_news.CACHE.history_dir())
    n = macro_history.export(src, args.dir)
    print(f"news-history: {n} file(s) {src} -> {args.dir}")
    return 0 if n else 1


def _news_history(args, sleep=time.sleep, clock=datetime.now) -> int:
    """#390: GDELT's past per news theme into ``--out-dir`` (or DESK_GDELT_CACHE). Stops at GDELT's
    first refusal; with ``--retry-after-min`` it waits that long and carries on, up to ``--max-rounds``
    refusals. With ``--hourly-at M`` it instead runs one batch of ``--max-files`` every hour at hh:M
    (a refusal only ends that hour's batch) until nothing is missing - one background run, small
    enough per hour to leave GDELT's limit to the live macro_news refresh on the same address."""
    from . import news_history

    out_dir = gdelt_cache_dir(args.out_dir) or "data/gdelt"
    hourly = getattr(args, "hourly_at", None)
    max_files = getattr(args, "max_files", None)
    rounds = 0
    while True:
        if hourly is not None:
            sleep(seconds_to_minute(clock(), hourly))
        got = news_history.collect(out_dir, first_year=args.first_year, max_files=max_files,
                                   progress=lambda msg: print(msg, file=sys.stderr, flush=True))
        total = got["fetched"] + got["kept"] + len(got["missing"])
        print(f"받음 {got['fetched']} · 있던 것 {got['kept']} · 남은 것 {len(got['missing'])} "
              f"(전체 {total}) -> {out_dir}", flush=True)
        if not got["missing"]:
            return 0
        if hourly is not None:
            note = f"GDELT 거절({got['refused'][:60]})" if got["refused"] else f"이번 시간 {max_files}개"
            print(f"{clock():%m-%d %H:%M} {note} — 다음 정시 {hourly:02d}분에 이어 받기", flush=True)
            continue
        if not got["refused"]:
            return 0
        rounds += 1
        if args.retry_after_min <= 0 or rounds > args.max_rounds:
            print(f"GDELT 가 거절해 멈춤({got['refused'][:80]}) — 15분쯤 뒤 같은 명령으로 이어 받기", flush=True)
            return 2
        print(f"GDELT 가 거절함({got['refused'][:80]}) — {args.retry_after_min:g}분 쉬고 이어 받기 "
              f"({rounds}/{args.max_rounds})", flush=True)
        sleep(args.retry_after_min * 60)


def _backtest(args) -> int:
    """No settings or database: bars come from Yahoo, the report goes to stdout and ``--out``."""
    import json
    from dataclasses import asdict

    from . import backtest, horizons

    tickers = [t.strip().upper() for t in args.tickers if t.strip()]
    if args.basket:
        tickers += [t for t in backtest.BASKET if t not in tickers]
    if not tickers:
        sys.exit("give tickers or --basket")
    if args.horizon is not None and args.horizon not in horizons.HORIZONS:
        sys.exit(f"horizon must be one of {', '.join(horizons.HORIZONS)}")
    costs = backtest.Costs(args.cost_bps) if args.cost_bps is not None else backtest.Costs()
    if args.leveraged:
        from . import leverage_bt

        rep = leverage_bt.run(None, args.horizon or "swing", args.start or "2019-01-01", costs,
                              progress=lambda msg: print(msg, file=sys.stderr))
        leverage_bt.print_report(rep)
        if args.out:
            with open(args.out, "w", encoding="utf-8", newline="\n") as f:
                json.dump(rep, f, ensure_ascii=False, indent=2)
        return
    if args.triggers:
        from . import trigger_bt

        rep = trigger_bt.run(tickers, args.horizon or "swing", args.start, costs.bps_per_side,
                             progress=lambda msg: print(msg, file=sys.stderr))
        trigger_bt.print_report(rep)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(rep, f, ensure_ascii=False, indent=1)
        return 0
    report = backtest.run(tickers, args.horizon, args.start, args.end, args.step, costs, args.sweep,
                          progress=lambda msg: print(msg, file=sys.stderr), risk=not args.no_risk or None,
                          features=args.features, flow_dir=args.flow_dir, flow_weight=args.flow_weight,
                          news_dir=gdelt_cache_dir(args.news_dir), macro_weight=args.macro_weight,
                          news_source=args.news_source, allow_missing=args.allow_missing)
    backtest.print_summary(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(asdict(report), f, ensure_ascii=False, indent=1)
        print(f"report: {args.out}")
    return 0


def _weekly_lines(args) -> int:
    import json

    from . import weekly_map

    report = weekly_map.compare_lines(weekly_map.Maps().all_bars())
    weekly_map.print_lines(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
    return 0


def _day_call(args) -> int:
    """#572: fit before the hold-out, score the hold-out once, write the model and the report."""
    import json
    from datetime import date
    from pathlib import Path

    from . import day_call_train as dct

    series = dct.fetch(flow_dir=args.flow_dir)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    start = date.fromisoformat(args.start) if args.start else None
    for market in (("kr", "us") if args.market == "all" else (args.market,)):
        model, report = dct.backtest(market, series, args.holdout_days or dct.HOLDOUT_DAYS, start,
                                     progress=lambda msg: print(msg, file=sys.stderr), compare=not args.no_compare)
        for name, body in ((f"model_{market}.json", model), (f"report_{market}.json", report)):
            with open(out / name, "w", encoding="utf-8", newline="\n") as f:
                json.dump(body, f, ensure_ascii=False, indent=1)
                f.write("\n")
        for call_name, call in report["calls"].items():
            for r, rep in call["rounds"].items():
                h = rep["holdout"]
                b = h["baselines"]
                hold = {m["margin"]: m for m in h["margins"]}[0.1]
                print(f"{market} {call_name:16} {r}차  n={h['n']}  방향 {h['direction_hit_rate']:.3f} {h['direction_ci95']}"
                      f"  (항상 롱 {b['always_long']['direction_hit_rate']:.3f} · 숏 {b['always_short']['direction_hit_rate']:.3f}"
                      f" · 전날 따라 {b['follow_yesterday']['direction_hit_rate']})  단계 {h['stage_hit_rate']:.3f}"
                      f" (기후 {b['climatology']['stage_hit_rate']:.3f})  Brier {h['brier']:.4f} skill {h['brier_skill']:+.3f}"
                      f"  10%p 이상 {hold['coverage']:.2f} → 적중 {hold['direction_hit_rate']}")
        for v in report["variants"]:
            b = v["baselines"]
            hold = {m["margin"]: m for m in v["margins"]}[0.1]
            print(f"  {market} {v['round']}차 {v['label']} {'+'.join(v['extras']) or '-':11} edges={v['edges']} "
                  f"n={v['n']} train={v['train_n']}  방향 {v['direction_hit_rate']:.3f} (롱 {b['always_long']['direction_hit_rate']:.3f}"
                  f" · 숏 {b['always_short']['direction_hit_rate']:.3f})  단계 {v['stage_hit_rate']:.3f} "
                  f"(기후 {b['climatology']['stage_hit_rate']:.3f})  skill {v['brier_skill']:+.3f}  "
                  f"보류 10%p: 범위 {hold['coverage']:.2f} 적중 {hold['direction_hit_rate']}")
        print(f"{market}: {out / f'model_{market}.json'} · {out / f'report_{market}.json'}  ({report['version']})")
    return 0


def _day_call_backfill(engine, args) -> int:
    """#572: the sessions after the committed model's training, as if called at each cutoff, scored."""
    from . import day_calls
    from .prices import yahoo_candles

    desk = day_calls.DayCalls(engine, yahoo_candles)
    # hourly futures far enough back for the hold-out (about 24 bars a day)
    series = day_calls.fetch_series(yahoo_candles, hourly_bars=24 * (args.days + 15))
    for market in (("kr", "us") if args.market == "all" else (args.market,)):
        if not desk.models.get(market):
            print(f"{market}: no committed model", file=sys.stderr)
            continue
        made = desk.backfill(market, series, args.days)
        print(f"{market}: {made} calls backfilled")
    return 0


def _unfinished(url: str) -> int:
    """#431 no migration here: it runs inside the live container, and only reads."""
    from .store import Store

    engine = make_engine(url)
    try:
        rows = Store(engine).unfinished()
    finally:
        engine.dispose()
    print(len(rows))
    for r in rows:
        print(f"{r['ticker']}	{r['status']}	{r['started_at'] or r['created_at']}	{r['id']}")
    return 0


def _utf8_out() -> None:
    """Reports carry Korean and '—': a Windows console or a redirect to a file defaults to cp949,
    where one such character used to stop ``backtest`` before it wrote ``--out``."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure") and (stream.encoding or "").lower().replace("-", "") != "utf8":
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _utf8_out()
    parser = argparse.ArgumentParser(prog="python -m desk.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="bring the database schema to head")
    sub.add_parser("otp-key", help="print a new DESK_OTP_KEY (Fernet key) for the server env")
    for name in ("create-user", "set-password", "revoke-sessions", "disable-otp"):
        sub.add_parser(name).add_argument("username")
    rescore = sub.add_parser("rescore", help=(
        "forget plan outcomes so the next pass scores them again; plans whose intraday bars are past "
        "Yahoo's reach (1m 7 days, 5m 60 days) come back unscorable, so prefer --days"))
    rescore.add_argument("--days", type=int, default=None, help="only runs created in the last N days")
    bt = sub.add_parser("backtest", help=(
        "replay the desk playbooks on past bars (walk-forward) and score them; no database, Yahoo bars"))
    bt.add_argument("tickers", nargs="*", help="tickers (default with --basket: the reference basket)")
    bt.add_argument("--basket", action="store_true", help="add the reference basket")
    bt.add_argument("--horizon", default=None, help="scalp | intraday | short_swing | swing (default swing)")
    bt.add_argument("--from", dest="start", default=None, help="first plan date, YYYY-MM-DD")
    bt.add_argument("--to", dest="end", default=None, help="last plan date, YYYY-MM-DD")
    bt.add_argument("--step", type=int, default=None, help="one plan every N primary bars")
    bt.add_argument("--cost-bps", type=float, default=None, help="commission + slippage per side (bp)")
    bt.add_argument("--sweep", action="store_true", help="also try the parameters one at a time")
    bt.add_argument("--out", default=None, help="write the full JSON report here")
    bt.add_argument("--features", action="store_true",
                    help="also bucket the trades by the chart at the plan (trend, oscillators, volume, gap...) "
                         "on the training and held-out dates (#284)")
    bt.add_argument("--leveraged", action="store_true",
                    help="leveraged products on their own plans vs their underlying's plans, with the decay filter (#239)")
    bt.add_argument("--triggers", action="store_true",
                    help="compare the zone-touch entry with the lower-bar trigger entry (#149; ~2 years of 60m bars)")
    bt.add_argument("--no-risk", action="store_true",
                    help="leave out the market's selling-score rule (#146) and its on/off comparison")
    bt.add_argument("--allow-missing", action="store_true",
                    help="go on without a ticker Yahoo has no bars for (listed in the report's missing); "
                         "by default the run stops (#555)")
    bt.add_argument("--flow-dir", default=None,
                    help="flow-history files (#389): Korean plans get the market's foreign net buying as a direction part")
    bt.add_argument("--flow-weight", type=float, default=None,
                    help="count the flow part in the direction score at this weight for the run (live weight stays 0)")
    bt.add_argument("--news-dir", default=None,
                    help="news-history files (#390; default DESK_GDELT_CACHE): every plan gets the news themes "
                         "of the days before it as the macro part")
    bt.add_argument("--news-source", default="gdelt", choices=("gdelt", "google-news"),
                    help="#485: which kept days --news-dir gives the macro part - GDELT's or the Google News "
                         "ones the live refresh keeps when GDELT refuses (google-news/ below it)")
    bt.add_argument("--macro-weight", type=float, default=None,
                    help="count the macro part in the direction score at this weight for the run (live weight stays 0)")
    cb = sub.add_parser("calibrate", help=(
        "rebuild desk/data/playbook_calibration.json from the basket backtest (#147): T1 rate and "
        "expected R per setup, regime and score band, with the held-out reliability check"))
    cb.add_argument("--horizon", action="append", default=None,
                    help="repeatable; default swing (the other frames have few bars on Yahoo)")
    cb.add_argument("--from", dest="start", default="2019-01-01", help="first plan date, YYYY-MM-DD")
    eb = sub.add_parser("entry-backtest", help=(
        "the entry-now pick (expected R per plan) against the score pick on the basket, train / held-out (#312); "
        "rewrites desk/data/entry_reach.json"))
    eb.add_argument("--horizon", action="append", default=None, help="repeatable; default swing")
    eb.add_argument("--from", dest="start", default="2019-01-01", help="first plan date, YYYY-MM-DD")
    wl = sub.add_parser("weekly-lines", help=(
        "compare the weekly map's black / red line pairs on the indices' weekly history (#216)"))
    wl.add_argument("--out", default=None, help="write the full JSON report here")
    dcp = sub.add_parser("day-call", help=(
        "#572 today's direction call: fit the model on Yahoo daily bars before the hold-out and score the "
        "hold-out once; writes model_<market>.json and report_<market>.json"))
    dcp.add_argument("action", choices=("backtest", "backfill"),
                     help="backtest: fit and score (no database); backfill: the committed model's hold-out "
                          "sessions re-made as of each cutoff into the database (source backfill), scored")
    dcp.add_argument("--days", type=int, default=90, help="backfill: how many calendar days back (default 90)")
    dcp.add_argument("--market", choices=("kr", "us", "all"), default="all")
    dcp.add_argument("--from", dest="start", default=None, help="first session to use, YYYY-MM-DD")
    dcp.add_argument("--holdout-days", type=int, default=None, help="calendar days held out (default 90)")
    dcp.add_argument("--out-dir", default="desk/data/day_call", help="where the model and report files go")
    dcp.add_argument("--flow-dir", default=None,
                     help="flow-history files (#389; collected on the server) for the KOSPI flow features")
    dcp.add_argument("--no-compare", action="store_true", help="skip the table of every variant")
    bz = sub.add_parser("bottom-backtest", help=(
        "the long-term bottom zone (#240) on past big lows: signals, how early, staged-buy returns"))
    bz.add_argument("tickers", nargs="*", default=["^NDX", "^GSPC", "^SOX", "^KS11", "005930.KS", "000660.KS"])
    bz.add_argument("--out", default=None, help="write the full JSON report here")
    fh = sub.add_parser("flow-history", help=(
        "page KOSPI / KOSDAQ daily net buying by investor back to --from into <out-dir>/<market>.json "
        "(#389; Toss answers only from its registered IP - run on the server; resumes where it stopped)"))
    fh.add_argument("--market", default="all", help="kospi | kosdaq | all (default all)")
    fh.add_argument("--from", dest="start", default="2019-01-01", help="oldest day kept, YYYY-MM-DD")
    fh.add_argument("--out-dir", default="data/flow", help="directory of the files (default data/flow)")
    nh = sub.add_parser("news-history", help=(
        "GDELT's daily tone and article counts per news theme into <out-dir> (#390; one request per 20 s, "
        "stops at the first refusal - run again later to carry on)"))
    nh.add_argument("action", nargs="?", choices=("collect", "export"), default="collect",
                    help="collect (default): fetch the past from GDELT; export: copy the files the live refresh keeps (#485)")
    nh.add_argument("--dir", default="data/gdelt", help="export: destination directory (default data/gdelt)")
    nh.add_argument("--src", default=None, help="export: source directory (default the live refresh's news-history)")
    nh.add_argument("--out-dir", default=None,
                    help="directory of the files (default DESK_GDELT_CACHE, else data/gdelt)")
    nh.add_argument("--retry-after-min", type=float, default=0.0,
                    help="after a refusal wait this many minutes and carry on (default 0 = stop; GDELT blocks ~15 min)")
    nh.add_argument("--max-rounds", type=int, default=12, help="most refusals to wait out with --retry-after-min (default 12)")
    nh.add_argument("--max-files", type=int, default=None, help="fetch at most this many files per batch")
    nh.add_argument("--hourly-at", type=int, default=None, choices=range(60), metavar="MINUTE",
                    help="run one batch (--max-files) every hour at this minute until done, e.g. 17")
    nh.add_argument("--first-year", type=int, default=2017, help="oldest year (default 2017)")
    sub.add_parser("unfinished", help=(
        "queued / running runs: the count on the first line, then one line each (#431; scripts/deploy.sh "
        "asks this inside the backend container before it restarts it)"))
    tm = sub.add_parser("timing", help="average time per stage and per agent over the latest finished runs")
    tm.add_argument("--last", type=int, default=20, help="how many recent finished runs (default 20)")
    args = parser.parse_args(argv)
    if args.command == "otp-key":
        print(otp.new_key())
        return 0
    if args.command in ("backtest", "calibrate"):
        from .backtest import MissingTickers

        try:
            return _backtest(args) if args.command == "backtest" else _calibrate(args)
        except MissingTickers as exc:   # #555: no report that silently lacks a basket ticker
            print(f"{exc} — 잠시 뒤 다시 돌리거나, 빼고 볼 거면 backtest --allow-missing", file=sys.stderr)
            return 2
    if args.command == "entry-backtest":
        return _entry_backtest(args)
    if args.command == "weekly-lines":
        return _weekly_lines(args)
    if args.command == "day-call" and args.action == "backtest":
        _utf8_out()
        return _day_call(args)
    if args.command == "flow-history":
        return _flow_history(args)
    if args.command == "news-history":
        return _news_history_export(args) if args.action == "export" else _news_history(args)
    if args.command == "bottom-backtest":
        from . import bottom_zone

        report = bottom_zone.report(args.tickers)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=1)
        return 0

    settings = load()
    if args.command == "unfinished":
        return _unfinished(settings.database_url)
    migrate(settings.database_url)
    if args.command == "migrate":
        print("schema at head")
        return 0

    engine = make_engine(settings.database_url)
    if args.command == "day-call":           # backfill: the hold-out sessions into this database
        _utf8_out()
        try:
            return _day_call_backfill(engine, args)
        finally:
            engine.dispose()
    if args.command == "timing":
        return _timing(engine, args.last)
    if args.command == "rescore":
        from .store import Store

        since = now() - timedelta(days=args.days) if args.days else None
        print(f"cleared {Store(engine).clear_outcomes(since)} outcome(s); the server rescores them next pass")
        return 0
    auth = Auth(engine, timedelta(hours=settings.session_max_hours), timedelta(hours=settings.session_idle_hours))
    try:
        if args.command == "create-user":
            auth.create_user(args.username, _password())
            print(f"created {args.username}")
        elif args.command == "set-password":
            if not auth.set_password(args.username, _password()):
                sys.exit(f"no such user: {args.username}")
            print(f"password changed; every session of {args.username} signed out")
        elif args.command == "revoke-sessions":
            print(f"signed out {auth.revoke_all(args.username)} session(s)")
        elif args.command == "disable-otp":
            if not auth.otp_disable_by_name(args.username):
                sys.exit(f"no such user: {args.username}")
            print(f"OTP off for {args.username}; recovery codes deleted, every session signed out")
    except ValueError as exc:
        sys.exit(str(exc))
    except IntegrityError:
        sys.exit(f"user exists: {args.username} (use set-password)")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
