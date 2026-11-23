"""Count and isolate legacy account artifacts outside the application lifecycle."""

from __future__ import annotations

import argparse
import errno
import json
import os
import re
import stat
import sys
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from sqlalchemy import create_engine, false, func, or_, select, update
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.pool import NullPool

from . import roles
from .db import account_owner_binding, market_alerts, privacy_policy, run_submissions, runs, users
from .privacy import (
    ISOLATED,
    LEGACY,
    PROTECTED,
    PolicyChanged,
    PolicyEpoch,
    PrivacyPolicyStore,
    ProcessPolicy,
)
from .run_access import account_run, legacy_submission, protected_execution, protected_submission

_MEMORY_VOLUME = PurePosixPath("/home/app/.tradingagents")
_INSPECT_LIMIT = 1024 * 1024


class InvalidArguments(ValueError):
    pass


class InspectMismatch(ValueError):
    pass


class UnsafeMemoryPath(ValueError):
    pass


class ArchiveRequiresPosix(OSError):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse errors normally include the rejected option or value verbatim.
        raise InvalidArguments from None


def _arguments(argv):
    parser = Parser(prog="python -m desk.privacy_admin", description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status", allow_abbrev=False)
    status.add_argument("--inspect-stdin", action="store_true", help="verify Docker inspect JSON from standard input")
    hide = commands.add_parser("hide-legacy", allow_abbrev=False)
    hide.add_argument("--archive-memory", action="store_true", help="select memory archival instead of DB updates")
    hide.add_argument("--apply", action="store_true", help="apply the selected operation in the expected isolation")
    hide.add_argument("--epoch", type=int)
    hide.add_argument("--confirm", action="store_true")
    rollback = commands.add_parser("rollback", allow_abbrev=False)
    rollback.add_argument("--epoch", type=int, required=True)
    rollback.add_argument("--confirm", action="store_true", required=True)
    args = parser.parse_args(argv)
    if args.command != "status" and args.epoch is not None and args.epoch < 0:
        raise InvalidArguments
    if args.command == "hide-legacy" and (args.apply != args.confirm or args.apply != (args.epoch is not None)):
        raise InvalidArguments
    return args


def _engine(dsn: str, *, write: bool):
    url = make_url(dsn)
    if url.get_backend_name() == "sqlite":
        db = url.database
        if not db or db in (":memory:", "file::memory:") or url.query.get("mode") == "memory":
            raise ValueError("existing database required")
        if {"immutable", "nolock", "vfs"} & url.query.keys():
            raise ValueError("SQLite locking is required")
        uri = db if db.startswith("file:") and url.query.get("uri") == "true" else Path(db).absolute().as_uri()
        url = url.set(database=uri).update_query_dict({"mode": "rw" if write else "ro", "uri": "true"})
    elif url.get_backend_name() != "postgresql":
        raise ValueError("unsupported database")
    # The app's normal SQLite engine creates directories and sets WAL mode on connect.
    return create_engine(url, poolclass=NullPool, hide_parameters=True)


def _memory_path() -> Path:
    from tradingagents.default_config import build_default_config

    return Path(build_default_config()["memory_log_path"]).expanduser().absolute()


def _memory_sources(path):
    if not path.is_absolute() or not path.name or ".." in path.parts:
        raise UnsafeMemoryPath
    return path, path.with_name(f"{path.stem}.public{path.suffix}")


def _check_stat(info, kind):
    if info is not None and (not kind(info.st_mode) or
                             getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise UnsafeMemoryPath
    return info


def _lstat(path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _memory_count(path) -> int:
    sources = _memory_sources(path)
    # Metadata only. Inspect parent components before checking either exact file.
    for directory in reversed(path.parents):
        if _check_stat(_lstat(directory), stat.S_ISDIR) is None:
            return 0
    return sum(_check_stat(_lstat(source), stat.S_ISREG) is not None for source in sources)


def _trusted_archive_directory(fd: int, directory: Path, *, final: bool) -> None:
    info = os.fstat(fd)
    if info.st_uid not in (0, os.geteuid()):
        raise UnsafeMemoryPath
    if info.st_mode & 0o022 and not (not final and directory == Path("/tmp") and
                                      info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
        raise UnsafeMemoryPath


@contextmanager
def _open_archive_parent(path: Path):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = Path(path.anchor)
    fd = os.open(directory, flags)
    try:
        _trusted_archive_directory(fd, directory, final=directory == path)
        for part in path.parts[1:]:
            try:
                child = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                yield None
                return
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise UnsafeMemoryPath from None
                raise
            os.close(fd)
            fd = child
            directory /= part
            _trusted_archive_directory(fd, directory, final=directory == path)
        yield fd
    finally:
        os.close(fd)


def _entry(fd: int, name: str):
    try:
        info = _check_stat(os.stat(name, dir_fd=fd, follow_symlinks=False), stat.S_ISREG)
        if info.st_uid not in (0, os.geteuid()):
            raise UnsafeMemoryPath
        return info
    except FileNotFoundError:
        return None


def _identity(info):
    return info.st_dev, info.st_ino


def _permission_is_private(info):
    return stat.S_IMODE(info.st_mode) & ~0o600 == 0


def _archive_candidate(fd: int, source: str, destination: str):
    active, archived = _entry(fd, source), _entry(fd, destination)
    if archived is None:
        if active is not None and active.st_nlink != 1:
            raise UnsafeMemoryPath
    elif not _permission_is_private(archived):
        raise UnsafeMemoryPath
    elif active is None:
        if archived.st_nlink != 1:
            raise UnsafeMemoryPath
    elif _identity(active) != _identity(archived) or active.st_nlink != 2:
        raise UnsafeMemoryPath
    return active, archived


def _archive_one(fd, source, destination, active, archived):
    if archived is None:
        file_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            info = _check_stat(os.fstat(file_fd), stat.S_ISREG)
            if _identity(info) != _identity(active) or info.st_nlink != 1:
                raise UnsafeMemoryPath
            os.fchmod(file_fd, stat.S_IMODE(info.st_mode) & 0o600)
            os.fsync(file_fd)
            # The new name must never replace pre-existing data. If unlink fails,
            # a later invocation may complete the move only for this same inode.
            os.link(source, destination, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        finally:
            os.close(file_fd)
    os.fsync(fd)
    current, archived = _archive_candidate(fd, source, destination)
    if current is None or archived is None or _identity(current) != _identity(active):
        raise UnsafeMemoryPath
    os.unlink(source, dir_fd=fd)
    os.fsync(fd)


def _archive_memory(path: Path) -> int:
    if os.name != "posix":
        raise ArchiveRequiresPosix
    sources = _memory_sources(path)
    with _open_archive_parent(path.parent) as fd:
        if fd is None:
            return 0
        names = [(source.name, f"{source.name}.legacy-archive") for source in sources]
        candidates = [(source, destination, *_archive_candidate(fd, source, destination))
                      for source, destination in names]
        for source, destination, active, archived in candidates:
            if active is not None:
                _archive_one(fd, source, destination, active, archived)
        return sum(active is not None for _, _, active, _ in candidates)


def _inspect_env(entries):
    if not isinstance(entries, list):
        raise InspectMismatch
    env = {}
    for entry in entries:
        name, separator, value = entry.partition("=")
        if not separator or not name or name in env:
            raise InspectMismatch
        env[name] = value
    return env


def _inspect_volume(mounts, memory):
    # The supported Compose topology mounts only this data volume. Additional
    # mounts could shadow either the memory files or the approved image's code.
    if not isinstance(mounts, list) or len(mounts) != 1:
        raise InspectMismatch
    (mount,) = mounts
    target = PurePosixPath(mount["Destination"])
    source = PurePosixPath(mount["Source"])
    if (target != _MEMORY_VOLUME or not all(path.is_relative_to(target) for path in _memory_sources(memory)) or
            mount["Type"] != "volume" or mount["RW"] is not True or not isinstance(mount["Name"], str) or
            not mount["Name"] or not source.is_absolute() or ".." in source.parts):
        raise InspectMismatch
    return mount["Name"], mount["Source"]


def _role_command(role: str) -> list[str]:
    command = ["uvicorn", "desk.api:create_app", "--factory", "--host"]
    if role in ("all", roles.API):
        command += ["0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
    else:
        command += ["127.0.0.1", "--port", "8000"]
    return command + ["--timeout-graceful-shutdown", "20"]


def _inspect_topology(dsn: str) -> str:
    try:
        payload = sys.stdin.read(_INSPECT_LIMIT + 1)
        if len(payload) > _INSPECT_LIMIT:
            raise InspectMismatch
        records = json.loads(payload)
        if not isinstance(records, list) or len(records) not in (1, 3):
            raise InspectMismatch
        if len(records) == 3 and make_url(dsn).get_backend_name() != "postgresql":
            raise InspectMismatch
        local_path = PurePosixPath(_memory_path().as_posix())
        seen_ids, seen_roles = set(), set()
        expected = None
        for record in records:
            identity, image, config = record["Id"], record["Image"], record["Config"]
            runtime = record["State"]
            if (not re.fullmatch(r"[0-9a-f]{64}", identity) or identity in seen_ids or
                    not re.fullmatch(r"sha256:[0-9a-f]{64}", image) or config["User"] != "app"):
                raise InspectMismatch
            if runtime["Running"] is not True or runtime.get("Paused") or runtime.get("Restarting"):
                raise InspectMismatch
            env = _inspect_env(config["Env"])
            role = roles.parse(env.pop("DESK_ROLE", None))
            if role in seen_roles or env.get("DESK_DATABASE_URL") != dsn:
                raise InspectMismatch
            if config["Entrypoint"] not in (None, []) or config["Cmd"] != _role_command(role):
                raise InspectMismatch
            home = PurePosixPath(env.get("HOME", "/home/app"))
            memory = PurePosixPath(env.get("TRADINGAGENTS_MEMORY_LOG_PATH") or
                                   home / ".tradingagents/memory/trading_memory.md")
            if memory != local_path:
                raise InspectMismatch
            volume = _inspect_volume(record["Mounts"], memory)
            # In this Compose definition only DESK_ROLE differs among the three
            # backend environments. Compare values in memory and report only status.
            reference = image, volume, env
            if expected is not None and reference != expected:
                raise InspectMismatch
            expected = reference
            seen_ids.add(identity)
            seen_roles.add(role)
        if seen_roles == {"all"}:
            return "all"
        if seen_roles == {roles.API, roles.COLLECTOR, roles.ANALYST}:
            return "split"
    except (AttributeError, KeyError, OSError, TypeError, ValueError, RecursionError):
        raise InspectMismatch from None
    raise InspectMismatch


def _read_epoch(conn: Connection) -> PolicyEpoch:
    row = conn.execute(select(privacy_policy.c.mode, privacy_policy.c.epoch).where(privacy_policy.c.id == 1)).one_or_none()
    if row is None or row.mode not in (LEGACY, ISOLATED, PROTECTED) or type(row.epoch) is not int or row.epoch < 0:
        raise PolicyChanged
    return PolicyEpoch(*row)


def _lock_isolation(conn: Connection, policy: ProcessPolicy) -> None:
    if conn.dialect.name == "sqlite":
        policy.lock(conn)
    elif conn.scalar(select(privacy_policy.c.epoch).where(
        privacy_policy.c.id == 1, privacy_policy.c.mode == policy.expected.mode,
        privacy_policy.c.epoch == policy.expected.epoch,
    ).with_for_update()) is None:
        # Exclusive on PostgreSQL to serialize two admin operations as well as CAS.
        raise PolicyChanged


def _legacy_candidates():
    return account_run(runs.c.options) & legacy_submission(runs.c.options) & runs.c.is_shareable.is_not(False)


def _ownerless_account_runs():
    # As for protected reads, queued runs need submission evidence. A run that
    # has started also needs a protected worker stamp and a current owner match.
    protected_writer = or_(
        (runs.c.status == "queued") & protected_submission(runs.c.options),
        protected_execution(runs.c.options),
    )
    bound = select(run_submissions.c.run_id).select_from(run_submissions.join(account_owner_binding, (
        (account_owner_binding.c.id == 1)
        & (account_owner_binding.c.user_id == run_submissions.c.submitted_by_user_id)
        & (account_owner_binding.c.generation == run_submissions.c.owner_generation)
    ))).where(
        run_submissions.c.run_id == runs.c.id, run_submissions.c.non_account_submission == false(),
    ).correlate(runs).exists()
    return account_run(runs.c.options) & (protected_writer & bound).is_not(True)


def _status(conn: Connection, policy: PolicyEpoch, topology: str):
    bound_user = select(account_owner_binding.c.user_id).where(account_owner_binding.c.id == 1).scalar_subquery()
    account_rows, occupied, same_owner = conn.execute(select(
        func.count(users.c.id), bound_user.is_not(None), bound_user == func.min(users.c.id),
    )).one()
    return {
        "mode": policy.mode, "epoch": policy.epoch, "role_configuration": topology,
        "account_rows": account_rows, "binding_occupied": occupied, "legacy_owner_matches_binding": same_owner,
        "ownerless_account_runs": conn.scalar(select(func.count()).select_from(runs).where(_ownerless_account_runs())),
        "legacy_holding_alert_rows": conn.scalar(select(func.count()).select_from(market_alerts).where(
            market_alerts.c.kind == "holding_alert",
        )),
        "legacy_memory_files": _memory_count(_memory_path()),
    }


def _execute(args, dsn: str):
    topology = "unverified"
    if args.command == "status" and args.inspect_stdin:
        topology = _inspect_topology(dsn)
    write = args.command == "rollback" or (args.command == "hide-legacy" and args.apply)
    engine = _engine(dsn, write=write)
    store = PrivacyPolicyStore(engine)
    try:
        if args.command == "rollback":
            policy = store.transition(PolicyEpoch(PROTECTED, args.epoch), ISOLATED)
            if policy is None:
                raise PolicyChanged
            return {"mode": policy.mode, "epoch": policy.epoch}
        with engine.begin() as conn:
            if write:
                policy = PolicyEpoch(ISOLATED, args.epoch)
                _lock_isolation(conn, ProcessPolicy(store, policy))
                if args.archive_memory:
                    archived = _archive_memory(_memory_path())
                    return {"mode": policy.mode, "epoch": policy.epoch, "archived_legacy_memory_files": archived}
                update_result = conn.execute(update(runs).where(_legacy_candidates()).values(is_shareable=False))
                return {"mode": policy.mode, "epoch": policy.epoch, "hidden_legacy_account_runs": update_result.rowcount}
            if conn.dialect.name == "sqlite":
                conn.exec_driver_sql("BEGIN")
            else:
                conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            policy = _read_epoch(conn)
            if args.command == "status":
                return _status(conn, policy, topology)
            result = {"mode": policy.mode, "epoch": policy.epoch, "legacy_memory_files": _memory_count(_memory_path())}
            if not args.archive_memory:
                result["legacy_account_run_candidates"] = conn.scalar(
                    select(func.count()).select_from(runs).where(_legacy_candidates()),
                )
            return result
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    try:
        args = _arguments(argv)
        # Import locally so .env parsing (when used locally) is covered by the
        # same redaction boundary as the DSN and TradingAgents configuration.
        from .settings import load

        result = _execute(args, load().database_url)
    except InvalidArguments:
        print(json.dumps({"error": "invalid_arguments"}))
        return 2
    except InspectMismatch:
        result = {"error": "role_configuration_mismatch"}
    except PolicyChanged:
        result = {"error": "policy_mismatch"}
    except UnsafeMemoryPath:
        result = {"error": "unsafe_memory_path"}
    except ArchiveRequiresPosix:
        result = {"error": "archive_requires_posix"}
    except KeyboardInterrupt:
        print(json.dumps({"error": "interrupted"}))
        return 130
    except Exception:  # noqa: BLE001 - prevent incidental secrets in all CLI tracebacks
        # Driver, configuration and filesystem errors can all contain sensitive
        # values and paths. Keep tracebacks and exception strings off this CLI.
        result = {"error": "unavailable"}
    print(json.dumps(result, sort_keys=True))
    return int("error" in result)


if __name__ == "__main__":
    raise SystemExit(main())
