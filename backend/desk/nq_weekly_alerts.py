"""Collector and authenticated API for weekly NQ oversold observations (#914)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import HTTPException, Query, Request
from fastapi.responses import JSONResponse

from . import notify
from . import nq_weekly_signal as signal
from .notify_channels import ChannelError, ChannelRateLimited, ChannelStopped, ChannelUnknown
from .nq_weekly_ledger import CHANNELS, Ledger, Rule
from .privacy import PolicyChanged

log = logging.getLogger(__name__)
KINDS = ("nq_alert", "tone_warning")
TICK_SEC = 300
URL = "/api/nq/weekly-oversold"
CHART_URL = "/#/charts?t=NQ%3DF&iv=1w"
PRIVATE = {"Cache-Control": "private, no-store"}
LABELS = {"bollinger": "볼린저 하단", "channel": "채널 하단", "fib": "피보나치 지지"}


def message(observation: dict) -> dict:
    week = observation["week"]["start"]
    evidence = observation["evidence"]
    rsi = evidence["rsi"]
    reasons = ", ".join(f"{LABELS[key]} {evidence[key]['value']:.2f}"
                        for key in observation["matched_conditions"])
    body = (f"기준 주 {week} · 확정 NQ=F 주봉\n"
            f"Wilder RSI(14) {rsi['value']:.2f} (기준 ≤ {rsi['limit']:g})\n"
            f"지지 {len(observation['matched_conditions'])}개 충족 (최소 {observation['min_hits']}개) · {reasons}\n"
            "Yahoo 연속 선물 관찰 신호 · 롤오버 구간은 확인되지 않았습니다.")
    return {"title": "중요 · 나스닥100 선물 주봉 과매도 겹침", "body": body,
            "tag": f"nq-weekly-oversold-{week}", "url": CHART_URL}


class Service:
    def __init__(self, ledger: Ledger, prices, legacy_store, current_resources, *, now=lambda: datetime.now(UTC)):
        self.ledger = ledger
        self.prices = prices
        self.legacy_store = legacy_store
        self.current_resources = current_resources
        self.now = now

    async def weekly(self) -> dict | None:
        try:
            return await self.prices.candles("NQ=F", "1w", signal.FETCH_WEEKS)
        except Exception as exc:  # noqa: BLE001 - a market source failure is an unavailable observation
            log.warning("NQ weekly candles unavailable: %s", type(exc).__name__)
            return None

    def _target(self, rule: Rule, name: str):
        if self.ledger.policy.protected:
            resources = self.current_resources()
            if resources is None or resources.binding.user_id != rule.owner:
                return "unavailable", None
            for target in resources.destinations:
                if target.name == name:
                    return ("ready", target) if set(KINDS) <= target.kinds else ("disabled", None)
            return "unavailable", None
        channel = notify.channel(name)
        if self.legacy_store is None or channel is None or not channel.configured:
            return "unavailable", None
        if name == "kakao":
            state = channel.status()
            if not state["connected"]:
                return "unavailable", None
            if self.legacy_store.get(name)["needs_reconnect"]:
                return "needs_reconnect", None
        return ("ready", channel) if self.legacy_store.wants(name, KINDS) else ("disabled", None)

    def channels(self, rule: Rule) -> list[dict]:
        result = []
        for name in CHANNELS:
            warning = False
            try:
                state, target = self._target(rule, name)
                warning = state == "needs_reconnect" or (
                    name == "kakao" and target is not None and not self.ledger.policy.protected
                    and target.status()["needs_reconnect"]
                )
            except Exception:  # noqa: BLE001 - channel details stay inside the transport
                state = "unavailable"
            result.append({"channel": name, "status": state, "needs_reconnect": warning})
        return result

    def _deliver(self, rule: Rule, name: str, row: dict) -> None | str:
        if self.now() >= row["expires_at"]:
            return "expired"
        if not self.ledger.active(rule):
            return "superseded"
        result, target = self._target(rule, name)
        if target is None:
            return result

        def allowed() -> bool:
            return (self.ledger.active(rule) and self._target(rule, name) == ("ready", target)
                    and self.now() < row["expires_at"])

        if self.ledger.policy.protected:
            if not allowed():
                return "expired" if self.now() >= row["expires_at"] else "superseded"
            target.deliver(row["payload"])
        elif name == "kakao":
            def fence(conn):
                try:
                    current = self.ledger.locked_active(conn, rule)
                except PolicyChanged:
                    current = False
                if not current or self.now() >= row["expires_at"]:
                    raise ChannelStopped("channel authorization changed")

            target.send_once(row["payload"], allowed=allowed, fence=fence, kinds=KINDS)
        else:
            target.send_once(row["payload"], allowed=allowed)
        return None

    async def deliver(self, rule: Rule, name: str) -> None:
        row = await asyncio.to_thread(self.ledger.claim, rule, name, self.now())
        if row is None:
            return
        retry = 0.0

        def send():
            return self._deliver(rule, name, row)

        try:
            # Kakao alone shares the existing OAuth worker with connect and token refresh.
            if name == "kakao" and not self.ledger.policy.protected:
                status = await asyncio.to_thread(notify.run, send)
            else:
                status = await asyncio.to_thread(send)
            status = status or "sent"
        except ChannelRateLimited as exc:
            status, retry = "rate_limited", exc.retry_after
        except ChannelStopped:
            status = "expired" if self.now() >= row["expires_at"] else "superseded"
        except ChannelUnknown:
            status = "unknown"
        except ChannelError as exc:
            status = "unknown" if self.ledger.policy.protected or str(exc) == "timeout" else "failed"
        except Exception as exc:  # noqa: BLE001 - a failure in one channel must not skip the other
            log.warning("NQ weekly %s delivery outcome unknown: %s", name, type(exc).__name__)
            status = "unknown"
        await asyncio.to_thread(self.ledger.finish, rule, row, status, self.now(), retry)

    async def step(self) -> float:
        rule = await asyncio.to_thread(self.ledger.rule)
        if rule is None or not rule.settings["enabled"]:
            return TICK_SEC
        weekly = await self.weekly()
        now = self.now()
        observation = await asyncio.to_thread(signal.observe, weekly, rule.settings, now)
        if observation["status"] in ("matched", "not_matched"):
            payload = message(observation) if observation["status"] == "matched" else {}
            await asyncio.to_thread(self.ledger.record, rule, observation, payload, now)
        await asyncio.gather(*(self.deliver(rule, name) for name in CHANNELS))
        return TICK_SEC


def register(app, service: Service) -> None:
    @app.get(URL)
    async def nq_weekly_oversold(
        request: Request, weeks: Annotated[int, Query(ge=1, le=signal.MAX_HISTORY_WEEKS)] = signal.DEFAULT_HISTORY_WEEKS,
    ):
        rule = await asyncio.to_thread(service.ledger.rule)
        can_edit = bool(rule and rule.owner == request.state.user.id)
        settings = rule.settings if rule else signal.DEFAULT_SETTINGS
        weekly = await service.weekly()
        out = await asyncio.to_thread(signal.view, weekly, settings, service.now(), weeks=weeks)
        channels, latest = await asyncio.gather(
            asyncio.to_thread(service.channels, rule), asyncio.to_thread(service.ledger.latest, rule),
        ) if can_edit else ([], [])
        return JSONResponse({**out, "settings": settings, "revision": rule.revision if rule else 0,
                             "can_edit": can_edit, "channels": channels, "deliveries": latest}, headers=PRIVATE)

    @app.put(f"{URL}/settings")
    def nq_weekly_oversold_settings(body: dict, request: Request):
        rule = service.ledger.rule()
        if rule is None or request.state.user.id != rule.owner:
            raise HTTPException(403, "only the desk owner changes the weekly rule", headers=PRIVATE)
        try:
            settings = signal.validate_settings(body)
        except ValueError as exc:
            raise HTTPException(422, str(exc), headers=PRIVATE) from None
        saved = service.ledger.save(request.state.user.id, settings, service.now())
        if saved is None:
            raise HTTPException(403, "weekly rule owner changed", headers=PRIVATE)
        return JSONResponse({"settings": saved.settings, "revision": saved.revision}, headers=PRIVATE)
