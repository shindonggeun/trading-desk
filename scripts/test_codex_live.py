from contextlib import closing, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import queue
import struct
import threading
import time
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
import uuid

import codex_live as live
import codex_team as team


def frame(opcode, payload=b"", fin=True):
    header = bytes([(128 if fin else 0) | opcode])
    size = len(payload)
    if size < 126:
        return header + bytes([size]) + payload
    return header + bytes([126]) + struct.pack("!H", size) + payload


class WireTests(unittest.TestCase):
    def setUp(self):
        self.client = live.Client.__new__(live.Client)
        self.client.buffer = b""
        self.client.timeout = .1
        self.client.counter = 0
        self.client.chunks = queue.Queue()
        self.client.process = Mock(stdin=io.BytesIO())

    def receive(self, data):
        self.client.buffer = data
        return self.client.receive(time.monotonic() + 1)

    def sent_requests(self):
        stream = io.BytesIO(self.client.process.stdin.getvalue())
        while header := stream.read(2):
            self.assertEqual(129, header[0])
            self.assertTrue(header[1] & 128)
            size = header[1] & 127
            if size == 126:
                size = struct.unpack("!H", stream.read(2))[0]
            mask = stream.read(4)
            payload = stream.read(size)
            self.assertEqual(size, len(payload))
            yield json.loads(bytes(value ^ mask[i % 4] for i, value in enumerate(payload)))

    def test_live_dispatch_and_inbox_via_framed_rpc(self):
        for mode in ("idle", "active", "unsupported", "mismatched-turn"):
            with self.subTest(mode=mode), TemporaryDirectory() as tmp, closing(
                team.Team(Path(tmp) / "team.sqlite")
            ) as store:
                owner, worker, other = (str(uuid.uuid4()) for _ in range(3))
                store.join("supervisor", owner, team.ROOT)
                store.join("S1", worker, team.ROOT)
                mid = store.send("supervisor", owner, "S1", "시험용 배정", "test")
                row = {"id": worker, "cwd": str(team.ROOT),
                       "status": {"type": "idle" if mode == "idle" else "active"}}
                replies = [{"result": {"data": [other, worker]}}, {"result": {"thread": row}}] * 2
                if mode == "idle":
                    replies.append({"result": {"turn": {"id": "test-turn"}}})
                elif mode != "unsupported":
                    replies.extend((
                        {"result": {"data": [{"id": "test-turn", "status": "inProgress"}]}},
                        {"result": {"turnId": "other-turn" if mode == "mismatched-turn" else "test-turn"}},
                    ))
                else:
                    replies.append({"error": {"code": -32601, "message": "private server details"}})
                self.client.counter = 0
                self.client.process.stdin = io.BytesIO()
                self.client.buffer = b"".join(
                    frame(1, json.dumps({"id": i, **reply}).encode())
                    for i, reply in enumerate(replies, 1)
                )
                if mode == "unsupported":
                    with self.assertRaisesRegex(live.LiveError, "thread/turns/list.*지원하지") as error:
                        store.deliver_live(mid, self.client)
                    self.assertNotIn("private server details", str(error.exception))
                elif mode == "mismatched-turn":
                    with self.assertRaisesRegex(live.LiveError, "turn/steer.*일치"):
                        store.deliver_live(mid, self.client)
                else:
                    self.assertEqual(("test-turn", live.recipient_evidence(row)), store.deliver_live(mid, self.client))

                received = store.inbox("S1", worker)[0]
                transport = "live_accepted" if mode in ("idle", "active") else "unknown"
                self.assertEqual((mid, "시험용 배정", "pending", transport),
                                 (received["id"], received["body"], received["state"], received["transport"]))
                with self.assertRaisesRegex(ValueError, "다시 보내지"):
                    store.deliver_live(mid, self.client)
                for state in ("received", "started", "completed"):
                    store.ack("S1", worker, mid, state)
                    self.assertEqual(state, store.inbox("S1", worker, include_completed=True)[0]["state"])

                requests = list(self.sent_requests())
                lookup = [("thread/loaded/list", {}), ("thread/read", {
                    "threadId": worker, "includeTurns": False})]
                self.assertEqual(lookup * 2, [(r["method"], r["params"]) for r in requests[:4]])
                delivery = requests[4:]
                if mode != "idle":
                    self.assertEqual({
                        "id": 5, "method": "thread/turns/list", "params": {
                            "threadId": worker, "limit": 1, "sortDirection": "desc",
                            "itemsView": "notLoaded"},
                    }, delivery.pop(0))
                if mode == "unsupported":
                    self.assertEqual([], delivery)
                else:
                    request, = delivery
                    expected_method = "turn/start" if mode == "idle" else "turn/steer"
                    self.assertEqual(expected_method, request["method"])
                    expected_params = {"threadId": worker, "input": request["params"]["input"]}
                    if mode != "idle":
                        expected_params["expectedTurnId"] = "test-turn"
                    self.assertEqual(expected_params, request["params"])
                    message, = request["params"]["input"]
                    self.assertEqual("text", message["type"])
                    self.assertIn(mid, message["text"])
                    self.assertIn("시험용 배정", message["text"])

    def test_split_chunks_fragmentation_and_ping_pong(self):
        data = frame(1, b'{"a":', False) + frame(9, b"ping") + frame(0, b'1}')
        for value in data:
            self.client.chunks.put(bytes([value]))
        self.assertEqual({"a": 1}, self.receive(b""))
        self.assertEqual(138, self.client.process.stdin.getvalue()[0])

    def test_masked_outbound_frames_all_length_forms(self):
        for size in (5, 200, 65536):
            self.client.process.stdin = io.BytesIO()
            self.client._frame(1, b"x" * size)
            data = self.client.process.stdin.getvalue()
            offset = 2 if size < 126 else 4 if size < 65536 else 10
            self.assertTrue(data[1] & 128)
            mask, payload = data[offset:offset+4], data[offset+4:]
            self.assertEqual(b"x" * size, bytes(v ^ mask[i % 4] for i, v in enumerate(payload)))

    def test_rejects_oversize_reserved_and_invalid_control_frames(self):
        cases = [bytes([129, 127]) + struct.pack("!Q", live.MAX_BYTES + 1),
                 frame(0, b'{}'), frame(9, b"x", False), bytes([193, 0]),
                 bytes([129, 128]), frame(8)]
        for data in cases:
            with self.subTest(data=data[:4]), self.assertRaises(live.LiveError):
                self.receive(data)

    def test_no_data_and_closed_stream_fail_bounded(self):
        with self.assertRaisesRegex(live.LiveError, "시간 초과"):
            self.client._take(1, time.monotonic() - 1)
        self.client.chunks.put(b"")
        with self.assertRaisesRegex(live.LiveError, "종료"):
            self.client._take(1, time.monotonic() + 1)

    def test_stalled_pipe_write_terminates_only_its_proxy(self):
        release = threading.Event()
        stream = Mock()
        stream.write.side_effect = lambda data: release.wait(2)
        self.client.process.stdin = stream
        self.client.process.poll.return_value = None
        self.client.process.terminate.side_effect = release.set
        start = time.monotonic()
        with self.assertRaisesRegex(live.LiveError, "전송 시간 초과"):
            self.client._write(b"x" * 65536)
        self.assertLess(time.monotonic() - start, 1)
        self.client.process.terminate.assert_called_once()
        self.client.process.wait.assert_called_once_with(timeout=3)

    def test_rpc_ignores_notifications_and_other_request_ids(self):
        self.client.buffer = (frame(1, b'{"method":"item/event"}') +
                              frame(1, b'{"id":0,"result":{}}') +
                              frame(1, b'{"id":1,"result":{"ok":true}}'))
        self.assertEqual({"ok": True}, self.client.call("read", {}))

    def test_rpc_errors_and_malformed_response_are_not_retried(self):
        for response in (b'{"id":1,"error":{"message":"private details"}}', b'{"id":1}'):
            self.client.counter = 0
            self.client.buffer = frame(1, response)
            with self.assertRaises(live.LiveError) as caught:
                self.client.call("test", {})
            self.assertNotIn("private details", str(caught.exception))

    def test_rpc_method_not_found_reports_unsupported_without_server_details(self):
        self.client.buffer = frame(1, b'{"id":1,"error":{"code":-32601,"message":"private"}}')
        with self.assertRaisesRegex(live.LiveError, "thread/read.*지원하지") as caught:
            self.client.call("thread/read", {})
        self.assertNotIn("private", str(caught.exception))

    def test_handshake_failure_closes_proxy(self):
        child = Mock()
        child.stdout.read1.return_value = b"HTTP/1.1 403 Forbidden\r\n\r\n"
        with patch.object(live.subprocess, "Popen", return_value=child), \
             patch.object(live.Client, "_read"), \
             patch.object(live.Client, "_chunk", return_value=b"HTTP/1.1 403 Forbidden\r\n\r\n"), \
             patch.object(live.Client, "close") as close:
            with self.assertRaises(live.LiveError):
                live.Client("codex.exe")
            close.assert_called_once()


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.client = live.Client.__new__(live.Client)

    def test_not_loaded_never_reads_or_resumes(self):
        self.client.loaded = Mock(return_value=[])
        self.client.call = Mock()
        with self.assertRaisesRegex(live.LiveError, "로드되지"):
            self.client.target("old")
        self.client.call.assert_not_called()

    def test_push_refuses_mismatched_target_before_dispatch(self):
        self.client.loaded = Mock(return_value=["selected"])
        self.client.call = Mock(return_value={"thread": {"id": "other", "status": {"type": "idle"}}})
        with self.assertRaisesRegex(live.LiveError, "수신 ID"):
            self.client.push("selected", "hello")
        self.client.call.assert_called_once_with("thread/read", {
            "threadId": "selected", "includeTurns": False})

    def test_ephemeral_and_subagent_are_not_independent_targets(self):
        for field in ({"ephemeral": True}, {"source": {"subAgent": {}}}, {"source": "subAgent"},
                      {"parentThreadId": "parent"}):
            self.client.loaded = Mock(return_value=["id"])
            self.client.call = Mock(return_value={"thread": {"id": "id", **field}})
            with self.assertRaisesRegex(live.LiveError, "독립"):
                self.client.target("id")
            self.assertFalse(live.independent(field))

    def test_idle_starts_without_policy_or_cwd_override(self):
        self.client.target = Mock(return_value={"status": {"type": "idle"}})
        self.client.call = Mock(return_value={"turn": {"id": "new"}})
        self.assertEqual(("new", live.recipient_evidence(self.client.target.return_value)),
                         self.client.push("id", "hello"))
        self.client.call.assert_called_once_with("turn/start", {
            "threadId": "id", "input": [{"type": "text", "text": "hello"}]})

    def test_idle_rejects_unverifiable_turn_id(self):
        self.client.target = Mock(return_value={"status": {"type": "idle"}})
        for response in ({}, {"turn": {"id": None}}, {"turn": {"id": " "}}):
            with self.subTest(response=response):
                self.client.call = Mock(return_value=response)
                with self.assertRaisesRegex(live.LiveError, "turn/start.*turn ID"):
                    self.client.push("id", "hello")
                self.client.call.assert_called_once()

    def test_active_steers_exact_turn_and_ambiguous_state_fails(self):
        self.client.target = Mock(return_value={"status": {"type": "active"},
            "turns": [{"id": "active", "status": "inProgress"}]})
        self.client.call = Mock(side_effect=[{"data": self.client.target.return_value["turns"]}, {"turnId": "active"}])
        self.assertEqual(("active", live.recipient_evidence(self.client.target.return_value)),
                         self.client.push("id", "hello"))
        self.assertEqual("active", self.client.call.call_args.args[1]["expectedTurnId"])
        self.client.target.return_value["turns"] = []
        self.client.call = Mock(return_value={"data": []})
        with self.assertRaises(live.LiveError):
            self.client.push("id", "hello")
        self.client.call.assert_called_once_with("thread/turns/list", {
            "threadId": "id", "limit": 1, "sortDirection": "desc", "itemsView": "notLoaded"})

    def test_active_rejects_malformed_turn_list_without_steering(self):
        self.client.target = Mock(return_value={"status": {"type": "active"}})
        for turns in (None, [None], [{"status": "inProgress"}],
                      [{"status": "completed"}, {"id": "old", "status": "inProgress"}]):
            with self.subTest(turns=turns):
                self.client.call = Mock(return_value={"data": turns})
                with self.assertRaises(live.LiveError):
                    self.client.push("id", "hello")
                self.client.call.assert_called_once()

    def test_different_repository_fails_closed(self):
        with patch.object(live.subprocess, "run", side_effect=[
                Mock(returncode=0, stdout="/repo-a/.git"), Mock(returncode=0, stdout="/repo-b/.git")]):
            with self.assertRaisesRegex(live.LiveError, "다른 저장소"):
                live.same_repo("a", "b")

    def test_cli_reports_consistent_evidence_for_the_validated_target(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "team.sqlite"
            owner, worker = str(uuid.uuid4()), str(uuid.uuid4())
            with closing(team.Team(db_path)) as store:
                store.join("supervisor", owner, tmp)
            row = {"id": worker, "name": "수신", "cwd": tmp, "source": "vscode",
                   "status": {"type": "idle"}, "approvalPolicy": "never",
                   "sandboxPolicy": {"type": "dangerFullAccess"}}
            final_row = {**row, "source": "cli"}
            self.client.close = Mock()
            self.client.call = Mock(side_effect=[
                {"data": [worker]}, {"thread": row},
                {"data": [worker]}, {"thread": row},
                {"data": [worker]}, {"thread": row},
                {"data": [worker]}, {"thread": final_row}, {"turn": {"id": "new-turn"}},
            ])
            with patch.object(team, "state_path", return_value=db_path), \
                 patch.object(live, "Client", return_value=self.client), \
                 patch.object(live, "same_repo"), \
                 patch.object(team, "codex", return_value="codex"), \
                 patch.dict(os.environ, {"CODEX_THREAD_ID": owner}):
                def run(*argv):
                    out = io.StringIO()
                    with patch.object(team.sys, "argv", ["codex_team.py", *argv]), redirect_stdout(out):
                        self.assertEqual(0, team.main())
                    return out.getvalue()

                peer, = json.loads(run("peers"))
                binding = run("bind", "S1", "--thread", worker).splitlines()
                sent = run("send", "S1", "--from-role", "supervisor", "--live", "--message", "시험").splitlines()
            expected = {"id": worker, "name": "수신", "cwd": tmp, "source": "vscode",
                        "status": {"type": "idle"}, "effectivePolicy": "확인 불가"}
            self.assertEqual(expected, peer)
            self.assertEqual(expected, json.loads(binding[1].removeprefix("수신 thread: ")))
            uuid.UUID(sent[0])
            self.assertEqual("공용 서버 수락: new-turn. 수신은 상대 ack로 확인합니다.", sent[1])
            self.assertEqual({**expected, "source": "cli"},
                             json.loads(sent[2].removeprefix("수신 thread: ")))

    def test_same_name_and_cwd_cli_peer_does_not_replace_selected_ide(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "team.sqlite"
            owner, ide, cli = (str(uuid.uuid4()) for _ in range(3))
            with closing(team.Team(db_path)) as store:
                store.join("supervisor", owner, tmp)
            shared = {"name": "같은 이름", "cwd": tmp}
            rows = {
                ide: {**shared, "id": ide, "source": "vscode", "status": {"type": "active"}},
                cli: {**shared, "id": cli, "source": "cli", "status": {"type": "idle"},
                      "approvalPolicy": "never", "sandboxPolicy": {"type": "dangerFullAccess"}},
            }

            def rpc(method, params):
                if method == "thread/loaded/list":
                    return {"data": [cli, ide]}
                if method == "thread/read":
                    return {"thread": rows[params["threadId"]]}
                if method == "thread/turns/list":
                    self.assertEqual(ide, params["threadId"])
                    return {"data": [{"id": "ide-turn", "status": "inProgress"}]}
                if method == "turn/steer":
                    return {"turnId": "ide-turn"}
                self.fail(f"Unexpected route: {method}")

            self.client.close = Mock()
            self.client.call = Mock(side_effect=rpc)
            with patch.object(team, "state_path", return_value=db_path), \
                 patch.object(live, "Client", return_value=self.client), \
                 patch.object(live, "same_repo"), \
                 patch.object(team, "codex", return_value="codex"), \
                 patch.dict(os.environ, {"CODEX_THREAD_ID": owner}):
                out = io.StringIO()
                with patch.object(team.sys, "argv", ["codex_team.py", "peers"]), redirect_stdout(out):
                    self.assertEqual(0, team.main())
                peers = json.loads(out.getvalue())
                self.assertEqual([(cli, "cli"), (ide, "vscode")], [(p["id"], p["source"]) for p in peers])
                self.assertEqual({"확인 불가"}, {peer["effectivePolicy"] for peer in peers})
                self.client.call.reset_mock()

                for argv in (["bind", "S1", "--thread", ide], [
                    "send", "S1", "--from-role", "supervisor", "--live", "--message", "격리 시험",
                ]):
                    with patch.object(team.sys, "argv", ["codex_team.py", *argv]), redirect_stdout(io.StringIO()):
                        self.assertEqual(0, team.main())

            requests = [entry.args for entry in self.client.call.call_args_list]
            self.assertEqual([ide] * 3, [p["threadId"] for m, p in requests if m == "thread/read"])
            method, params = requests[-1]
            self.assertEqual("turn/steer", method)
            self.assertEqual({"threadId": ide, "expectedTurnId": "ide-turn", "input": params["input"]}, params)
            self.assertIn("격리 시험", params["input"][0]["text"])

    def test_live_cli_failure_guides_to_the_same_saved_message(self):
        for mode in ("preflight", "dispatch"):
            with self.subTest(mode=mode), TemporaryDirectory() as tmp:
                db_path = Path(tmp) / "team.sqlite"
                owner, worker = str(uuid.uuid4()), str(uuid.uuid4())
                with closing(team.Team(db_path)) as store:
                    store.join("supervisor", owner, tmp)
                    store.join("S1", worker, tmp)
                row = {"id": worker, "cwd": tmp}
                replies = ([{"data": [worker]}, {"thread": {"id": worker}}] if mode == "preflight"
                           else [{"data": [worker]}, {"thread": row}] * 2)
                self.client.close = Mock()
                self.client.call = Mock(side_effect=replies)
                out, err = io.StringIO(), io.StringIO()
                with patch.object(team, "state_path", return_value=db_path), \
                     patch.object(live, "Client", return_value=self.client), \
                     patch.object(live, "same_repo"), \
                     patch.object(team, "codex", return_value="codex"), \
                     patch.dict(os.environ, {"CODEX_THREAD_ID": owner}), \
                     patch.object(team.sys, "argv", ["codex_team.py", "send", "S1",
                                  "--from-role", "supervisor", "--live", "--message", "시험"]), \
                     redirect_stdout(out), redirect_stderr(err):
                    self.assertEqual(1, team.main())
                mid = out.getvalue().strip()
                self.assertIn(mid, err.getvalue())
                self.assertIn("inbox S1", err.getvalue())
                self.assertIn("작업 경로" if mode == "preflight" else "상태", err.getvalue())
                self.assertEqual(len(replies), self.client.call.call_count)
                with closing(team.Team(db_path)) as store:
                    saved, = store.inbox("S1", worker)
                    self.assertEqual((mid, "pending" if mode == "preflight" else "unknown", "pending"),
                                     (saved["id"], saved["transport"], saved["state"]))
                    store.ack("S1", worker, mid, "received")
                    with self.assertRaisesRegex(ValueError, "다시 보내지"):
                        store.deliver_live(mid, self.client)
                self.assertEqual(len(replies), self.client.call.call_count)

    def test_peers_filters_subagent_and_ephemeral_metadata(self):
        with TemporaryDirectory() as tmp:
            client = self.client
            client.close = Mock()
            cli, ide = str(uuid.uuid4()), str(uuid.uuid4())
            unloaded = {"id": "unloaded", "cwd": tmp, "status": {"type": "notLoaded"}}
            client.loaded = Mock(return_value=[cli, ide, "legacy", "sub", "ephemeral", "child", "unloaded"])
            client.call = Mock(side_effect=[{"thread": row} for row in (
                {"id": cli, "cwd": tmp, "source": "cli", "status": {"type": "idle"},
                 "approvalPolicy": "never", "sandboxPolicy": {"type": "dangerFullAccess"}},
                {"id": ide, "cwd": tmp, "source": "vscode", "status": {"type": "active"}},
                {"id": "legacy", "cwd": tmp},
                {"id": "sub", "cwd": tmp, "source": {"subAgent": {}}},
                {"id": "ephemeral", "cwd": tmp, "ephemeral": True},
                {"id": "child", "cwd": tmp, "parentThreadId": cli},
                unloaded)])
            with patch.object(team, "state_path", return_value=Path(tmp)/"state.sqlite"), \
                 patch.object(live, "Client", return_value=client), \
                 patch.object(live, "same_repo"), \
                 patch.object(team, "codex", return_value="codex"), \
                 patch.object(team.sys, "argv", ["codex_team.py", "peers"]), \
                 patch("builtins.print") as output:
                team.main()
            rows = json.loads(output.call_args.args[0])
            self.assertEqual([cli, ide, "legacy"], [r["id"] for r in rows])
            self.assertEqual(["cli", "vscode", None], [r["source"] for r in rows])
            self.assertEqual([{"type": "idle"}, {"type": "active"}, None], [r["status"] for r in rows])
            self.assertEqual({"확인 불가"}, {r["effectivePolicy"] for r in rows})
            self.assertTrue(all("approvalPolicy" not in r and "sandboxPolicy" not in r for r in rows))
            self.assertTrue(all(not call.args[1]["includeTurns"] for call in client.call.call_args_list))
            client.call = Mock(return_value={"thread": unloaded})
            with self.assertRaisesRegex(live.LiveError, "로드되지"):
                client.target("unloaded")
            client.call.assert_called_once_with("thread/read", {"threadId": "unloaded", "includeTurns": False})

    def test_peers_failure_and_empty_results_point_to_existing_inbox(self):
        for mode in ("empty", "unsupported", "mismatched-id"):
            with self.subTest(mode=mode), TemporaryDirectory() as tmp:
                client = self.client
                client.close = Mock()
                client.loaded = Mock(return_value=[] if mode == "empty" else ["expected"])
                client.call = Mock()
                if mode == "unsupported":
                    client.call.side_effect = live.LiveError("공용 서버가 thread/read 메서드를 지원하지 않습니다")
                else:
                    client.call.return_value = {"thread": {"id": "other", "cwd": tmp}}
                out, err = io.StringIO(), io.StringIO()
                with patch.object(team, "state_path", return_value=Path(tmp) / "state.sqlite"), \
                     patch.object(live, "Client", return_value=client), \
                     patch.object(team, "codex", return_value="codex"), \
                     patch.object(team.sys, "argv", ["codex_team.py", "peers"]), \
                     redirect_stdout(out), redirect_stderr(err):
                    self.assertEqual(0 if mode == "empty" else 1, team.main())
                self.assertIn("inbox <역할>", err.getvalue())
                self.assertEqual("[]" if mode == "empty" else "", out.getvalue().strip())
                self.assertTrue(all(c.args[0] == "thread/read" for c in client.call.call_args_list))

    def test_non_supervisor_cannot_bind_loaded_thread(self):
        with TemporaryDirectory() as tmp:
            with patch.object(team, "state_path", return_value=Path(tmp)/"state.sqlite"), \
                 patch.object(live, "Client") as client, \
                 patch.object(team, "codex", return_value="codex"), \
                 patch.dict(os.environ, {"CODEX_THREAD_ID": str(uuid.uuid4())}), \
                 patch.object(team.sys, "argv", ["codex_team.py", "bind", "S1", "--thread", str(uuid.uuid4())]):
                with self.assertRaises(ValueError):
                    team.main()
                client.return_value.target.assert_not_called()

    def test_delivery_acceptance_is_not_receipt_and_no_duplicate_dispatch(self):
        with TemporaryDirectory() as tmp:
            store = team.Team(Path(tmp) / "team.sqlite")
            try:
                owner, worker = str(uuid.uuid4()), str(uuid.uuid4())
                store.join("supervisor", owner, tmp)
                store.join("S1", worker, tmp)
                mid = store.send("supervisor", owner, "S1", "한국어", "test")
                client = Mock()
                client.target.return_value = {"cwd": tmp}
                client.push.return_value = ("turn", live.recipient_evidence({"cwd": tmp}))
                with patch.object(live, "same_repo"):
                    self.assertEqual(client.push.return_value, store.deliver_live(mid, client))
                    with self.assertRaises(ValueError):
                        store.deliver_live(mid, client)
                self.assertEqual(1, client.push.call_count)
                row = store.inbox("S1", worker)[0]
                self.assertEqual(("pending", "live_accepted"), (row["state"], row["transport"]))
            finally:
                store.close()

    def test_unknown_delivery_keeps_original_without_automatic_replay(self):
        with TemporaryDirectory() as tmp:
            store = team.Team(Path(tmp) / "team.sqlite")
            try:
                owner, worker = str(uuid.uuid4()), str(uuid.uuid4())
                store.join("supervisor", owner, tmp)
                store.join("S1", worker, tmp)
                mid = store.send("supervisor", owner, "S1", "인계")
                client = Mock()
                client.target.return_value = {"cwd": tmp}
                client.push.side_effect = live.LiveError("lost response")
                with patch.object(live, "same_repo"):
                    with self.assertRaises(live.LiveError):
                        store.deliver_live(mid, client)
                    with self.assertRaises(ValueError):
                        store.deliver_live(mid, client)
                self.assertEqual("unknown", store.inbox("S1", worker)[0]["transport"])
                self.assertEqual(1, client.push.call_count)
            finally:
                store.close()

    def test_inflight_dispatch_blocks_takeover_but_not_independent_acks(self):
        with TemporaryDirectory() as tmp:
            store = team.Team(Path(tmp) / "team.sqlite")
            try:
                owner, worker, replacement = [str(uuid.uuid4()) for _ in range(3)]
                store.join("supervisor", owner, tmp)
                store.join("S1", worker, tmp)
                mid = store.send("supervisor", owner, "S1", "시험")
                def push(*args):
                    other = team.Team(Path(tmp) / "team.sqlite")
                    try:
                        other.ack("S1", worker, mid, "received")
                        with self.assertRaisesRegex(ValueError, "전달 중"):
                            other.join("S1", replacement, tmp, takeover=True)
                    finally:
                        other.close()
                    return "turn"
                client = Mock()
                client.target.return_value = {"cwd": tmp}
                client.push.side_effect = push
                with patch.object(live, "same_repo"):
                    store.deliver_live(mid, client)
                self.assertEqual("received", store.inbox("S1", worker)[0]["state"])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
