import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("worker_bridge", ROOT / "bridge/server.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def wait_until(predicate, seconds=4):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.015)
    raise AssertionError("Timed out waiting for worker state")


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workers = []

    def tearDown(self):
        for w in self.workers:
            w.close()
        self.tmp.cleanup()

    def worker(self, kind, task):
        w = bridge.Worker(kind, self.tmp.name, task,
                          argv=[sys.executable, str(ROOT / "tests/fake_agent.py")])
        self.workers.append(w)
        w.start()
        return w

    def test_empty_sessions_wait_for_first_message_on_all_adapters(self):
        for kind in ["codex", "qwen", "opencode"]:
            w = self.worker(kind, None)
            wait_until(lambda: w.status == "idle")
            self.assertTrue(w.snapshot()["canMessage"])
            self.assertEqual(w.snapshot()["history"], [])
            self.assertEqual(w.snapshot()["tools"], [])
            self.assertEqual(w.generation, 0)
            sid = w.session_id
            w.send("hello worker")
            wait_until(lambda: w.status == "done")
            self.assertEqual(w.output, "Reply: hello worker")
            self.assertEqual(w.session_id, sid)
            self.assertEqual(w.task, "hello worker")

    def test_chat_history_retains_both_turns(self):
        w = self.worker("qwen", None)
        wait_until(lambda: w.status == "idle")
        w.send("first")
        wait_until(lambda: w.status == "done")
        w.send("second")
        wait_until(lambda: w.status == "done" and w.output == "Reply: second")
        history = w.snapshot()["history"]
        self.assertEqual([m["role"] for m in history], ["user", "assistant", "user", "assistant"])
        self.assertEqual([m["text"] for m in history], ["first", "Reply: first", "second", "Reply: second"])

    def test_acp_model_uses_config_options_when_advertised(self):
        # OpenCode 2.x: no session/set_model; the model is a config option of the session.
        w = bridge.Worker("opencode", self.tmp.name, None, model="fake/other",
                          argv=[sys.executable, str(ROOT / "tests/fake_agent.py"), "--config-options"])
        self.workers.append(w)
        w.start()
        wait_until(lambda: w.status in {"idle", "error"})
        self.assertEqual(w.error, "")
        w.send("hello")
        wait_until(lambda: w.status == "done")
        self.assertEqual(w.output, "Reply: hello [fake/other]")

    def test_acp_model_matches_a_suffixed_id(self):
        # Qwen Code lists "name(authType)"; the pane's catalog has the plain name.
        w = bridge.Worker("qwen", self.tmp.name, None, model="fake-flash",
                          argv=[sys.executable, str(ROOT / "tests/fake_agent.py"), "--config-options"])
        self.workers.append(w)
        w.start()
        wait_until(lambda: w.status in {"idle", "error"})
        self.assertEqual(w.error, "")
        w.send("hi")
        wait_until(lambda: w.status == "done")
        self.assertEqual(w.output, "Reply: hi [fake-flash(openai)]")

    def test_acp_model_not_offered_is_a_clear_error(self):
        w = bridge.Worker("opencode", self.tmp.name, None, model="fake/missing",
                          argv=[sys.executable, str(ROOT / "tests/fake_agent.py"), "--config-options"])
        self.workers.append(w)
        w.start()
        wait_until(lambda: w.status == "error")
        self.assertIn("not offered", w.error)

    def test_acp_model_falls_back_to_set_model(self):
        w = bridge.Worker("qwen", self.tmp.name, None, model="any/model",
                          argv=[sys.executable, str(ROOT / "tests/fake_agent.py")])
        self.workers.append(w)
        w.start()
        wait_until(lambda: w.status in {"idle", "error"})
        self.assertEqual(w.error, "")

    def test_codex_tool_events_and_final_output(self):
        w = self.worker("codex", "first task")
        wait_until(lambda: w.status == "done")
        s = w.snapshot()
        self.assertEqual(s["output"], "Reply: first task")
        self.assertEqual(s["tools"][0]["status"], "completed")
        self.assertTrue(s["canMessage"])

    def test_both_acp_adapters_report_tools_and_output(self):
        for kind in ["qwen", "opencode"]:
            w = self.worker(kind, "first task")
            wait_until(lambda: w.status == "done")
            self.assertEqual(w.snapshot()["output"], "Reply: first task")
            self.assertEqual(w.snapshot()["tools"][0]["status"], "completed")

    def test_acp_followup_waits_until_current_turn(self):
        w = self.worker("qwen", "first task")
        wait_until(lambda: w.status == "running" and w.session_id)
        self.assertEqual(w.send("follow up"), "queued")
        wait_until(lambda: w.status == "done" and w.output == "Reply: follow up")
        self.assertEqual(w.snapshot()["queued"], 0)

    def test_codex_midturn_steering(self):
        w = self.worker("codex", "hold first")
        wait_until(lambda: w.turn_id is not None)
        self.assertEqual(w.send("new direction"), "steered")
        wait_until(lambda: "new direction" in w.output)
        w.interrupt()
        wait_until(lambda: w.status == "interrupted")

    def test_acp_interrupt_clears_queue(self):
        w = self.worker("opencode", "hold first")
        wait_until(lambda: w.status == "running" and bool(w.tools))
        w.send("must never run")
        w.interrupt()
        wait_until(lambda: w.status == "interrupted")
        self.assertEqual(w.snapshot()["queued"], 0)
        self.assertNotIn("must never run", w.output)

    def test_idle_followup_reuses_session(self):
        w = self.worker("codex", "first")
        wait_until(lambda: w.status == "done")
        sid = w.session_id
        w.send("second")
        wait_until(lambda: w.status == "done" and w.output == "Reply: second")
        self.assertEqual(sid, w.session_id)

    def test_closed_worker_rejects_send(self):
        w = self.worker("qwen", "first")
        wait_until(lambda: w.status == "done")
        w.close()
        with self.assertRaises(ValueError):
            w.send("second")

    def test_missing_cli_is_visible_error(self):
        w = bridge.Worker("qwen", self.tmp.name, "test", argv=["/missing/claude-workers-fixture"])
        self.workers.append(w)
        w.start()
        wait_until(lambda: w.status == "error")
        self.assertIn("No such file", w.error)

    def test_workspace_requires_absolute_path(self):
        with self.assertRaisesRegex(ValueError, "absolute"):
            bridge.Worker("qwen", ".", "test")

    def test_followup_obeys_session_active_limit(self):
        w = self.worker("qwen", "first")
        wait_until(lambda: w.status == "done")
        manager = bridge.Manager()
        manager.workers[w.id] = w
        for i in range(6):
            other = bridge.Worker("qwen", self.tmp.name, "not launched")
            manager.workers[str(i)] = other
        with self.assertRaisesRegex(ValueError, "At most 6"):
            manager.send(w.id, "second")

    def test_message_queue_is_bounded(self):
        w = self.worker("qwen", "hold")
        wait_until(lambda: w.session_id and w.status == "running" and bool(w.tools))
        for i in range(12):
            w.send(str(i))
        with self.assertRaisesRegex(ValueError, "queue is full"):
            w.send("overflow")
        w.interrupt()


class PermissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.w = bridge.Worker("qwen", self.tmp.name, "task")
        self.w.session_id = "s"

    def tearDown(self):
        self.w.close()
        self.tmp.cleanup()

    def test_read_file_is_scoped_and_respects_lines(self):
        p = Path(self.tmp.name) / "fixture.txt"
        p.write_text("one\ntwo\nthree\n")
        result = self.w._request("fs/read_text_file", {"sessionId": "s", "path": str(p), "line": 2, "limit": 1})
        self.assertEqual(result, {"content": "two\n"})
        with self.assertRaisesRegex(ValueError, "outside"):
            self.w._path("/etc/hosts")

    def test_symlink_outside_workspace_is_rejected(self):
        p = Path(self.tmp.name) / "escape"
        p.symlink_to("/etc")
        with self.assertRaises(ValueError):
            self.w._path(str(p / "hosts"))

    def test_client_file_write_requires_allow(self):
        p = Path(self.tmp.name) / "new.txt"
        result = []
        t = threading.Thread(target=lambda: result.append(self.w._request("fs/write_text_file", {
            "sessionId": "s", "path": str(p), "content": "approved content"})))
        t.start()
        wait_until(lambda: bool(self.w.approvals))
        self.assertFalse(p.exists())
        aid = next(iter(self.w.approvals))
        self.w.decide(aid, "allow")
        t.join(1)
        self.assertEqual(result, [{}])
        self.assertEqual(p.read_text(), "approved content")

    def test_interrupt_cancels_permission(self):
        result = []
        t = threading.Thread(target=lambda: result.append(self.w._permission("action", "details", [])))
        t.start()
        wait_until(lambda: bool(self.w.approvals))
        self.w.interrupt()
        t.join(1)
        self.assertEqual(result, [None])
        self.assertFalse(self.w.approvals)

    def test_always_allow_option_is_never_selected(self):
        result = self.w._request("session/request_permission", {"sessionId": "s", "toolCall": {},
            "options": [{"kind": "allow_always", "optionId": "always"}]})
        self.assertEqual(result, {"outcome": {"outcome": "cancelled"}})

    def test_foreign_session_cannot_read_files(self):
        with self.assertRaisesRegex(ValueError, "Unknown ACP session"):
            self.w._request("fs/read_text_file", {"sessionId": "other", "path": "/etc/hosts"})


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.manager = bridge.Manager()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), bridge.make_handler(self.manager, "test-token"))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.manager.close()

    def test_requires_session_token(self):
        with self.assertRaises(HTTPError) as exc:
            urlopen(self.url + "/state")
        self.assertEqual(exc.exception.code, 403)
        exc.exception.close()

    def test_rejects_browser_origin(self):
        req = Request(self.url + "/state", headers={"Authorization": "Bearer test-token", "Origin": "https://example.com"})
        with self.assertRaises(HTTPError) as exc:
            urlopen(req)
        self.assertEqual(exc.exception.code, 403)
        exc.exception.close()

    def test_returns_inventory_without_spawning_workers(self):
        req = Request(self.url + "/state", headers={"Authorization": "Bearer test-token"})
        with urlopen(req) as response:
            state = json.load(response)
        self.assertEqual(state["workers"], [])
        self.assertEqual({a["kind"] for a in state["agents"]}, {"codex", "qwen", "opencode"})

    def test_unknown_worker_is_rejected(self):
        req = Request(self.url + "/interrupt", data=b'{"id":"foreign"}',
                      headers={"Authorization": "Bearer test-token", "Content-Type": "application/json"})
        with self.assertRaises(HTTPError) as exc:
            urlopen(req)
        self.assertEqual(exc.exception.code, 400)
        exc.exception.close()


if __name__ == "__main__":
    unittest.main()
