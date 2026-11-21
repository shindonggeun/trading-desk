import json
from unittest.mock import Mock

import pytest

from desk import backtest, cli, entry_now


def _input(root, ticker, frame, stamps):
    folder = root / ticker
    folder.mkdir(exist_ok=True)
    bars = [{"at": stamp, "open": 100, "high": 102, "low": 99, "close": 101, "volume": 80} for stamp in stamps]
    (folder / f"{frame}.json").write_text(json.dumps(bars), encoding="utf-8")


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def unexpected(*_args, **_kwargs):
        pytest.fail("preflight must not fetch, replay or write snapshots")

    monkeypatch.setattr(entry_now.Reach, "load", unexpected)
    monkeypatch.setattr(entry_now, "gather", unexpected)
    monkeypatch.setattr(entry_now, "replay", unexpected)


def _args(root, *rest):
    return ["entry-backtest", "--check-input", str(root), "--from", "2026-11-16", "--through", "2026-11-21", *rest]


def test_checks_all_horizons_and_shows_missing_tickers_frames_and_dates(tmp_path, capsys):
    _input(tmp_path, "QQQ", "1m", ["2026-11-17T14:30:00Z", "2026-11-19T20:59:00Z"])
    assert cli.main(_args(tmp_path)) == 2
    out = capsys.readouterr().out
    assert "필수 프레임 1m, 1d, 5m, 60m, 1w" in out
    assert "QQQ 1m: 2026-11-17 ~ 2026-11-19 (2봉) / 필요 2026-11-16 ~ 2026-11-20" in out
    assert "시작 구간 부족·종료 구간 부족" in out
    assert "QQQ 5m: 프레임 파일 없음" in out
    assert "005930.KS: 종목 폴더 없음" in out


def test_weekly_coverage_compares_weeks_and_daily_coverage_uses_exchange_dates(tmp_path, capsys):
    for ticker in backtest.BASKET:
        daily = (["2026-11-15T15:00:00Z", "2026-11-19T15:00:00Z"] if ticker.endswith(".KS")
                 else ["2026-11-16T05:00:00Z", "2026-11-20T05:00:00Z"])
        _input(tmp_path, ticker, "1d", daily)
        _input(tmp_path, ticker, "1w", daily[:1])
    files = sorted(tmp_path.rglob("*.json"))
    snapshot = [file.read_bytes() for file in files]
    assert cli.main(_args(tmp_path, "--horizon", "swing")) == 0
    out = capsys.readouterr().out
    assert "005930.KS 1d: 2026-11-16 ~ 2026-11-20" in out
    assert "QQQ 1w: 2026-11-16 ~ 2026-11-16" in out
    assert [file.read_bytes() for file in files] == snapshot
    assert sorted(tmp_path.rglob("*.json")) == files


@pytest.mark.parametrize("stamps", [
    [], ["2026-11-16T14:30:00"], ["2026-11-16T14:30:00Z", "2026-11-16T09:30:00-05:00"],
])
def test_unusable_input_is_reported_for_the_frame(tmp_path, capsys, stamps):
    _input(tmp_path, "QQQ", "1m", stamps)
    assert cli.main(_args(tmp_path, "--horizon", "scalp")) == 2
    assert "QQQ 1m: 입력 오류" in capsys.readouterr().out


@pytest.mark.parametrize("args", [
    ["--through", "2026-11-21"],
    ["--check-input", "."],
    ["--check-input", ".", "--from", "2026-11-22", "--through", "2026-11-21"],
])
def test_incomplete_or_reversed_check_arguments_do_not_start_backtest(capsys, args):
    assert cli.main(["entry-backtest", *args]) == 2
    assert "--through" in capsys.readouterr().err


def test_default_mode_still_dispatches_to_swing_replay_inputs(monkeypatch):
    gather = Mock(side_effect=RuntimeError("stop at the existing input loader"))
    monkeypatch.setattr(entry_now.Reach, "load", object)
    monkeypatch.setattr(entry_now, "gather", gather)
    with pytest.raises(RuntimeError, match="stop at the existing input loader"):
        cli.main(["entry-backtest"])
    tickers, horizon, start = gather.call_args.args
    assert tickers == list(backtest.BASKET)
    assert horizon.id == "swing"
    assert start == "2019-01-01"
