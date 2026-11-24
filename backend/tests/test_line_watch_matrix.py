"""A fixed OHLC control isolates detector behavior across watch timeframes."""

from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest

from desk import line_signals, line_watch_bars
from tests.test_channels import FIVE, HOURLY
from tests.test_line_signals import _mirror

RESISTANCE = {"touch_resistance", "break_up", "retest_up"}
START = datetime(2025, 1, 6, tzinfo=UTC)


@pytest.mark.parametrize("frame", line_watch_bars.TIMEFRAMES)
def test_resistance_detector_frame_matrix(frame):
    counts = Counter()
    minutes = line_watch_bars.MINUTES[frame]
    step = timedelta(minutes=minutes)
    for raw in (_mirror(FIVE), HOURLY):
        bars = [{**row, "at": (START + step * i).isoformat()} for i, row in enumerate(raw)]
        seen = set()
        for n in range(len(bars) - 15, len(bars) + 1):
            counts["bars"] += 1
            for signal in line_signals.detect(bars[:n], frame, confirmed=True):
                if signal["rule"] not in RESISTANCE:
                    continue
                key = (signal["rule"], signal["line_id"], signal["at"])
                counts["duplicates"] += key in seen
                seen.add(key)
                counts[signal["event"]] += 1
                if signal["event"] == "touch" and n < len(bars):
                    counts["followed"] += 1
                    counts["next_close_lower"] += bars[n]["close"] < bars[n - 1]["close"]

    assert counts["bars"] == 32
    assert counts["touch"] == (5 if frame == "240m" else 3)
    assert counts["break"] == 3 and counts["retest"] == 0
    assert counts["followed"] == (4 if frame == "240m" else 2)
    assert counts["next_close_lower"] == 1
    assert counts["duplicates"] == 0
