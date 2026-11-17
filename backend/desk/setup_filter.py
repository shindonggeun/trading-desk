"""Setups kept only where the chart showed an edge on both the training and the held-out dates (#285).

The basket backtest's feature report (#284, ``python -m desk.cli backtest --basket --features``)
buckets every swing trade by the chart at its plan (desk.entry_features). Only the buckets better
than their setup's own average on the training dates *and* the held-out ones, with enough trades,
became rules here - one or two per setup, the edges fixed at the training dates' quartiles:

- pullback buy: kept only when the stock leads its index (20-day return ``REL20_MIN`` above it) or
  has run ``RET20_MIN`` in 20 days - the trend is there to pull back in. Training +0.007R / held-out
  +0.153R (rel20) against the setup's -0.091R / -0.001R.
- dip-catch: skipped on a dry day (volume under ``DRY_VOL`` of its 20-day average) - nobody is
  buying the low yet. Training -0.236R / held-out -0.268R, the worst bucket of the setup. Against
  the trend (desk.direction leans short) it also needs a falling 20-day line (#166 stage 6,
  ``DIP_AGAINST_SLOPE``); checked first, so a flat and dry one reads as that rule.
- short fade: no bucket passed; negative in every regime - off (``FADE_OFF``).

A dropped side is setup none with ``filtered`` naming the setup, the rule and the numbers, so the
card can say why. The kept long sides are scored again on the same evidence: ``EDGES`` (strength
against the index, the 20-day line rising, volume above its average) are added to the conditions
as ``how: "edge"`` and desk.playbook._score gives them three quarters of the score - on the kept
trades the held-out R by the number met was -0.016 / -0.076 / +0.050 / +0.265R (0 · 1 · 2 · 3).

Measured 2026-11-13 (swing, the basket, 10bp a side; `python -m desk.cli backtest --basket`):
held-out (from 2025-10-09) -0.035R → +0.022R per fill (plans 1058 → 695), training -0.065R → 0.000R,
the deepest fall of the basket's running R -161.6R → -68.5R; pullback buy -0.072R → +0.060R,
dip-catch -0.098R → -0.047R. The table rebuilt on it (`python -m desk.cli calibrate`, held-out from
2024-06-19) reads R -0.029 / -0.101 / +0.022 / +0.193 by band 0 · 40 · 60 · 80. Only the swing frame
was measured: the other horizons are left as they are. Pure functions; the features read the plan's own bars only.

#432: a live plan's last daily bar can be the session still trading (or not open yet - a US pre-market
bar). Its volume so far against a whole day's average read 0.06x before the open and dropped a
dip-catch as "dry". ``elapsed`` (desk.session_progress) says how much of that session has passed:
before the open (or too little of it) the volume is not judged - the dry-volume rule keeps the side and
the volume edge is ``pending``, counted as not met (the bands stay "edges met") - and later the volume
is scaled to a whole day by the time passed. The backtest's bars are finished sessions (``elapsed``
None), so its numbers do not move.
"""

from __future__ import annotations

from . import entry_features, horizons

ENABLED = True
HORIZONS = ("swing",)
FADE_OFF = True
REL20_MIN = 0.145        # pullback: 20-day return above the index's by this much...
RET20_MIN = 0.20         # ...or this much on its own
DRY_VOL = 0.82           # dip-catch: the plan day's volume under this x its 20-day average
# #166 stage 6: a dip-catch against the trend (desk.direction leans short) only while the 20-day line
# is falling - ``slope20`` (the line against itself 5 sessions ago) at or under this. A shallow dip in
# a downtrend whose 20-day line is still flat is not a sell-off to catch. Swing basket 2019-06-, costs
# in, gated, cut 2024-08-01: pausing them took the training dates -0.035R -> -0.018R (2,371 -> 2,220
# fills), every year block better or the same but 2024 (+0.056 -> +0.046R). Chosen on the training
# dates only (the best training mean whose worst year block lost under 0.02R; -0.02 and the 20-day
# return cuts lost 0.03R in 2024). Held-out +0.027R -> +0.027R (1,052 -> 1,005 fills); crash 2022
# -0.132 -> -0.104R, 2020-03 and 2025-04 the same.
DIP_AGAINST_SLOPE: float | None = -0.01
EDGES = (                # (id, feature, at least, label) - the score's evidence on a kept long
    ("edge_rel_strength", "rel20", REL20_MIN, "지수보다 강함(20일)"),
    ("edge_trend", "slope20", 0.046, "20일선 상승"),
    ("edge_volume", "vol_ratio", 1.08, "거래량 평균 이상"),
)
VOL_MIN_ELAPSED = 0.15   # #432: less of the session than this has passed - its volume is not judged yet


def volume_note(raw: float | None, elapsed: float | None) -> str | None:
    """#432: what a live plan did with a session still trading, for the card and the chat."""
    if elapsed is None:
        return None
    if elapsed < VOL_MIN_ELAPSED or raw is None:
        return "오늘 거래량은 아직 집계 중(장 전 · 장 초반) — 거래량 조건은 판정에서 뺌"
    return (f"오늘 거래량은 진행 중 — 경과 시간({elapsed:.0%}) 비례로 하루치로 환산해 비교"
            f"(지금까지 {raw:.2f}배 → 환산 {raw / elapsed:.2f}배)")


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:+.1f}%"


def verdict(side: str, setup: str, f: dict, direction: str | None = None) -> dict | None:
    """``filtered`` for a side the rules drop, None for one they keep. ``direction``: the side against
    desk.direction (with | against | neutral), set by desk.direction.apply before this runs."""
    if side == "short" and setup == "fade" and FADE_OFF:
        return {"setup": setup, "rule": "fade_off",
                "reason": "숏 페이드는 백테스트에서 모든 국면 음수 — 기본으로 끔"}
    if side == "long" and setup == "pullback":
        rel, ret = f.get("rel20"), f.get("ret20")
        if (rel is not None and rel >= REL20_MIN) or (ret is not None and ret >= RET20_MIN):
            return None
        return {"setup": setup, "rule": "pullback_strength", "values": {"rel20": rel, "ret20": ret},
                "reason": f"눌림목은 추세가 강할 때만 — 지수 대비 20일 {_pct(rel)}(기준 {_pct(REL20_MIN)} 이상), "
                          f"20일 수익률 {_pct(ret)}(기준 {_pct(RET20_MIN)} 이상)"}
    if side == "long" and setup == "dip_catch":
        slope = f.get("slope20")
        if (direction == "against" and DIP_AGAINST_SLOPE is not None and slope is not None
                and slope > DIP_AGAINST_SLOPE):
            return {"setup": setup, "rule": "dip_against_flat", "values": {"slope20": slope},
                    "reason": f"하락 추세 속 하따는 20일선이 꺾여 내려갈 때만 — 20일선 5일 기울기 {_pct(slope)}"
                              f"(기준 {_pct(DIP_AGAINST_SLOPE)} 이하)"}
        vol = f.get("vol_ratio")
        if vol is None or vol >= DRY_VOL:
            return None
        return {"setup": setup, "rule": "dip_dry_volume", "values": {"vol_ratio": vol},
                "reason": f"거래량이 20일 평균의 {vol:.2f}배 — 저점을 사는 손이 아직 없음(기준 {DRY_VOL}배 이상)"}
    return None


def edge_conditions(f: dict, pending: frozenset[str] = frozenset()) -> list[dict]:
    """``pending``: features not judged yet (#432, today's volume before the open) - shown, met None,
    and counted as not met by desk.playbook._score."""
    out = []
    for cid, name, at_least, label in EDGES:
        v = f.get(name)
        if name in pending:
            out.append({"id": cid, "met": None, "how": "edge", "value": None, "pending": True,
                        "label": f"{label}(계획 시점 장 전 · 장 초반이라 판정 안 함)"})
            continue
        out.append({"id": cid, "met": v is not None and v >= at_least, "how": "edge", "value": v, "label": label})
    return out


def apply(pb: dict | None, daily: list[dict], weekly: list[dict] | None, risk: dict | None, horizon: str,
          calibrate: bool = True, elapsed: float | None = None) -> dict | None:
    """The playbooks with the sides the rules drop set to none (``filtered``) and the kept longs
    scored on ``EDGES``. ``calibrate`` looks the desk's record up again at the new score.
    ``elapsed``: desk.session_progress.elapsed of the last daily bar on a live plan (#432); None for
    finished bars."""
    from . import playbook

    if not ENABLED or not pb or horizon not in HORIZONS:
        return pb
    f = entry_features.compute(daily, weekly, risk) if daily else {}
    pending: frozenset[str] = frozenset()
    out = dict(pb)
    if elapsed is not None:
        raw = f.get("vol_ratio")
        out["volume_note"] = volume_note(raw, elapsed)
        if raw is None or elapsed < VOL_MIN_ELAPSED:
            f = {**f, "vol_ratio": None}
            pending = frozenset({"vol_ratio"})
        else:
            f = {**f, "vol_ratio": round(raw / elapsed, 2)}
    for name in ("long", "short"):
        s = out.get(name) or {}
        setup = s.get("setup")
        if setup in (None, "none", "value_swing"):
            continue
        cut = verdict(name, setup, f, s.get("direction"))
        if cut:
            out[name] = {**playbook._none_side(cut["reason"], s.get("size_hint") or "half",
                                               bool(s.get("flat_by_close"))),
                         "direction": s.get("direction"), "filtered": cut}
            continue
        if name == "long":
            conditions = [c for c in s.get("conditions") or [] if c.get("how") != "edge"] + edge_conditions(f, pending)
            out[name] = {**s, "conditions": conditions,
                         "score": playbook._score((s.get("zone") or {}).get("score") or 0, conditions)}
    if calibrate:
        playbook.calibrate_sides(out, horizons.get(horizon))
    return out
