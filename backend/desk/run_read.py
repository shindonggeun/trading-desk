"""Account-scoped queries for the primary run read routes."""

from dataclasses import dataclass

from sqlalchemy import and_, false, func, or_, select, true
from sqlalchemy.engine import Engine, RowMapping

from .db import account_owner_binding, events, run_submissions, runs
from .run_access import shared_run_rows
from .store import _light, _run, iso

_STATUS_SUMMARY = {"queued": "progress", "running": "progress", "done": "completed", "failed": "failed"}


def _current_run_owner(viewer_id: int):
    if type(viewer_id) is not int or viewer_id < 1:
        return false()
    return select(run_submissions.c.run_id).select_from(run_submissions.join(account_owner_binding, and_(
        account_owner_binding.c.id == 1, account_owner_binding.c.user_id == viewer_id,
        account_owner_binding.c.generation == run_submissions.c.owner_generation,
    ))).where(
        run_submissions.c.run_id == runs.c.id,
        run_submissions.c.submitted_by_user_id == viewer_id,
        run_submissions.c.non_account_submission == false(),
    ).correlate(runs).exists()


def _full_read(viewer_id: int):
    if type(viewer_id) is not int or viewer_id < 1:
        return false()
    return or_(_current_run_owner(viewer_id), shared_run_rows(runs.c.is_shareable))


def _change_granted(viewer_id: int, full):
    submitted = select(run_submissions.c.run_id).where(
        run_submissions.c.run_id == runs.c.id,
        run_submissions.c.submitted_by_user_id == viewer_id,
        run_submissions.c.non_account_submission == true(),
    ).correlate(runs).exists()
    return and_(full, or_(_current_run_owner(viewer_id), submitted))


def _visible_runs(viewer_id: int):
    full = _full_read(viewer_id)
    status_only = false()
    if type(viewer_id) is int and viewer_id > 0:
        status_only = and_(
            run_submissions.c.submitted_by_user_id == viewer_id,
            run_submissions.c.non_account_submission == true(),
            runs.c.is_shareable.is_(None),
            runs.c.status.in_(tuple(_STATUS_SUMMARY)),
        )
    return select(
        runs.c.id.label("visible_id"), runs.c.status.label("visible_status"),
        runs.c.created_at.label("visible_created_at"), full.label("full_read"),
        _change_granted(viewer_id, full).label("can_delete"),
    ).select_from(runs.outerjoin(run_submissions)).where(or_(full, status_only))


def _run_view(row: RowMapping) -> dict:
    if row["full_read"]:
        return _run(row)
    return {"id": row["visible_id"], "status": _STATUS_SUMMARY[row["visible_status"]]}


@dataclass(frozen=True)
class EventBatch:
    status: str
    terminal_seq: int | None
    items: list[dict]


def last_done_seq(engine: Engine, run_id: str) -> int | None:
    """Read only the completion cursor for the legacy, unfiltered stream."""
    with engine.connect() as conn:
        return conn.scalar(select(func.max(events.c.seq)).where(events.c.run_id == run_id, events.c.type == "done"))


class RunReader:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def page(self, viewer_id: int, *, limit: int, offset: int) -> tuple[list[dict], int]:
        visible = _visible_runs(viewer_id).cte("visible_runs")
        page = select(visible).order_by(
            visible.c.visible_created_at.desc(), visible.c.visible_id,
        ).limit(limit).offset(offset).subquery()
        total = select(func.count().label("visible_total")).select_from(visible).subquery()
        # The outer row preserves the total for an empty page. Only full-read
        # rows join to a payload, so status rows never fetch plans or options.
        statement = (
            select(total.c.visible_total, page.c.visible_id, page.c.visible_status, page.c.full_read,
                   page.c.can_delete, *runs.c)
            .select_from(total.outerjoin(page, true()).outerjoin(
                runs, and_(page.c.full_read, runs.c.id == page.c.visible_id),
            ))
            .order_by(page.c.visible_created_at.desc(), page.c.visible_id)
        )
        with self.engine.connect() as conn:
            rows = conn.execute(statement).mappings().all()
        items = []
        for row in rows:
            if row["visible_id"] is not None:
                item = _light(_run_view(row))
                if row["full_read"]:
                    item["can_delete"] = bool(row["can_delete"])
                items.append(item)
        return items, rows[0]["visible_total"]

    def detail(self, viewer_id: int, run_id: str) -> dict | None:
        visible = _visible_runs(viewer_id).where(runs.c.id == run_id).subquery()
        outlook = select(events.c.data).where(
            events.c.run_id == runs.c.id, events.c.type == "outlook",
        ).order_by(events.c.seq.desc()).limit(1).correlate(runs).scalar_subquery()
        statement = select(
            visible.c.visible_id, visible.c.visible_status, visible.c.full_read, *runs.c, outlook.label("outlook"),
        ).select_from(visible.outerjoin(runs, and_(visible.c.full_read, runs.c.id == visible.c.visible_id)))
        with self.engine.connect() as conn:
            row = conn.execute(statement).mappings().one_or_none()
        if row is None:
            return None
        result = _run_view(row)
        if row["full_read"]:
            result["outlook"] = row["outlook"]
        return result

    def has_full_read(self, viewer_id: int, run_id: str) -> bool:
        with self.engine.connect() as conn:
            return conn.scalar(select(runs.c.id).where(runs.c.id == run_id, _full_read(viewer_id))) is not None

    def full_run(self, viewer_id: int, run_id: str) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(runs).where(runs.c.id == run_id, _full_read(viewer_id))).mappings().one_or_none()
        return _run(row) if row is not None else None

    def change_scope(self, viewer_id: int):
        """A recorded submitter may change their own non-account full result."""
        return _change_granted(viewer_id, _full_read(viewer_id))

    def may_change(self, viewer_id: int, run_id: str) -> bool:
        with self.engine.connect() as conn:
            return conn.scalar(select(runs.c.id).where(runs.c.id == run_id, self.change_scope(viewer_id))) is not None

    def event_batch(self, viewer_id: int, run_id: str, *, after: int, limit: int | None = None) -> EventBatch | None:
        terminal = events.alias("terminal_event")
        terminal_seq = select(func.max(terminal.c.seq)).where(
            terminal.c.run_id == runs.c.id, terminal.c.type == "done",
        ).correlate(runs).scalar_subquery()
        statement = select(runs.c.status, terminal_seq.label("terminal_seq"), *events.c).select_from(
            runs.outerjoin(events, and_(events.c.run_id == runs.c.id, events.c.seq > after)),
        ).where(runs.c.id == run_id, _full_read(viewer_id)).order_by(events.c.seq)
        if limit is not None:
            statement = statement.limit(limit)
        with self.engine.connect() as conn:
            rows = conn.execute(statement).mappings().all()
        if not rows:
            return None
        items = []
        for row in rows:
            if row["seq"] is not None:
                items.append({**{col.key: row[col.key] for col in events.c}, "ts": iso(row["ts"])})
        return EventBatch(rows[0]["status"], rows[0]["terminal_seq"], items)
