#!/usr/bin/env python3
"""Session-scoped Claude Code worker bridge. Python 3.10+, standard library only."""
from __future__ import annotations

import argparse
import collections
import hashlib
import hmac
import json
import os
from pathlib import Path
import queue
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError, HTTPError
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

VERSION = "0.4.0"
RUNTIME_KEY_VERSION = "0.2.0"  # Compatible 0.2.x updates reconnect to the same live bridge.
COMMANDS = {"codex": ["codex", "app-server", "--stdio"],
            "qwen": ["qwen", "--acp"], "opencode": ["opencode", "acp"]}
ACTIVE = {"starting", "running", "waiting", "interrupting"}
ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


def clean(value, limit=4000):
    value = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    value = ANSI.sub("", value)
    return "".join(c for c in value if c in "\n\t" or ord(c) >= 32)[-limit:]


def checked_text(value, name="message"):
    if not isinstance(value, str) or not value.strip() or len(value) > 32000:
        raise ValueError(f"{name} must contain 1–32000 characters")
    return value.strip()


class Rpc:
    """Line-delimited JSON-RPC, with responses and reverse requests on separate threads."""
    def __init__(self, argv, cwd, event, request, exited):
        self.pending = {}
        self.lock = threading.RLock()
        self.serial = 0
        self.event, self.handler, self.exited = event, request, exited
        self.closed = False
        self.close_lock = threading.Lock()
        self.stopping = False
        self.proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, encoding="utf-8", errors="replace",
                                     bufsize=1, start_new_session=True)
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._stderr, daemon=True).start()

    def _send(self, data):
        with self.lock:
            if self.closed:
                raise RuntimeError("Worker process is closed")
            self.proc.stdin.write(json.dumps(data, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()

    def notify(self, method, params=None):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def call(self, method, params=None, timeout=45):
        with self.lock:
            self.serial += 1
            rid = self.serial
            mailbox = queue.Queue(maxsize=1)
            self.pending[rid] = mailbox
        try:
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
            try:
                answer = mailbox.get(timeout=timeout)
            except queue.Empty:
                raise TimeoutError(f"{method} timed out after {timeout}s") from None
            if "error" in answer:
                raise RuntimeError(clean(answer["error"]))
            return answer.get("result", {})
        finally:
            with self.lock:
                self.pending.pop(rid, None)

    def _reverse(self, msg):
        try:
            result = self.handler(msg["method"], msg.get("params", {}))
            answer = {"jsonrpc": "2.0", "id": msg["id"], "result": result}
        except Exception as exc:
            answer = {"jsonrpc": "2.0", "id": msg["id"],
                      "error": {"code": -32000, "message": clean(str(exc))}}
        try:
            self._send(answer)
        except (OSError, RuntimeError):
            pass

    def _read(self):
        try:
            for line in self.proc.stdout:
                if len(line) > 8 * 1024 * 1024:
                    self.event("bridge/warning", {"message": "Oversized worker event omitted"})
                    continue
                try:
                    msg = json.loads(line)
                except (ValueError, TypeError):
                    self.event("bridge/log", {"message": clean(line)})
                    continue
                if not isinstance(msg, dict):
                    continue
                if "method" in msg:
                    if "id" in msg:
                        threading.Thread(target=self._reverse, args=(msg,), daemon=True).start()
                    else:
                        self.event(msg["method"], msg.get("params", {}))
                elif "id" in msg:
                    with self.lock:
                        box = self.pending.get(msg["id"])
                        if box is not None and box.empty():
                            box.put_nowait(msg)
        except Exception as exc:
            self.event("bridge/warning", {"message": str(exc)})
        finally:
            self.proc.wait()
            with self.lock:
                self.closed = True
                for box in self.pending.values():
                    if box.empty():
                        box.put_nowait({"error": f"Worker exited ({self.proc.returncode})"})
            self.exited(self.proc.returncode)

    def _stderr(self):
        for line in self.proc.stderr:
            self.event("bridge/log", {"message": clean(line)})

    def close(self):
        with self.close_lock:
            if self.stopping:
                return
            self.stopping = True
        # Kill the process group, including shell tools launched by the CLI.
        # Repeated cleanup must not signal a PID that the OS may have reused.
        for sig, delay in [(signal.SIGTERM, 2), (signal.SIGKILL, 1)]:
            if self.proc.poll() is not None:
                break
            try:
                os.killpg(self.proc.pid, sig)
            except ProcessLookupError:
                break
            except PermissionError:
                self.proc.send_signal(sig)
            try:
                self.proc.wait(timeout=delay)
            except subprocess.TimeoutExpired:
                continue
            break
        for stream in [self.proc.stdin, self.proc.stdout, self.proc.stderr]:
            try:
                stream.close()
            except (OSError, ValueError):
                pass


class Worker:
    def __init__(self, kind, cwd, task, model=None, argv=None, origin="unknown"):
        self.origin = origin
        if kind not in COMMANDS:
            raise ValueError("Choose codex, qwen or opencode")
        self.id = secrets.token_hex(5)
        if not isinstance(cwd, str) or not Path(cwd).is_absolute():
            raise ValueError("Workspace must be an absolute directory path")
        self.kind, self.cwd, self.task, self.model = kind, str(Path(cwd).resolve(strict=True)), task, model
        if not Path(self.cwd).is_dir():
            raise ValueError("Workspace must be a directory")
        self.argv = argv or COMMANDS[kind]
        self.status, self.error, self.output = "starting", "", ""
        self.history = collections.deque(maxlen=12)
        self.reply = None
        self.created = time.time()
        self.updated = self.created
        self.events = collections.deque(maxlen=80)
        self.tools = collections.OrderedDict()
        self.messages = collections.deque()
        self.approvals = {}
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.rpc = None
        self.session_id = None
        self.turn_id = None
        self.completion = threading.Event()
        self.cancelled = False
        self.closed = False
        self.generation = 0
        self.record("lifecycle", f"Starting {kind}")

    def record(self, kind, text, **extra):
        with self.lock:
            self.updated = time.time()
            self.events.append({"kind": kind, "text": clean(text, 1000), "at": self.updated, **extra})
            self.condition.notify_all()

    def snapshot(self):
        with self.lock:
            return {"id": self.id, "kind": self.kind, "cwd": self.cwd, "task": self.task,
                    "model": self.model, "status": self.status, "error": self.error, "origin": self.origin,
                    "output": self.output, "created": self.created, "updated": self.updated,
                    "queued": len(self.messages), "tools": list(self.tools.values()),
                    "events": list(self.events), "history": [dict(m) for m in self.history],
                    "nativeSessionId": self.session_id, "canMessage": bool(self.session_id) and not self.closed,
                    "approvals": [{k: v for k, v in a.items() if k not in {"event", "answer"}}
                                  for a in self.approvals.values()]}

    def start(self):
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        try:
            # The lock prevents interrupt/close racing the initial spawn.
            with self.lock:
                if self.closed:
                    return
                self.rpc = Rpc(self.argv, self.cwd, self._event, self._request, self._exited)
            if self.kind == "codex":
                self.rpc.call("initialize", {"clientInfo": {"name": "claude-workers", "version": VERSION},
                                             "capabilities": {"experimentalApi": True}})
                self.rpc.notify("initialized")
                params = {"cwd": self.cwd, "approvalPolicy": "on-request", "sandbox": "workspace-write"}
                if self.model:
                    params["model"] = self.model
                session = self.rpc.call("thread/start", params)
                self.session_id = session["thread"]["id"]
            else:
                hello = self.rpc.call("initialize", {"protocolVersion": 1,
                    "clientCapabilities": {"fs": {"readTextFile": True, "writeTextFile": True}},
                    "clientInfo": {"name": "claude-workers", "version": VERSION}})
                if hello.get("protocolVersion") != 1:
                    raise RuntimeError("This bridge requires ACP protocol version 1")
                session = self.rpc.call("session/new", {"cwd": self.cwd, "mcpServers": []}, timeout=90)
                self.session_id = session["sessionId"]
                if self.model:
                    self.rpc.call("session/set_model", {"sessionId": self.session_id, "modelId": self.model})
            with self.lock:
                if self.cancelled or self.closed:
                    return
                if self.task:
                    self.messages.append(self.task)
                else:
                    self.status = "idle"
                    self.record("lifecycle", "Session ready. Send your first message.")
            while not self.closed:
                with self.condition:
                    self.condition.wait_for(lambda: self.messages or self.closed)
                    if self.closed:
                        break
                    prompt = self.messages.popleft()
                    self.cancelled = False
                    self.status = "running"
                    self.generation += 1
                    self.output = ""
                    self.completion.clear()
                    self.history.append({"role": "user", "text": clean(prompt, 6000), "at": time.time()})
                    self.reply = {"role": "assistant", "text": "", "status": "running", "at": time.time()}
                    self.history.append(self.reply)
                self.record("message", prompt, role="user")
                if self.kind == "codex":
                    turn = self.rpc.call("turn/start", {"threadId": self.session_id,
                        "input": [{"type": "text", "text": prompt, "text_elements": []}]})
                    with self.lock:
                        self.turn_id = turn["turn"]["id"]
                    while not self.completion.wait(0.5):
                        if self.closed:
                            return
                    if self.error:
                        raise RuntimeError(self.error)
                else:
                    answer = self.rpc.call("session/prompt", {"sessionId": self.session_id,
                        "prompt": [{"type": "text", "text": prompt}]}, timeout=86400)
                    if answer.get("stopReason") == "cancelled":
                        self.cancelled = True
                with self.lock:
                    if self.closed:
                        return
                    self.turn_id = None
                    self.status = "interrupted" if self.cancelled else "done"
                    if self.reply is not None:
                        self.reply["status"] = self.status
                    self.record("lifecycle", self.status.capitalize())
        except Exception as exc:
            with self.lock:
                if not self.closed:
                    self.error = clean(str(exc))
                    self.status = "error"
                    if self.reply is not None:
                        self.reply["status"] = "error"
                    self.record("error", self.error)
        finally:
            if self.status in {"error", "closed"} or (self.cancelled and not self.session_id):
                self.close(preserve_error=True)

    def _exited(self, code):
        with self.lock:
            if not self.closed:
                self.error = f"Worker process exited ({code}). Start a new worker."
                self.status = "error"
                self.closed = True
                self.record("error", self.error)
            self.completion.set()
            self.condition.notify_all()

    def _tool(self, tid, title=None, status=None, detail=None):
        with self.lock:
            tool = self.tools.setdefault(str(tid), {"id": str(tid), "title": "Tool", "status": "in_progress"})
            if title:
                tool["title"] = clean(title, 300)
            if status:
                tool["status"] = status
            if detail:
                tool["detail"] = clean(detail, 1000)
            while len(self.tools) > 32:
                self.tools.popitem(last=False)
            self.record("tool", f"{tool['title']} · {tool['status']}")

    def _output(self, text, append=False):
        with self.lock:
            self.output = clean((self.output if append else "") + text, 24000)
            if self.reply is not None:
                self.reply["text"] = self.output[-6000:]
            self.updated = time.time()

    def _event(self, method, params):
        with self.lock:
            if method in {"bridge/log", "bridge/warning"}:
                self.record("log", params.get("message", ""))
            elif method == "session/update":
                u = params.get("update", {})
                kind = u.get("sessionUpdate")
                if kind == "agent_message_chunk":
                    self._output(u.get("content", {}).get("text", ""), append=True)
                    self.updated = time.time()
                elif kind in {"tool_call", "tool_call_update"}:
                    self._tool(u.get("toolCallId", "unknown"), u.get("title"), u.get("status"),
                               u.get("rawInput") or u.get("content"))
                elif kind == "plan":
                    self.record("plan", "; ".join(f"{x.get('content', '')} ({x.get('status', '')})"
                                                 for x in u.get("entries", [])))
                elif kind == "notice":
                    self.record("notice", u.get("message", u))
            elif method == "turn/started":
                self.turn_id = params.get("turn", {}).get("id")
                self.record("lifecycle", "Turn started")
            elif method == "turn/completed":
                turn = params.get("turn", {})
                if turn.get("status") == "failed":
                    self.error = clean(turn.get("error") or "Codex turn failed")
                if turn.get("status") == "interrupted":
                    self.cancelled = True
                self.completion.set()
            elif method in {"item/started", "item/completed"}:
                item = params.get("item", {})
                kind = item.get("type", "")
                if kind in {"commandExecution", "fileChange", "mcpToolCall", "webSearch", "dynamicToolCall"}:
                    title = item.get("command") or item.get("tool") or kind
                    self._tool(item.get("id", "unknown"), title,
                               item.get("status") or ("completed" if method.endswith("completed") else "in_progress"),
                               item.get("changes") or item.get("arguments"))
                elif kind == "agentMessage" and method == "item/completed":
                    self._output(item.get("text", self.output))
            elif method == "item/agentMessage/delta":
                self._output(params.get("delta", ""), append=True)
                self.updated = time.time()
            elif method == "item/commandExecution/outputDelta":
                tid = str(params.get("itemId", "unknown"))
                tool = self.tools.get(tid, {})
                self._tool(tid, detail=tool.get("detail", "") + params.get("delta", ""))
            elif method == "error":
                self.record("error", params.get("error", params))
            self.condition.notify_all()

    def _permission(self, title, details, options):
        aid = secrets.token_hex(5)
        a = {"id": aid, "title": clean(title, 300), "details": clean(details, 5000),
             "options": options, "event": threading.Event(), "answer": None}
        with self.lock:
            if self.cancelled or self.closed:
                return None
            self.approvals[aid] = a
            self.status = "waiting"
            self.record("approval", a["title"])
        a["event"].wait(180)
        with self.lock:
            self.approvals.pop(aid, None)
            if self.status == "waiting" and not self.approvals:
                self.status = "running"
            self.record("approval", "Allowed once" if a["answer"] == "allow" else "Denied or cancelled")
        return None if self.cancelled or self.closed else a["answer"]

    def decide(self, aid, choice):
        if choice not in {"allow", "deny"}:
            raise ValueError("Choose allow or deny")
        with self.lock:
            if aid not in self.approvals:
                raise ValueError("This permission request has expired")
            a = self.approvals[aid]
            a["answer"] = choice
            a["event"].set()

    def _path(self, path):
        p = Path(path)
        if not p.is_absolute():
            raise ValueError("ACP file paths must be absolute")
        p = p.resolve()
        if not p.is_relative_to(Path(self.cwd)):
            raise ValueError("File is outside this worker's workspace")
        return p

    def _request(self, method, p):
        if self.kind == "codex":
            if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
                decision = self._permission(p.get("command") or method, p,
                                           [{"kind": "allow_once"}, {"kind": "reject_once"}])
                return {"decision": "accept" if decision == "allow" else "decline"}
            if method == "item/tool/requestUserInput":
                self.record("notice", "Worker asked a question. Interrupt, then send your answer as a message.")
                return {"answers": {}}
            raise ValueError(f"Unsupported Codex request: {method}")
        if p.get("sessionId") != self.session_id:
            raise ValueError("Unknown ACP session")
        if method == "session/request_permission":
            options = p.get("options", [])
            has_allow = any(o.get("kind") == "allow_once" for o in options)
            # Never substitute allow_always when only one action was approved.
            decision = self._permission(p.get("toolCall", {}).get("title", "Worker action"),
                                        p.get("toolCall", {}), options) if has_allow else "deny"
            wanted = "allow_once" if decision == "allow" else "reject_once"
            selected = next((o for o in options if o.get("kind") == wanted), None)
            return {"outcome": {"outcome": "selected", "optionId": selected["optionId"]}} if selected else {"outcome": {"outcome": "cancelled"}}
        if method == "fs/read_text_file":
            path = self._path(p["path"])
            if path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("File exceeds 2 MiB")
            text = path.read_text()
            lines = text.splitlines(keepends=True)
            start = max(0, int(p.get("line", 1)) - 1)
            end = start + int(p["limit"]) if p.get("limit") is not None else len(lines)
            self.record("tool", f"Read {path.relative_to(self.cwd)}")
            return {"content": "".join(lines[start:end])}
        if method == "fs/write_text_file":
            path = self._path(p["path"])
            content = p["content"]
            if len(content.encode()) > 2 * 1024 * 1024:
                raise ValueError("File exceeds 2 MiB")
            if self._permission(f"Write {path.relative_to(self.cwd)}", content,
                                [{"kind": "allow_once"}, {"kind": "reject_once"}]) != "allow":
                raise ValueError("Write denied")
            # Resolve again after the wait: a symlink may have changed meanwhile.
            path = self._path(p["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            self.record("tool", f"Wrote {path.relative_to(self.cwd)}")
            return {}
        raise ValueError(f"Unsupported ACP request: {method}")

    def send(self, message):
        message = checked_text(message)
        with self.lock:
            if not self.session_id or self.closed:
                raise ValueError("Worker is not ready; start a new worker if it has closed")
            # Capture the active turn under lock, but never block its event reader.
            turn = self.turn_id if self.kind == "codex" and self.status == "running" else None
            if turn is None:
                if len(self.messages) >= 12:
                    raise ValueError("Message queue is full (12); wait or interrupt")
                self.messages.append(message)
                if not self.task:
                    self.task = message
                if self.status in {"idle", "done", "interrupted"}:
                    self.status = "running"
                self.record("message", "Queued: " + message, role="user")
                self.condition.notify_all()
                return "queued"
        try:
            self.rpc.call("turn/steer", {"threadId": self.session_id, "expectedTurnId": turn,
                "input": [{"type": "text", "text": message, "text_elements": []}]})
            with self.lock:
                self.history.append({"role": "user", "text": clean(message, 6000), "at": time.time(), "steered": True})
            self.record("message", "Sent to active turn: " + message, role="user")
            return "steered"
        except Exception:
            # Do not silently re-run a message when delivery is uncertain.
            self.record("error", "Steering was not confirmed; inspect status before retrying")
            raise

    def interrupt(self):
        with self.lock:
            self.messages.clear()
            self.cancelled = True
            for a in self.approvals.values():
                a["event"].set()
            if self.status not in ACTIVE:
                self.record("lifecycle", "Queued messages cleared")
                return
            self.status = "interrupting"
            generation, rpc, sid, turn = self.generation, self.rpc, self.session_id, self.turn_id
            self.record("lifecycle", "Interrupt requested; queued messages cleared")
        if rpc and sid:
            try:
                if self.kind == "codex" and turn:
                    rpc.call("turn/interrupt", {"threadId": sid, "turnId": turn}, timeout=5)
                elif self.kind != "codex":
                    rpc.notify("session/cancel", {"sessionId": sid})
            except Exception as exc:
                self.record("log", str(exc))
        # A stalled CLI is stopped as a process group after the grace period.
        def fallback():
            time.sleep(5)
            with self.lock:
                stalled = self.generation == generation and self.status in ACTIVE
            if stalled:
                self.close()
        threading.Thread(target=fallback, daemon=True).start()

    def close(self, preserve_error=False):
        with self.lock:
            self.closed = True
            self.messages.clear()
            for a in self.approvals.values():
                a["event"].set()
            if not preserve_error or self.status != "error":
                self.status = "closed"
            self.completion.set()
            self.condition.notify_all()
            rpc = self.rpc
        if rpc:
            rpc.close()


class Manager:
    def __init__(self, session=""):
        self.session = session
        self.workspace = ""
        self.title = "Claude session " + session[:8]
        self.peers = {}
        self.connection = None
        self.title_checked = 0
        self.workers = {}
        self.lock = threading.RLock()
        self.last_seen = time.monotonic()

    def start(self, data, empty=False):
        with self.lock:
            if len(self.workers) >= 24:
                raise ValueError("Session limit is 24 workers; restart the Claude session to clear history")
            if sum(w.status in ACTIVE for w in self.workers.values()) >= 6:
                raise ValueError("At most 6 workers may run at once")
            model = data.get("model") or None
            if model is not None:
                model = checked_text(model, "model")
            origin = data.get("origin", "unknown")
            if origin not in {"delegated", "direct", "unknown"}:
                raise ValueError("Invalid worker origin")
            w = Worker(data.get("kind"), data.get("cwd", ""), None if empty else checked_text(data.get("task"), "task"), model, origin=origin)
            self.workers[w.id] = w
            w.start()
            return w.snapshot()

    def send(self, wid, message):
        with self.lock:
            w = self.get(wid)
            if w.status not in ACTIVE and sum(x.status in ACTIVE for x in self.workers.values()) >= 6:
                raise ValueError("At most 6 workers may run at once")
            return w.send(message)

    def get(self, wid):
        with self.lock:
            if wid not in self.workers:
                raise ValueError("Worker does not belong to this Claude session")
            return self.workers[wid]

    def snapshot(self):
        with self.lock:
            if self.workspace and time.monotonic() - self.title_checked > 15:
                self.title = session_title(self.session, self.workspace)
                self.title_checked = time.monotonic()
            return {"hostSessionId": self.session, "parentTitle": self.title, "workspace": self.workspace,
                    "workers": [{**w.snapshot(), "hostSessionId": self.session, "parentTitle": self.title, "reachable": True} for w in self.workers.values()],
                    "agents": [{"kind": k, "available": bool(shutil.which(v[0]))} for k, v in COMMANDS.items()]}

    def context(self, data):
        cwd = data.get("workspace")
        if not isinstance(cwd, str) or not Path(cwd).is_absolute() or not Path(cwd).is_dir():
            raise ValueError("Workspace must be an absolute directory")
        self.workspace = str(Path(cwd).resolve())
        # Read only this host's transcript to obtain its user-visible title.
        self.title = session_title(self.session, self.workspace)
        self.title_checked = time.monotonic()
        if self.connection:
            self.connection.update(workspace=self.workspace, sessionId=self.session)
            write_connection(runtime_path(self.session), self.connection)
        return {"ok": True}

    def peer_connections(self):
        if not self.workspace or not self.session:
            return []
        peers = []
        for path in runtime_path(self.session).parent.glob("*.json"):
            try:
                st = path.lstat()
                if path.is_symlink() or st.st_uid != os.getuid() or st.st_mode & 0o077 or st.st_size > 64000:
                    continue
                conn = json.loads(path.read_text())
                if not isinstance(conn, dict) or not isinstance(conn.get("sessionId"), str) or not conn["sessionId"] or not isinstance(conn.get("token"), str):
                    continue
                url = urlsplit(conn.get("url", ""))
                if (conn.get("workspace") == self.workspace and conn.get("sessionId") != self.session
                        and url.scheme == "http" and url.hostname == "127.0.0.1" and url.port
                        and url.path == "" and not url.username and not url.query and not url.fragment):
                    peers.append(conn)
            except (OSError, ValueError, TypeError):
                continue
        return peers[:64]

    def overview(self):
        conns = self.peer_connections()
        def read_peer(conn):
            try:
                snapshot = connection_request(conn, "/monitor", timeout=0.5)
                if snapshot.get("workspace") != self.workspace or snapshot.get("hostSessionId") != conn["sessionId"]:
                    return None
                return conn["sessionId"], snapshot
            except (OSError, ValueError, URLError):
                return None
        results = []
        if conns:
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(read_peer, conns))
        live = set()
        with self.lock:
            for result in results:
                if result:
                    sid, data = result
                    self.peers[sid] = data
                    live.add(sid)
            result = self.snapshot()
            for sid, data in list(self.peers.items()):
                if data.get("workspace") != self.workspace:
                    continue
                for worker in data["workers"]:
                    copy = dict(worker)
                    copy["reachable"] = sid in live
                    if sid not in live:
                        copy.update(status="offline", canMessage=False, approvals=[])
                    result["workers"].append(copy)
            # Keep bounded last-seen records; never persist transcripts in the registry.
            while len(self.peers) > 64:
                self.peers.pop(next(iter(self.peers)))
            return result

    def control(self, data):
        route = data.get("action")
        if route not in {"send", "interrupt", "close", "decide", "retry", "delete"}:
            raise ValueError("Unsupported worker action")
        sid = data.get("hostSessionId")
        if sid == self.session:
            return self.worker_action(route, data)
        conn = next((c for c in self.peer_connections() if c["sessionId"] == sid), None)
        if not conn:
            raise ValueError("Parent session is offline or outside this workspace")
        try:
            return connection_request(conn, "/" + route, body=data, timeout=7)
        except HTTPError as exc:
            try:
                detail = json.load(exc).get("error", "Worker action failed")
            except (ValueError, AttributeError):
                detail = "Worker action failed"
            finally:
                exc.close()
            raise ValueError(detail) from exc
        except URLError as exc:
            raise ValueError("Parent session unavailable; refresh before retrying") from exc

    def worker_action(self, route, data):
        w = self.get(data.get("id"))
        if route == "delete":
            w.close()
            with self.lock:
                self.workers.pop(w.id, None)
            return {"ok": True, "removed": True}
        if route == "retry":
            with self.lock:
                if w.status != "error":
                    raise ValueError("Only failed workers can be retried")
                task = checked_text(w.task, "original task") if w.task else None
                return self.start({"kind": w.kind, "cwd": w.cwd, "task": task,
                                   "model": w.model, "origin": w.origin}, empty=task is None)
        if route == "send":
            return {"delivery": self.send(w.id, data.get("message"))}
        if route == "interrupt":
            w.interrupt()
        elif route == "close":
            w.close()
        elif route == "decide":
            w.decide(data.get("approvalId"), data.get("choice"))
        else:
            raise ValueError("Unsupported worker action")
        return {"ok": True}

    def close(self):
        with self.lock:
            workers = list(self.workers.values())
        threads = [threading.Thread(target=w.close, daemon=True) for w in workers]
        for t in threads:
            t.start()
        for t in threads:
            t.join(4)


def make_handler(manager, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _json(self, status, value):
            data = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self):
            auth = self.headers.get("Authorization", "")
            # No browser origin and no CORS. The token stays in the private mod connection.
            if self.headers.get("Origin") or not hmac.compare_digest(auth, "Bearer " + token):
                self._json(403, {"error": "Forbidden"})
                return False
            # Monitoring a different parent must not keep that parent alive.
            if self.path != "/monitor":
                manager.last_seen = time.monotonic()
            return True

        def do_GET(self):
            if not self._authorized():
                return
            if self.path == "/overview":
                self._json(200, manager.overview())
            elif self.path in {"/state", "/monitor"}:
                self._json(200, manager.snapshot())
            elif self.path == "/health":
                self._json(200, {"version": VERSION})
            else:
                self._json(404, {"error": "Unknown endpoint"})

        def do_POST(self):
            if not self._authorized():
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 128000:
                    raise ValueError("Invalid request size")
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError("Expected an object")
                if self.path in {"/start", "/new"}:
                    result = manager.start(data, empty=self.path == "/new")
                elif self.path == "/context":
                    result = manager.context(data)
                elif self.path == "/control":
                    result = manager.control(data)
                elif self.path == "/shutdown":
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    result = {"ok": True}
                else:
                    if self.path not in {"/send", "/interrupt", "/close", "/decide", "/retry", "/delete"}:
                        self._json(404, {"error": "Unknown endpoint"})
                        return
                    result = manager.worker_action(self.path[1:], data)
                self._json(200, result)
            except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
                self._json(400, {"error": clean(str(exc))})
    return Handler


def connection_request(conn, route="/health", body=None, timeout=2):
    req = Request(conn["url"] + route,
                  data=json.dumps(body).encode() if body is not None else None,
                  headers={"Authorization": "Bearer " + conn["token"], "Content-Type": "application/json"})
    with urlopen(req, timeout=timeout) as response:
        return json.load(response)


def write_connection(path, conn):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(conn))
    temp.chmod(0o600)
    temp.replace(path)


def session_title(session, workspace):
    fallback = "Claude session " + session[:8]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", session):
        return fallback
    project = re.sub(r"[^A-Za-z0-9]", "-", workspace)
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
    path = config / "projects" / project / (session + ".jsonl")
    try:
        with path.open("rb") as file:
            head = file.read(256000).decode("utf-8", errors="replace")
            file.seek(max(0, path.stat().st_size - 256000))
            tail = file.read(256000).decode("utf-8", errors="replace")
        first = None
        title = None
        for line in (head + "\n" + tail).splitlines():
            try:
                item = json.loads(line)
                if item.get("type") == "custom-title" and item.get("customTitle"):
                    title = str(item["customTitle"])
                if not first and item.get("type") == "user":
                    content = item.get("message", {}).get("content")
                    if isinstance(content, str):
                        first = content
                    elif isinstance(content, list):
                        first = next((c.get("text") for c in content if c.get("type") == "text"), None)
            except (ValueError, TypeError, AttributeError):
                continue
        label = title or first
        return clean(" ".join(label.split())[:120], 120) if label else fallback
    except OSError:
        return fallback


def runtime_path(session):
    # A private, stable runtime path survives hot reload. No credentials in plugin files.
    root = Path(tempfile.gettempdir()) / f"claude-workers-{os.getuid()}"
    root.mkdir(mode=0o700, exist_ok=True)
    if root.stat().st_uid != os.getuid() or root.is_symlink():
        raise RuntimeError("Unsafe runtime directory")
    root.chmod(0o700)
    key = hashlib.sha256((session + ":" + RUNTIME_KEY_VERSION).encode()).hexdigest()[:32]
    return root / (key + ".json")


def ensure(session):
    import fcntl
    path = runtime_path(session)
    # Serialize reconnect/start across a reload and an in-flight command.
    with open(str(path) + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            try:
                conn = json.loads(path.read_text())
                connection_request(conn)
                return conn
            except (OSError, ValueError, URLError):
                pass
        proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "serve", "--session", session],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(100):
            if proc.poll() is not None:
                raise RuntimeError("Worker bridge failed to start")
            try:
                conn = json.loads(path.read_text())
                connection_request(conn)
                return conn
            except (OSError, ValueError, URLError):
                time.sleep(0.05)
        proc.terminate()
        raise RuntimeError("Worker bridge startup timed out")


def managed_connection(session):
    path = runtime_path(session)
    st = path.lstat()
    if path.is_symlink() or st.st_uid != os.getuid() or st.st_mode & 0o077 or st.st_size > 64000:
        raise ValueError("Unsafe parent connection")
    conn = json.loads(path.read_text())
    url = urlsplit(conn.get("url", ""))
    if (url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port
            or url.path or url.username or url.query or url.fragment
            or not isinstance(conn.get("token"), str)):
        raise ValueError("Invalid parent connection")
    return conn


def manage_existing(caller, parent, wid, action):
    """Compatible management for already-running 0.2/0.3 daemons; never restart them."""
    if action not in {"retry", "delete"}:
        raise ValueError("Unsupported management action")
    own, target = managed_connection(caller), managed_connection(parent)
    if caller != parent and (not own.get("workspace") or own.get("workspace") != target.get("workspace")):
        raise ValueError("Parent is outside this workspace")
    snapshot = connection_request(target, "/state")
    w = next((w for w in snapshot["workers"] if w["id"] == wid), None)
    if not w:
        raise ValueError("Worker no longer exists in this parent")
    if action == "retry" and w["status"] != "error":
        raise ValueError("Only failed workers can be retried")
    try:
        result = connection_request(target, "/" + action, {"id": wid}, timeout=10)
    except HTTPError as exc:
        if exc.code != 404:
            raise
        exc.close()
        # Legacy bridge: reuse its existing public lifecycle routes.
        if action == "delete":
            result = connection_request(target, "/close", {"id": wid}, timeout=10)
        else:
            result = connection_request(target, "/start" if w.get("task") else "/new", {
                "kind": w["kind"], "cwd": w["cwd"], "task": w.get("task"),
                "model": w.get("model"), "origin": w.get("origin", "unknown"),
            }, timeout=10)
    if action == "retry":
        return {"worker": {**result, "hostSessionId": parent, "parentTitle": snapshot.get("parentTitle")}}
    return {"ok": True}


def serve(session):
    manager, token = Manager(session), secrets.token_urlsafe(32)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(manager, token))
    server.daemon_threads = True
    path = runtime_path(session)
    conn = {"url": f"http://127.0.0.1:{server.server_port}", "token": token, "pid": os.getpid(), "sessionId": session}
    manager.connection = conn
    write_connection(path, conn)
    def watchdog():
        while True:
            time.sleep(10)
            # Lost parent/hot-reload failure must not leave workers running forever.
            if time.monotonic() - manager.last_seen > 120:
                server.shutdown()
                return
    threading.Thread(target=watchdog, daemon=True).start()
    for sig in [signal.SIGTERM, signal.SIGINT]:
        signal.signal(sig, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        manager.close()
        server.server_close()
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["ensure", "serve", "retry-worker", "delete-worker"])
    parser.add_argument("--session", required=True)
    parser.add_argument("--parent")
    parser.add_argument("--worker")
    args = parser.parse_args()
    if args.action == "ensure":
        print(json.dumps(ensure(args.session)))
    elif args.action == "serve":
        serve(args.session)
    else:
        try:
            if not args.parent or not args.worker:
                raise ValueError("Parent and worker are required")
            print(json.dumps(manage_existing(args.session, args.parent, args.worker, args.action.split("-")[0])))
        except Exception as exc:
            print(clean(str(exc)), file=sys.stderr)
            sys.exit(1)
