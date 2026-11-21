"""Offline coverage check for the entry-backtest basket's OHLCV inputs."""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path

from . import backtest, horizons, market_calendar


def _bounds(ticker: str, start: date, end: date) -> tuple[date, date] | None:
    market = "kr" if ticker.endswith((".KS", ".KQ")) else "us"
    first = start if market_calendar.is_open(market, start) else market_calendar.next_open(market, start)
    if first > end:
        return None
    last = end if market_calendar.is_open(market, end) else market_calendar.previous_open(market, end)
    return first, last


def _span(path: Path, ticker: str) -> tuple[date, date, int]:
    bars = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(bars, list) or not bars:
        raise ValueError("비어 있지 않은 봉 배열이 필요합니다")
    previous = None
    for index, bar in enumerate(bars, 1):
        try:
            timestamp = datetime.fromisoformat(bar["at"])
            if timestamp.utcoffset() is None or (previous is not None and timestamp <= previous):
                raise ValueError
            for field in ("open", "high", "low", "close", "volume"):
                number = bar[field]
                if type(number) not in (int, float) or not math.isfinite(number):
                    raise ValueError
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{index}행에 시간대가 있는 오름차순 시각과 유한한 OHLCV가 필요합니다") from exc
        previous = timestamp
    return (date.fromisoformat(backtest.local_day(bars[0]["at"], ticker)),
            date.fromisoformat(backtest.local_day(bars[-1]["at"], ticker)), len(bars))


def _period(day: date, interval: str) -> tuple[int, ...]:
    return tuple(day.isocalendar()[:2]) if interval == "1w" else (day.toordinal(),)


def _check_frame(folder: Path, ticker: str, interval: str, bounds: tuple[date, date]) -> bool:
    label = f"{ticker} {interval}"
    path = folder / f"{interval}.json"
    try:
        first, last, count = _span(path, ticker)
    except FileNotFoundError:
        print(f"{label}: 프레임 파일 없음 ({path.name})")
        return False
    except (OSError, ValueError) as exc:
        print(f"{label}: 입력 오류 ({exc})")
        return False

    start, end = bounds
    missing = []
    if _period(first, interval) > _period(start, interval):
        missing.append("시작 구간 부족")
    if _period(last, interval) < _period(end, interval):
        missing.append("종료 구간 부족")
    status = "·".join(missing) or "범위 충족"
    print(f"{label}: {first} ~ {last} ({count}봉) / 필요 {start} ~ {end} / {status}")
    return not missing


def check(root: Path, names: list[str], start: date, end: date) -> int:
    if start > end:
        raise ValueError("--from은 --through보다 늦을 수 없습니다")
    frames = tuple(dict.fromkeys(
        frame for name in names for frame in (horizons.HORIZONS[name].primary, horizons.HORIZONS[name].secondary)
    ))
    print(f"요청 {start} ~ {end} / 기간 {', '.join(names)} / 필수 프레임 {', '.join(frames)}")
    ok = True
    for ticker in backtest.BASKET:
        folder = root / ticker
        if not folder.is_dir():
            print(f"{ticker}: 종목 폴더 없음")
            ok = False
            continue
        bounds = _bounds(ticker, start, end)
        if bounds is None:
            print(f"{ticker}: 요청 구간에 거래일 없음")
            ok = False
            continue
        for interval in frames:
            ok = _check_frame(folder, ticker, interval, bounds) and ok
    return 0 if ok else 2
