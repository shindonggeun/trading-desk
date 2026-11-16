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
    python -m desk.cli backtest --basket --news-dir data/gdelt [--macro-weight 0.5]  # replay the macro part (#390)

In Docker: ``docker compose exec backend python -m desk.cli create-user <name>``.
For scripts, DESK_NEW_PASSWORD supplies the password instead of the prompt.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
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


def _news_history(args) -> int:
    """#390: GDELT's past per news theme into ``--out-dir``; stops at GDELT's first refusal."""
    from . import news_history

    got = news_history.collect(args.out_dir, first_year=args.first_year,
                               progress=lambda msg: print(msg, file=sys.stderr))
    print(f"받음 {got['fetched']} · 있던 것 {got['kept']} · 남은 것 {len(got['missing'])} -> {args.out_dir}")
    if got["refused"]:
        print(f"GDELT 가 거절해 멈춤({got['refused'][:80]}) — 15분쯤 뒤 같은 명령으로 이어 받기")
        return 2
    return 0


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
                          news_dir=args.news_dir, macro_weight=args.macro_weight)
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
    bt.add_argument("--flow-dir", default=None,
                    help="flow-history files (#389): Korean plans get the market's foreign net buying as a direction part")
    bt.add_argument("--flow-weight", type=float, default=None,
                    help="count the flow part in the direction score at this weight for the run (live weight stays 0)")
    bt.add_argument("--news-dir", default=None,
                    help="news-history files (#390): every plan gets the news themes of the days before it as the macro part")
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
    nh.add_argument("--out-dir", default="data/gdelt", help="directory of the files (default data/gdelt)")
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
    if args.command == "backtest":
        return _backtest(args)
    if args.command == "calibrate":
        return _calibrate(args)
    if args.command == "entry-backtest":
        return _entry_backtest(args)
    if args.command == "weekly-lines":
        return _weekly_lines(args)
    if args.command == "flow-history":
        return _flow_history(args)
    if args.command == "news-history":
        return _news_history(args)
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
