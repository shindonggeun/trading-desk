"""#239 stage 3: a leveraged product on its underlying's plan - beta, decay, price conversion."""

from itertools import pairwise

import pytest

from desk import leverage_bt


def test_realized_beta_reads_a_3x_product():
    under = [100.0]
    for i in range(80):
        under.append(under[-1] * (1 + (0.01 if i % 3 else -0.015)))
    product = [50.0]
    for a, b in pairwise(under):
        product.append(product[-1] * (1 + 3 * (b / a - 1)))
    assert leverage_bt.realized_beta(product, under) == pytest.approx(3.0, abs=1e-6)
    assert leverage_bt.realized_beta(product[:5], under[:5]) is None      # too few days


def test_decay_grows_with_the_square_of_the_leverage_and_the_days():
    under = [100.0]
    for i in range(30):
        under.append(under[-1] * (1.02 if i % 2 else 0.98))
    one = leverage_bt.decay_pct(under, 3, 1)
    assert one == pytest.approx(3 * 0.02 ** 2, rel=0.1)                   # (9 - 3) / 2 · σ²
    assert leverage_bt.decay_pct(under, 3, 10) == pytest.approx(10 * one)
    assert leverage_bt.decay_pct(under, 1, 10) == 0


def test_the_underlying_plan_in_product_prices_and_the_decay_skip():
    pb = {"frame": {"reference_price": 100.0, "atr": 2.0},
          "long": {"setup": "dip_catch", "zone": {"low": 96.0, "high": 98.0, "mid": 97.0},
                   "entries": [{"step": 1, "price": 98.0, "share": 0.5}, {"step": 2, "price": 96.0, "share": 0.5}],
                   "avg_entry": 97.0, "stop": 94.0, "targets": [{"role": "t1", "price": 103.0}],
                   "time_stop": {"bars": 10}},
          "short": {"setup": "none"}}
    conv = leverage_bt.to_product(pb, 50.0, 100.0, 3.0)
    long = conv["long"]
    assert [e["price"] for e in long["entries"]] == [47.0, 44.0]           # -2% → -6%, -4% → -12%
    assert long["stop"] == 41.0 and long["targets"][0]["price"] == 54.5 and long["risk"] == 4.5
    assert long["zone"] == {"low": 44.0, "high": 47.0, "mid": 45.5} and conv["frame"]["atr"] == 3.0
    assert conv["short"] == {"setup": "none"}
    # T1 is 54.5 / 45.5 - 1 ≈ 19.8% away: 1% a day for 10 days eats about half of it
    assert leverage_bt._decayed(conv, "long", 0.012, 1) is True
    assert leverage_bt._decayed(conv, "long", 0.002, 1) is False
