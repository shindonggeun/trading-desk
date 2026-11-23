"""Visibility rules for stored analysis runs and their derived data."""

from collections.abc import Mapping

from sqlalchemy import false, func, or_, true
from sqlalchemy.sql import ColumnElement

VISIBILITY_WRITER = "_run_visibility_writer"
VISIBILITY_WORKER = "_run_visibility_worker"
LEGACY_WRITER = "1"
PROTECTED_WRITER = "2"


def protected_submission(options):
    return options[VISIBILITY_WRITER].as_string() == PROTECTED_WRITER


def protected_execution(options):
    return protected_submission(options) & (options[VISIBILITY_WORKER].as_string() == PROTECTED_WRITER)


def legacy_submission(options):
    writer = options[VISIBILITY_WRITER].as_string()
    return or_(writer.is_(None), writer == LEGACY_WRITER)


def proven_public_run(runs):
    return protected_execution(runs.c.options) & shared_run_rows(runs.c.is_shareable)


def account_run(options) -> ColumnElement[bool]:
    """#775 a run that read the broker account (``use_portfolio``) - the owner's alone in legacy mode.

    Never NULL, so ``not_()`` of it keeps every other run. The same decision as reads_account on a
    loaded row; the server writes the option as a JSON boolean (desk.runner.RunRequest).
    """
    return func.coalesce(options["use_portfolio"].as_boolean(), false()) == true()


def reads_account(run: Mapping[str, object] | None) -> bool:
    """account_run for a stored run already read (Store.get_run)."""
    options = run.get("options") if run is not None else None
    return isinstance(options, Mapping) and options.get("use_portfolio") is True


def visibility_for_new_run(use_portfolio: object, *, source_visibility: tuple[object, ...] | None = None) -> bool | None:
    """Classify a new run once the writer knows every derived input.

    An empty tuple certifies the absence of derived inputs. None means
    provenance was not established. Callers obtain markers from stored sources.
    """
    if use_portfolio is True:
        return False
    if use_portfolio is not False or not isinstance(source_visibility, tuple):
        return None
    return True if all(source is True for source in source_visibility) else None


def _authenticated(viewer_id: int | None) -> bool:
    return type(viewer_id) is int and viewer_id > 0


def _is_owner(viewer_id: int | None, owner_id: int | None) -> bool:
    return type(owner_id) is int and viewer_id == owner_id


def can_access_run(
    run: Mapping[str, object] | None,
    *,
    viewer_id: int | None,
    owner_id: int | None,
) -> bool:
    """Use authenticated and owner IDs resolved by the server.

    A missing run or session is denied. A stored run with missing or uncertain
    metadata remains accessible to the desk owner.
    """
    return run is not None and _authenticated(viewer_id) and (
        _is_owner(viewer_id, owner_id) or run.get("is_shareable") is True
    )


def shared_run_rows(column: ColumnElement[bool]) -> ColumnElement[bool]:
    """Use the same explicit-true condition for public inputs and run queries."""
    return column == true()


def visible_run_rows(
    column: ColumnElement[bool],
    *,
    viewer_id: int | None,
    owner_id: int | None,
) -> ColumnElement[bool]:
    """Apply before pagination, row counts or account-scoped aggregates."""
    if not _authenticated(viewer_id):
        return false()
    if _is_owner(viewer_id, owner_id):
        return true()
    return shared_run_rows(column)
