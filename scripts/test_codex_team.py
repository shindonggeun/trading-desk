import concurrent.futures
import os
from pathlib import Path
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
import uuid

import codex_team as team


class TeamTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "team.sqlite"
        self.store = team.Team(self.path)
        self.addCleanup(self.store.close)
        self.supervisor, self.worker, self.other = [str(uuid.uuid4()) for _ in range(3)]
        self.store.join("supervisor", self.supervisor, self.temp.name)
        self.store.join("S1", self.worker, self.temp.name)

    def send(self, **kwargs):
        return self.store.send("supervisor", self.supervisor, "S1", "한국어 인계", **kwargs)

    def test_default_state_path_is_shared_by_real_git_worktrees(self):
        repo = Path(self.temp.name) / "renamed-clone"
        linked = Path(self.temp.name) / "linked"
        subprocess.run(["git", "init", "-b", "develop", str(repo)], check=True, capture_output=True)
        (repo / ".gitignore").write_bytes((team.ROOT / ".gitignore").read_bytes())
        subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                        "commit", "--allow-empty", "-m", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "worktree", "add", "--no-track", "-b", "linked", str(linked)],
                       cwd=repo, check=True, capture_output=True)
        ignored = [".trading-desk-codex-team/team.sqlite", ".trading-desk-codex-team/team.sqlite-wal"]
        for checkout in (repo, linked):
            with patch.dict(os.environ, {"CODEX_TEAM_STATE_DIR": ""}), patch.object(team, "ROOT", checkout):
                selected = team.state_path()
            # The DB does not exist yet, but its checkout root does.
            self.assertTrue(selected.parents[1].samefile(repo))
            self.assertEqual(Path(ignored[0]), selected.relative_to(selected.parents[1]))
            result = subprocess.run(["git", "check-ignore", "--", *ignored],
                                    cwd=checkout, check=True, capture_output=True, text=True)
            self.assertEqual(ignored, result.stdout.splitlines())

    def test_mixed_versions_share_legacy_then_require_explicit_cutover(self):
        repo = Path(self.temp.name) / "renamed-clone"
        legacy = repo.parent / ".renamed-clone-codex-team" / "team.sqlite"
        current = repo / ".trading-desk-codex-team" / "team.sqlite"
        legacy.parent.mkdir()
        legacy.write_bytes(self.path.read_bytes())
        with patch.object(team, "repository_root", return_value=repo), \
             patch.dict(os.environ, {"CODEX_TEAM_STATE_DIR": ""}):
            self.assertTrue(legacy.samefile(team.state_path()))
            with patch.object(team.sys, "argv", ["codex_team.py", "migrate-state"]), \
                 patch("builtins.print"):
                self.assertEqual(0, team.main())
            self.assertTrue(current.is_file())
            self.assertTrue(legacy.is_file())
            self.assertFalse(legacy.samefile(current))
            with self.assertRaisesRegex(ValueError, "CODEX_TEAM_STATE_DIR"):
                team.state_path()
            with patch.dict(os.environ, {"CODEX_TEAM_STATE_DIR": str(current.parent)}):
                self.assertTrue(current.samefile(team.state_path()))

    def test_migrate_state_preserves_messages_and_source(self):
        self.store.db.execute("PRAGMA journal_mode=WAL")
        mid = self.send(task_key="#734")
        self.store.ack("S1", self.worker, mid, "received")
        self.assertTrue(self.path.with_name("team.sqlite-wal").is_file())
        destination = Path(self.temp.name) / "new" / "team.sqlite"
        team.migrate_state(self.path, destination)
        copied = team.Team(destination)
        try:
            row = copied.inbox("S1", self.worker)[0]
            self.assertEqual((mid, "한국어 인계", "received"),
                             (row["id"], row["body"], row["state"]))
            self.assertEqual(self.supervisor, copied.member("supervisor")["thread"])
        finally:
            copied.close()
        self.assertTrue(self.path.is_file())

    def test_migrate_state_refuses_existing_destination(self):
        self.send()
        destination = Path(self.temp.name) / "new" / "team.sqlite"
        other = team.Team(destination)
        other.close()
        with self.assertRaisesRegex(ValueError, "이미"):
            team.migrate_state(self.path, destination)
        self.assertTrue(self.path.is_file())
        untouched = team.Team(destination)
        try:
            self.assertEqual(0, untouched.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
        finally:
            untouched.close()

    def test_migrate_state_rejects_corrupt_source_without_destination(self):
        source = Path(self.temp.name) / "broken.sqlite"
        source.write_bytes(b"not a database")
        destination = Path(self.temp.name) / "new" / "team.sqlite"
        with self.assertRaises(sqlite3.DatabaseError):
            team.migrate_state(source, destination)
        self.assertFalse(destination.exists())
        self.assertEqual([], list(destination.parent.iterdir()))

    def test_migrate_state_cleans_staging_on_windows_publication_failure(self):
        self.send()
        before = self.path.read_bytes()
        destination = Path(self.temp.name) / "new" / "team.sqlite"
        with patch.object(team.os, "link", side_effect=PermissionError(13, "Access is denied")):
            with self.assertRaises(PermissionError):
                team.migrate_state(self.path, destination)
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual([], list(destination.parent.iterdir()))

    def test_migrate_state_preserves_a_destination_created_during_copy(self):
        before = self.path.read_bytes()
        destination = Path(self.temp.name) / "new" / "team.sqlite"
        link = os.link

        def publish(staged, target):
            target.write_bytes(b"other writer")
            link(staged, target)

        with patch.object(team.os, "link", side_effect=publish):
            with self.assertRaises(FileExistsError):
                team.migrate_state(self.path, destination)
        self.assertEqual(b"other writer", destination.read_bytes())
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual([destination], list(destination.parent.iterdir()))

    def test_takeover_preserves_tasks_and_blocks_old_supervisor(self):
        mid = self.send(task_key="#582")
        with self.assertRaises(ValueError):
            self.store.join("supervisor", self.other, self.temp.name)
        self.store.join("supervisor", self.other, self.temp.name, takeover=True)
        with self.assertRaisesRegex(ValueError, "소유자"):
            self.send()
        self.assertEqual(mid, self.store.inbox("S1", self.worker)[0]["id"])
        self.assertEqual(2, self.store.member("supervisor")["generation"])

    def test_worker_takeover_reads_previous_pending_task(self):
        mid = self.send(task_key="#20")
        self.store.join("S1", self.other, self.temp.name, takeover=True)
        self.assertEqual(mid, self.store.inbox("S1", self.other)[0]["id"])
        with self.assertRaises(ValueError):
            self.store.ack("S1", self.worker, mid, "received")

    def test_same_task_cannot_be_assigned_to_two_workers(self):
        self.store.join("S2", self.other, self.temp.name)
        self.send(task_key="#20")
        with self.assertRaisesRegex(ValueError, "같은 작업"):
            self.store.send("supervisor", self.supervisor, "S2", "중복", "#20")
        with self.assertRaisesRegex(ValueError, "미완료"):
            self.send(task_key="#582")

    def test_concurrent_assignments_have_one_winner(self):
        self.store.join("S2", self.other, self.temp.name)

        def assign(role):
            store = team.Team(self.path)
            try:
                store.send("supervisor", self.supervisor, role, "경합", "#20")
                return True
            except ValueError:
                return False
            finally:
                store.close()

        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            self.assertEqual(1, sum(pool.map(assign, ["S1", "S2"])))

    def test_receipt_requires_correct_owner_and_order(self):
        mid = self.send(task_key="#20")
        for state in ("started", "completed"):
            with self.assertRaises(ValueError):
                self.store.ack("S1", self.worker, mid, state)
        for state in team.STATES:
            self.store.ack("S1", self.worker, mid, state)
        self.assertEqual([], self.store.inbox("S1", self.worker))
        self.assertEqual("completed", self.store.inbox("S1", self.worker, True)[0]["state"])
        self.send(task_key="#20")

    def test_worker_reports_to_new_supervisor(self):
        self.store.join("supervisor", self.other, self.temp.name, takeover=True)
        mid = self.store.send("S1", self.worker, "supervisor", "진행 보고")
        self.assertEqual(mid, self.store.inbox("supervisor", self.other)[0]["id"])
        with self.assertRaises(ValueError):
            self.store.send("S1", self.worker, "S1", "배정", "#1")

    def test_restart_keeps_unicode_and_states(self):
        mid = self.send()
        self.store.ack("S1", self.worker, mid, "received")
        other = team.Team(self.path)
        try:
            row = other.inbox("S1", self.worker)[0]
            self.assertEqual("한국어 인계", row["body"])
            self.assertEqual("received", row["state"])
        finally:
            other.close()

    def test_live_foreground_cannot_be_overwritten(self):
        token, selected = self.store.reserve("S1", self.temp.name)
        self.assertEqual(self.worker, selected)
        with self.assertRaises(ValueError):
            self.store.join("S1", self.other, self.temp.name, takeover=True)
        self.assertFalse(self.store.pulse("S1", "wrong-token"))
        self.store.release("S1", "wrong-token")
        self.assertTrue(team.live(self.store.member("S1")))
        self.store.release("S1", token)
        self.assertFalse(team.live(self.store.member("S1")))

    def test_new_foreground_binds_only_with_valid_token(self):
        token, selected = self.store.reserve("S2", self.temp.name)
        self.assertIsNone(selected)
        self.store.join("S2", self.other, self.temp.name, token=token)
        self.assertEqual(self.other, self.store.member("S2")["thread"])
        with self.assertRaises(ValueError):
            self.store.join("S2", str(uuid.uuid4()), self.temp.name, token=token)
        self.store.release("S2", token)
        with self.assertRaises(ValueError):
            self.store.join("S2", self.other, self.temp.name, token=token)

    def test_delivery_never_resumes_background_and_is_not_receipt(self):
        mid = self.send()
        token, _ = self.store.reserve("S1", self.temp.name)
        with patch.object(team, "codex", return_value="codex.cmd"), \
             patch.object(team.subprocess, "run", return_value=Mock(returncode=0)) as run:
            self.store.deliver("S1", token)
            self.store.deliver("S1", token)
        self.assertEqual(1, run.call_count)
        self.assertNotIn("--resume", run.call_args.args[0])
        row = self.store.inbox("S1", self.worker)[0]
        self.assertEqual((mid, "queued", "pending"), (row["id"], row["transport"], row["state"]))

    def test_disconnected_worker_keeps_message_without_dispatch(self):
        self.send()
        token, _ = self.store.reserve("S1", self.temp.name)
        self.store.release("S1", token)
        with patch.object(team.subprocess, "run") as run:
            self.store.deliver("S1", token)
            run.assert_not_called()
        self.assertEqual("pending", self.store.inbox("S1", self.worker)[0]["transport"])

    def test_timeout_is_unknown_and_never_automatically_replayed(self):
        self.send()
        token, _ = self.store.reserve("S1", self.temp.name)
        with patch.object(team, "codex", return_value="codex"), \
             patch.object(team.subprocess, "run", side_effect=subprocess.TimeoutExpired("codex", 20)) as run:
            self.store.deliver("S1", token)
            self.store.deliver("S1", token)
        self.assertEqual(1, run.call_count)
        self.assertEqual("unknown", self.store.inbox("S1", self.worker)[0]["transport"])

    def test_stale_heartbeat_requires_foreground_reconnect(self):
        token, _ = self.store.reserve("S1", self.temp.name)
        self.store.db.execute("UPDATE members SET heartbeat=1 WHERE role='S1'")
        self.send()
        with patch.object(team.subprocess, "run") as run:
            self.store.deliver("S1", token)
            run.assert_not_called()

    def test_launch_requires_terminal(self):
        with patch.object(team.sys.stdin, "isatty", return_value=False):
            with self.assertRaisesRegex(ValueError, "대화형"):
                team.launch(self.store, "S1", Path(self.temp.name))

    def test_launch_inherits_terminal_and_releases_even_on_spawn_failure(self):
        with patch.object(team.sys.stdin, "isatty", return_value=True), \
             patch.object(team.sys.stdout, "isatty", return_value=True), \
             patch.object(team, "codex", return_value="codex"), \
             patch.object(team.subprocess, "Popen", side_effect=OSError("spawn failed")) as popen:
            with self.assertRaises(OSError):
                team.launch(self.store, "S1", Path(self.temp.name))
        self.assertFalse(team.live(self.store.member("S1")))
        self.assertNotIn("stdout", popen.call_args.kwargs)
        self.assertNotIn("stdin", popen.call_args.kwargs)
        self.assertNotIn("shell", popen.call_args.kwargs)
        self.assertEqual(["codex", "--no-daemon", "resume", self.worker], popen.call_args.args[0][:4])

    def test_release_runs_even_when_child_shutdown_fails(self):
        child = Mock()
        child.poll.return_value = 0
        with patch.object(team.sys.stdin, "isatty", return_value=True), \
             patch.object(team.sys.stdout, "isatty", return_value=True), \
             patch.object(team, "codex", return_value="codex"), \
             patch.object(team.subprocess, "Popen", return_value=child), \
             patch.object(team, "stop_child", side_effect=RuntimeError("종료 미확인")):
            with self.assertRaisesRegex(RuntimeError, "종료 미확인"):
                team.launch(self.store, "S1", Path(self.temp.name))
        self.assertFalse(team.live(self.store.member("S1")))

    def test_join_from_other_worktree_uses_actual_cwd(self):
        with patch.object(team, "state_path", return_value=self.path), \
             patch.dict(os.environ, {"CODEX_THREAD_ID": self.worker}, clear=True), \
             patch.object(team.Path, "cwd", return_value=Path(self.temp.name)), \
             patch.object(team.sys, "argv", ["codex_team.py", "join", "S1"]):
            team.main()
        self.assertEqual(self.temp.name, self.store.member("S1")["cwd"])

    @unittest.skipUnless(os.name == "nt", "Windows 전경 프로세스 트리 검증")
    def test_real_windows_process_tree_is_stopped(self):
        import ctypes
        import sys
        script = ("import subprocess,sys,time; "
                  "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); "
                  "print(p.pid,flush=True);time.sleep(60)")
        child = subprocess.Popen([sys.executable, "-u", "-c", script], stdout=subprocess.PIPE,
                                 text=True)
        self.addCleanup(child.stdout.close)
        try:
            descendant = int(child.stdout.readline())
            team.stop_child(child)
            self.assertIsNotNone(child.poll())
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.restype = ctypes.c_void_p
            handle = kernel.OpenProcess(0x1000, False, descendant)
            if handle:
                try:
                    code = ctypes.c_ulong()
                    self.assertTrue(kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)))
                    self.assertNotEqual(259, code.value)
                finally:
                    kernel.CloseHandle(ctypes.c_void_p(handle))
        finally:
            if child.poll() is None:
                team.stop_child(child)

    def test_messages_reject_missing_identity_and_empty_content(self):
        with self.assertRaises(ValueError):
            self.store.send("supervisor", self.other, "S1", "잘못된 발신자")
        for content in ("", "가" * 11000):
            with self.assertRaises(ValueError):
                self.store.send("supervisor", self.supervisor, "S1", content)


if __name__ == "__main__":
    unittest.main()
