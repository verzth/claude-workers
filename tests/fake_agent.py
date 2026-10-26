"""Deterministic subprocess fixture: both ACP and Codex JSON-RPC, no model calls."""
import json
import sys
import threading
import time

lock = threading.Lock()
cancelled = threading.Event()
turn_serial = 0

def emit(message):
    with lock:
        print(json.dumps(message), flush=True)

def event(method, params):
    emit({"jsonrpc": "2.0", "method": method, "params": params})

def answer(msg, result):
    emit({"jsonrpc": "2.0", "id": msg["id"], "result": result})

def prompt(msg, native, tid):
    p = msg["params"]
    text = (p["input"] if native else p["prompt"])[0]["text"]
    cancelled.clear()
    if native:
        event("turn/started", {"turn": {"id": tid}})
        event("item/started", {"item": {"id": "tool-" + tid, "type": "commandExecution", "command": "test-command", "status": "inProgress"}})
    else:
        event("session/update", {"sessionId": "session-test", "update": {"sessionUpdate": "tool_call", "toolCallId": "acp-tool", "title": "Read fixture", "status": "in_progress"}})
    cancelled.wait(15 if text.startswith("hold") else 0.08)
    if native:
        event("item/completed", {"item": {"id": "tool-" + tid, "type": "commandExecution", "command": "test-command", "status": "completed"}})
        event("item/completed", {"item": {"id": "msg-" + tid, "type": "agentMessage", "text": "Reply: " + text}})
        event("turn/completed", {"turn": {"id": tid, "status": "interrupted" if cancelled.is_set() else "completed"}})
    else:
        event("session/update", {"sessionId": "session-test", "update": {"sessionUpdate": "tool_call_update", "toolCallId": "acp-tool", "status": "completed"}})
        event("session/update", {"sessionId": "session-test", "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Reply: " + text}}})
        answer(msg, {"stopReason": "cancelled" if cancelled.is_set() else "end_turn"})

for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        answer(msg, {"protocolVersion": 1, "agentCapabilities": {}, "authMethods": []})
    elif method == "thread/start":
        answer(msg, {"thread": {"id": "thread-test"}})
    elif method == "session/new":
        answer(msg, {"sessionId": "session-test"})
    elif method in {"turn/start", "session/prompt"}:
        turn_serial += 1
        tid = str(turn_serial)
        if method == "turn/start":
            answer(msg, {"turn": {"id": tid}})
        threading.Thread(target=prompt, args=(msg, method == "turn/start", tid), daemon=True).start()
    elif method == "turn/steer":
        event("item/agentMessage/delta", {"delta": "Steered: " + msg["params"]["input"][0]["text"]})
        answer(msg, {"turnId": msg["params"]["expectedTurnId"]})
    elif method in {"turn/interrupt", "session/cancel"}:
        cancelled.set()
        if "id" in msg:
            answer(msg, {})
    elif "id" in msg:
        answer(msg, {})
