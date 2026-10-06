#!/usr/bin/env python3
"""A stand-in for the Codex CLI, speaking just enough of the app-server protocol.

It exists so the JSON-RPC client can be exercised end to end without an API
key, without quota, and without leaving a thread in anyone's Codex. The shapes
it emits were taken from the schema the real binary generates
(``codex app-server generate-json-schema --experimental``), not invented.

Since the 04.09.2026 design round it is also a **recovery oracle**: every
thread gets a unique id, every accepted turn is written to a durable journal
with its ``clientUserMessageId``, and ``thread/list`` and ``thread/turns/list``
answer from that journal. That is what lets a test prove "no duplicate turn"
and "an unmaterialised thread is invisible" instead of assuming them.

Behaviour is chosen by ``FAKE_CODEX_MODE``:

``ok``        the happy path; the final message is ``FAKE_CODEX_VERDICT``
``approval``  asks for a command approval mid-turn and waits for the answer
``hang``      accepts the turn, journals it as in progress, then never completes
``busy``      ``thread/resume`` fails the way a thread with another writer does;
              with ``FAKE_CODEX_BUSY_ID`` only that thread is held
``missing``   ``thread/resume`` fails the way a deleted thread does
``crash``     exits immediately after the handshake
``garbage``   completes with a final message that is not JSON
``trailer``   answers in prose with a trailing ```json verdict block
``quota``     the turn fails with `usageLimitExceeded`
``slow``      keeps sending heartbeat notifications before completing
``accept_then_die``  journals the turn as accepted and completed, then drops the
              connection before answering — the client never sees the result
``busy_after_accept``  any thread that already has a turn is held (Desktop
              opened it after the turn was accepted); fresh threads resume fine

``FAKE_CODEX_JOURNAL`` is the journal file. Without it the fake keeps state in
memory only, which is enough for single-process tests.
``FAKE_CODEX_THREAD_ID`` pins the id of the next created thread.
``FAKE_CODEX_SEEN`` dumps every request received, for assertions.
``FAKE_CODEX_PAGE_SIZE`` caps a ``thread/list`` and a ``thread/turns/list`` page.
``FAKE_CODEX_TURNS_FAIL_PAST_FIRST`` fails any ``thread/turns/list`` page after the first.
``FAKE_CODEX_LIST_FAILS`` makes ``thread/list`` fail, as a server under load can.
``FAKE_CODEX_VERDICT_FILE`` reads the final message from a file, for answers too
large for the environment.
``FAKE_CODEX_PROJECTS`` is a JSON list of ``{"name", "roots"}`` for ``project/list``;
``FAKE_CODEX_PROJECT_LIST_FAILS`` makes that method unknown, like an older server.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time
from typing import Any
import uuid

MODE = os.environ.get("FAKE_CODEX_MODE", "ok")
VERSION = os.environ.get("FAKE_CODEX_VERSION", "0.153.0-alpha.5")
JOURNAL = os.environ.get("FAKE_CODEX_JOURNAL")

_DEFAULT_VERDICT = {
    "stance": "agree",
    "stop": True,
    "confidence": "high",
    "summary": "Возражений нет.",
    "findings": [],
    "open_questions": [],
}

SEEN: list[dict[str, Any]] = []
EXPERIMENTAL = False


# -- journal ---------------------------------------------------------------


def _load() -> dict[str, Any]:
    if not JOURNAL:
        return _MEM
    try:
        with open(JOURNAL, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return {"threads": {}}


def _store(data: dict[str, Any]) -> None:
    if not JOURNAL:
        _MEM.clear()
        _MEM.update(data)
        return
    tmp = JOURNAL + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    os.replace(tmp, JOURNAL)


_MEM: dict[str, Any] = {"threads": {}}


def thread_record(thread_id: str) -> dict[str, Any] | None:
    return _load().get("threads", {}).get(thread_id)


def add_thread(thread_id: str, params: dict[str, Any]) -> None:
    data = _load()
    data.setdefault("threads", {})[thread_id] = {
        "id": thread_id,
        "name": None,
        "archived": False,
        "ephemeral": bool(params.get("ephemeral")),
        "cwd": params.get("cwd"),
        "created_at": int(time.time()),
        "turns": [],
    }
    _store(data)


def update_thread(thread_id: str, **fields: Any) -> None:
    data = _load()
    rec = data.setdefault("threads", {}).setdefault(thread_id, {"id": thread_id, "turns": []})
    rec.update(fields)
    _store(data)


def add_turn(thread_id: str, turn: dict[str, Any]) -> None:
    data = _load()
    rec = data.setdefault("threads", {}).setdefault(thread_id, {"id": thread_id, "turns": []})
    rec.setdefault("turns", []).append(turn)
    _store(data)


def set_turn(thread_id: str, turn_id: str, **fields: Any) -> None:
    data = _load()
    for turn in data.get("threads", {}).get(thread_id, {}).get("turns", []):
        if turn.get("id") == turn_id:
            turn.update(fields)
    _store(data)


def has_rollout(thread_id: str) -> bool:
    rec = thread_record(thread_id)
    return bool(rec and rec.get("turns"))


# -- wire ------------------------------------------------------------------


def emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def respond(request_id: Any, result: Any) -> None:
    emit({"jsonrpc": "2.0", "id": request_id, "result": result})


def fail(request_id: Any, code: int, message: str) -> None:
    emit({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def notify(method: str, params: dict[str, Any]) -> None:
    emit({"jsonrpc": "2.0", "method": method, "params": params})


def read_message() -> dict[str, Any] | None:
    line = sys.stdin.readline()
    if not line:
        return None
    line = line.strip()
    if not line:
        return {}
    try:
        return json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return {}


def await_response(expected_id: Any) -> dict[str, Any] | None:
    while True:
        message = read_message()
        if message is None:
            return None
        if not message:
            continue
        if message.get("id") == expected_id and "method" not in message:
            return message
        SEEN.append(message)


# -- answers ---------------------------------------------------------------


def final_verdict_text() -> str:
    if MODE == "garbage":
        return "Конечно! Вот мой анализ: всё отлично."
    raw = os.environ.get("FAKE_CODEX_VERDICT")
    path = os.environ.get("FAKE_CODEX_VERDICT_FILE")
    if path:
        # A verdict too big for the environment — a megabyte of summary is
        # exactly the kind of answer worth driving end to end.
        raw = pathlib.Path(path).read_text(encoding="utf-8", errors="surrogatepass")
    body = raw if raw else json.dumps(_DEFAULT_VERDICT, ensure_ascii=False)
    if MODE == "trailer":
        return (
            "Посмотрел предложение. Главный риск — запись состояния, но он закрыт.\n\n"
            "```json\n" + body + "\n```"
        )
    return body


def agent_message(text: str, phase: str) -> dict[str, Any]:
    return {"id": f"item-{uuid.uuid4().hex[:8]}", "type": "agentMessage", "text": text, "phase": phase}


def user_message(text: str, client_id: str | None) -> dict[str, Any]:
    return {"id": f"item-{uuid.uuid4().hex[:8]}", "type": "userMessage", "content": text, "clientId": client_id}


def turn_view(turn: dict[str, Any]) -> dict[str, Any]:
    """The shape `thread/turns/list` returns for a journaled turn."""
    items = [user_message(turn.get("input", ""), turn.get("client_id"))]
    if turn.get("status") == "completed" and turn.get("result"):
        items.append(agent_message(turn["result"], "final_answer"))
    if turn.get("error"):
        return {"id": turn["id"], "status": turn["status"], "items": items, "error": turn["error"]}
    return {"id": turn["id"], "status": turn["status"], "items": items}


def handle_turn(request_id: Any, params: dict[str, Any]) -> None:
    thread_id = str(params.get("threadId"))
    turn_id = f"turn-{uuid.uuid4().hex[:8]}"
    text = ""
    for item in params.get("input") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            text += str(item.get("text", ""))
    client_id = params.get("clientUserMessageId")

    # Accepting the turn is what materialises the rollout — journal it first.
    add_turn(thread_id, {"id": turn_id, "client_id": client_id, "input": text, "status": "inProgress"})
    update_thread(thread_id, listed=True)
    notify("turn/started", {"threadId": thread_id, "turnId": turn_id})

    if MODE == "accept_then_die":
        # Server-side acceptance barrier: the turn ran to completion on the
        # server, and the connection died before the client heard anything.
        set_turn(thread_id, turn_id, status="completed", result=final_verdict_text())
        sys.stdout.flush()
        os._exit(0)

    if MODE == "approval":
        emit(
            {
                "jsonrpc": "2.0",
                "id": 9001,
                "method": "item/commandExecution/requestApproval",
                "params": {"threadId": thread_id, "command": ["rm", "-rf", "/"]},
            }
        )
        answer = await_response(9001)
        if answer is None:
            return
        sys.stderr.write(f"approval answered: {json.dumps(answer.get('result'))}\n")

    notify(
        "item/completed",
        {
            "threadId": thread_id,
            "turnId": turn_id,
            "completedAtMs": 0,
            "item": agent_message("Смотрю на предложение.", "commentary"),
        },
    )
    # The response arrives before the completion notification on purpose.
    respond(request_id, {"turn": {"id": turn_id, "status": "inProgress", "items": []}})

    if MODE == "slow":
        deadline = time.monotonic() + float(os.environ.get("FAKE_CODEX_SLOW_SEC", "3"))
        while time.monotonic() < deadline:
            notify("item/started", {"threadId": thread_id, "turnId": turn_id,
                                    "item": {"id": "hb", "type": "reasoning"}})
            time.sleep(0.4)

    if MODE == "hang":
        # The turn is accepted and journaled as in progress; it never ends
        # unless interrupted, and an interrupt is answered honestly.
        while True:
            message = read_message()
            if message is None:
                return
            if message.get("method") == "turn/interrupt":
                set_turn(thread_id, turn_id, status="interrupted")
                respond(message.get("id"), {})
                notify("turn/completed", {"threadId": thread_id,
                                          "turn": {"id": turn_id, "status": "interrupted", "items": []}})
                return
            if message:
                SEEN.append(message)

    if MODE == "quota":
        error = {"message": "usage limit exceeded", "codexErrorInfo": "usageLimitExceeded",
                 "additionalDetails": None}
        set_turn(thread_id, turn_id, status="failed", error=error)
        notify("turn/completed", {"threadId": thread_id,
                                  "turn": {"id": turn_id, "status": "failed", "items": [], "error": error}})
        return

    text_out = final_verdict_text()
    set_turn(thread_id, turn_id, status="completed", result=text_out)
    notify(
        "turn/completed",
        {
            "threadId": thread_id,
            "turn": {"id": turn_id, "status": "completed",
                     "items": [agent_message(text_out, "final_answer")]},
        },
    )


def serve() -> int:
    global EXPERIMENTAL
    initialized = False
    while True:
        message = read_message()
        if message is None:
            return 0
        if not message:
            continue

        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}

        if method is None:
            continue
        if request_id is None:
            if method == "initialized":
                initialized = True
            continue

        SEEN.append({"method": method, "params": params})

        if method == "initialize":
            caps = params.get("capabilities") or {}
            EXPERIMENTAL = bool(isinstance(caps, dict) and caps.get("experimentalApi"))
            respond(request_id, {"userAgent": f"fake-codex/{VERSION}"})
            if MODE == "crash":
                return 0
            continue

        if not initialized:
            fail(request_id, -32002, "Not initialized")
            continue

        if method == "thread/start":
            thread_id = os.environ.get("FAKE_CODEX_THREAD_ID") or f"01a0{uuid.uuid4().hex[4:]}"
            add_thread(thread_id, params)
            respond(
                request_id,
                {
                    "thread": {"id": thread_id, "name": None, "preview": ""},
                    "model": params.get("model"),
                    "modelProvider": "openai",
                    "cwd": params.get("cwd"),
                    "sandbox": params.get("sandbox"),
                    "approvalPolicy": params.get("approvalPolicy"),
                },
            )
        elif method == "thread/name/set":
            if os.environ.get("FAKE_CODEX_NAME_SET_FAILS"):
                fail(request_id, -32603, "thread/name/set is unavailable")
                continue
            update_thread(str(params.get("threadId")), name=params.get("name"))
            respond(request_id, {})
            notify("thread/name/updated", {"threadId": params.get("threadId"),
                                           "threadName": params.get("name")})
        elif method == "thread/resume":
            thread_id = str(params.get("threadId"))
            held = os.environ.get("FAKE_CODEX_BUSY_ID")
            if MODE == "busy" and (not held or held == thread_id):
                fail(request_id, -32600, "thread already has an active writer")
                continue
            if MODE == "busy_after_accept" and has_rollout(thread_id):
                # Desktop opened the thread after a turn was accepted.
                fail(request_id, -32600, "thread already has an active writer")
            elif MODE == "missing" or not has_rollout(thread_id):
                fail(request_id, -32600, f"no rollout found for thread id {thread_id}")
            else:
                respond(request_id, {"thread": {"id": thread_id}})
        elif method == "thread/turns/list":
            if not EXPERIMENTAL:
                fail(request_id, -32601, "thread/turns/list requires capabilities.experimentalApi")
                continue
            rec = thread_record(str(params.get("threadId"))) or {"turns": []}
            turns = list(rec.get("turns", []))
            if params.get("sortDirection", "desc") == "desc":
                turns.reverse()
            start = int(params.get("cursor") or 0)
            if start and os.environ.get("FAKE_CODEX_TURNS_FAIL_PAST_FIRST"):
                fail(request_id, -32603, "turn history page unavailable")
                continue
            size = min(int(params.get("limit") or 50), int(os.environ.get("FAKE_CODEX_PAGE_SIZE") or 10**6))
            page = turns[start:start + size]
            nxt = str(start + size) if start + size < len(turns) else None
            respond(request_id, {"data": [turn_view(t) for t in page], "nextCursor": nxt})
        elif method == "project/list":
            # Desktop projects, as `project/list` reports them. Not in the
            # published schema; the real server answers it on 0.153.4.
            if os.environ.get("FAKE_CODEX_PROJECT_LIST_FAILS"):
                fail(request_id, -32600, "Invalid request: unknown variant `project/list`")
                continue
            projects = json.loads(os.environ.get("FAKE_CODEX_PROJECTS") or "[]")
            respond(request_id, {"data": [
                {"id": f"proj-{i}", "name": p["name"], "roots": [{"path": r} for r in p["roots"]],
                 "metadata": {}, "position": i}
                for i, p in enumerate(projects)]})
        elif method == "thread/list":
            if os.environ.get("FAKE_CODEX_LIST_FAILS"):
                fail(request_id, -32603, "thread/list is unavailable")
                continue
            # Only threads with a materialised rollout are visible — the same
            # rule the real store applies through `preview <> ''`.
            data = _load().get("threads", {})
            visible = [
                {"id": t["id"], "name": t.get("name"), "preview": t["turns"][0].get("input", ""),
                 # Unix seconds, as the 0.153.4 schema declares.
                 "createdAt": int(t.get("created_at") or 0),
                 "updatedAt": int(t.get("created_at") or 0),
                 "cwd": t.get("cwd"), "archived": bool(t.get("archived")),
                 "forkedFromId": t.get("forked_from")}
                for t in data.values()
                if t.get("turns") and t.get("listed", True)
                and not t.get("archived") and not t.get("ephemeral")
            ]
            visible.sort(key=lambda row: (-row["createdAt"], row["id"]))
            # Real pagination: a client that reads only the first page misses
            # whatever fell off it.
            page_size = min(
                int(params.get("limit") or 50),
                int(os.environ.get("FAKE_CODEX_PAGE_SIZE") or 10**6),
            )
            start = int(params.get("cursor") or 0)
            page = visible[start:start + page_size]
            nxt = str(start + page_size) if start + page_size < len(visible) else None
            respond(request_id, {"data": page, "nextCursor": nxt})
        elif method == "turn/start":
            handle_turn(request_id, params)
        elif method == "turn/interrupt":
            respond(request_id, {})
        elif method == "thread/fork":
            source = thread_record(str(params.get("threadId")))
            if source is None:
                fail(request_id, -32600, "no rollout found for thread id")
                continue
            new_id = os.environ.get("FAKE_CODEX_FORK_ID") or f"01a0{uuid.uuid4().hex[4:]}"
            data = _load()
            data["threads"][new_id] = {
                "id": new_id, "name": None, "archived": False, "ephemeral": False,
                "cwd": params.get("cwd"), "forked_from": source["id"],
                "created_at": int(time.time()),
                # The history travels; the listing entry does not. Measured on
                # 0.153.4: a fresh fork answers `thread/resume` and
                # `thread/turns/list`, and is absent from `thread/list` until
                # it takes a turn of its own.
                "listed": False,
                "turns": [dict(turn) for turn in source.get("turns", [])],
            }
            _store(data)
            respond(request_id, {
                "thread": {"id": new_id, "name": None, "preview": ""},
                "model": params.get("model"), "modelProvider": "openai", "cwd": params.get("cwd"),
                "sandbox": params.get("sandbox"), "approvalPolicy": params.get("approvalPolicy"),
            })
        elif method == "thread/archive":
            held = os.environ.get("FAKE_CODEX_BUSY_ID")
            target = str(params.get("threadId"))
            # The same rule as thread/resume: a writer elsewhere refuses this.
            if (MODE == "busy" and held and held == target) or (
                MODE == "busy_after_accept" and has_rollout(target)
            ):
                fail(request_id, -32600, "thread already has an active writer")
                continue
            update_thread(str(params.get("threadId")), archived=True)
            respond(request_id, {})
        else:
            fail(request_id, -32601, f"fake-codex does not implement {method}")


def main(argv: list[str]) -> int:
    if "--version" in argv:
        print(f"codex-cli {VERSION}")
        return 0
    if "app-server" not in argv:
        print(f"fake-codex: unexpected argv {argv}", file=sys.stderr)
        return 64
    try:
        return serve()
    finally:
        dump = os.environ.get("FAKE_CODEX_SEEN")
        if dump:
            try:
                with open(dump, "w", encoding="utf-8") as handle:
                    json.dump(SEEN, handle, ensure_ascii=False, indent=2)
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
