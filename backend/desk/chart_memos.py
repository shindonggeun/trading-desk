"""Private notes for exact chart products (#925)."""

from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, delete, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Engine, RowMapping

from .db import aware, chart_memos

PRIVATE_HEADERS = {"Cache-Control": "private, no-store"}
INVALID_REQUEST = "invalid chart memo request"
MAX_TEXT_LENGTH = 2000


class ChartMemoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(strict=True, max_length=MAX_TEXT_LENGTH)


def memo_ticker(ticker: str) -> str:
    ticker = ticker.strip()
    if (not ticker or len(ticker) > 20 or not ticker.isascii()
            or not all(c.isalnum() or c in ".-^=" for c in ticker)
            or not any(c.isalnum() for c in ticker)):
        raise HTTPException(422, "invalid chart ticker", headers=PRIVATE_HEADERS)
    return ticker.upper()


def _memo_user_id(
    request: Request,
    # 64 username characters, each up to four percent-encoded UTF-8 bytes.
    account: Annotated[str, Header(alias="X-Desk-Memo-Account", min_length=1, max_length=768)],
) -> int:
    try:
        expected_account = unquote(account, errors="strict")
    except UnicodeDecodeError:
        raise HTTPException(422, INVALID_REQUEST, headers=PRIVATE_HEADERS) from None
    user = request.state.user
    if expected_account != user.username:
        raise HTTPException(409, "chart memo account changed", headers=PRIVATE_HEADERS)
    return user.id


def _payload(ticker: str, row: RowMapping | None = None) -> dict:
    memo = None
    if row is not None:
        memo = {
            "text": row["text"],
            "created_at": aware(row["created_at"]).astimezone(UTC).isoformat(),
            "updated_at": aware(row["updated_at"]).astimezone(UTC).isoformat(),
            "source": "user",
        }
    return {"ticker": ticker, "memo": memo}


class ChartMemos:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get(self, user_id: int, ticker: str) -> dict:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(chart_memos).where(chart_memos.c.user_id == user_id, chart_memos.c.ticker == ticker)
            ).mappings().one_or_none()
        return _payload(ticker, row)

    def save(self, user_id: int, ticker: str, text: str) -> dict:
        # PostgreSQL text rejects NUL and unpaired surrogates cannot be encoded as UTF-8.
        if "\x00" in text or any("\ud800" <= c <= "\udfff" for c in text):
            raise HTTPException(422, "invalid chart memo text", headers=PRIVATE_HEADERS)
        text = text.strip()
        with self.engine.begin() as conn:
            if not text:
                conn.execute(
                    delete(chart_memos).where(chart_memos.c.user_id == user_id, chart_memos.c.ticker == ticker)
                )
                return _payload(ticker)
            stamp = datetime.now(UTC)
            insert = postgresql.insert if conn.dialect.name == "postgresql" else sqlite.insert
            stmt = insert(chart_memos).values(user_id=user_id, ticker=ticker, text=text,
                                              created_at=stamp, updated_at=stamp)
            row = conn.execute(stmt.on_conflict_do_update(
                index_elements=[chart_memos.c.user_id, chart_memos.c.ticker],
                set_={
                    "text": stmt.excluded.text,
                    # A waiting writer's clock can precede the row it finds.
                    "updated_at": case(
                        (stmt.excluded.updated_at >= chart_memos.c.updated_at, stmt.excluded.updated_at),
                        else_=chart_memos.c.updated_at,
                    ),
                },
            ).returning(chart_memos)).mappings().one()
        return _payload(ticker, row)


class _PrivateRoute(APIRoute):
    def get_route_handler(self):
        handle = super().get_route_handler()

        async def handle_private_request(request: Request):
            try:
                return await handle(request)
            except RequestValidationError:
                # FastAPI's default response includes the rejected input, which can contain the memo.
                return JSONResponse({"detail": INVALID_REQUEST},
                                    status_code=422, headers=PRIVATE_HEADERS)

        return handle_private_request


def create_router(engine: Engine) -> APIRouter:
    router = APIRouter(route_class=_PrivateRoute)
    memos = ChartMemos(engine)

    @router.get("/api/chart-memo")
    def get_chart_memo(
        user_id: Annotated[int, Depends(_memo_user_id)],
        ticker: Annotated[str, Depends(memo_ticker)],
    ):
        return JSONResponse(memos.get(user_id, ticker), headers=PRIVATE_HEADERS)

    @router.put("/api/chart-memo")
    def save_chart_memo(
        body: ChartMemoInput,
        user_id: Annotated[int, Depends(_memo_user_id)],
        ticker: Annotated[str, Depends(memo_ticker)],
    ):
        return JSONResponse(memos.save(user_id, ticker, body.text), headers=PRIVATE_HEADERS)

    return router
