#!/usr/bin/env python3
"""A short-lived JSON-RPC client for ``codex app-server``.

Why short-lived: a paginated Codex thread accepts one writer across processes,
and the plugin this replaces died of a long-running broker daemon — orphaned
processes, no PID liveness check, whole process trees killed by a host timeout.
One process per operation, opened and closed, has none of those failure modes.

Transport is newline-delimited JSON-RPC 2.0 over the child's stdio. Three kinds
of message arrive on that pipe and all three must be handled, because ignoring
the third one deadlocks the turn:

* a response — has ``id``, no ``method``;
* a notification — has ``method``, no ``id``;
* a *server request* — has both, and the server blocks until it is answered.

Reading is done by a thread feeding a queue rather than ``select`` on the pipe:
mixing ``select`` with buffered line reads loses data that is already sitting in
Python's buffer, and that bug only shows up under load.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import json
from pathlib import Path
import queue
import subprocess
import threading
import time
from typing import Any

from config import CONTROL_TIMEOUT_SEC


class AppServerError(RuntimeError):
    """The server answered, and the answer was an error."""

    def __init__(self, method: str, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"{method} failed: [{code}] {message}")
        self.method = method
        self.code = code
        self.message = message
        self.data = data

    @property
    def is_busy_thread(self) -> bool:
        """True for the single-writer rejection.

        A paginated thread already open for writing elsewhere — usually the
        Desktop app with that thread on screen — answers -32600. Callers turn
        this into an instruction rather than a stack trace.
        """
        return self.code == -32600 and "writer" in self.message.lower()

    @property
    def is_missing_thread(self) -> bool:
        """True when the thread id is gone: deleted, or never materialised."""
        lowered = self.message.lower()
        return self.code == -32600 and "no rollout" in lowered


# Turn-level error kinds, as named by `CodexErrorInfo` in the generated schema
# (0.153.0-alpha.5). The string variants arrive as a bare string; the object
# variants arrive as a one-key object naming the variant. Anything unlisted is
# shown by its raw name rather than hidden.
_ERROR_EXPLANATIONS: dict[str, str] = {
    "usageLimitExceeded": "Codex usage limit reached — quota is exhausted for now",
    "rateLimitExceeded": "Codex rate limit hit — wait a little and retry the round",
    "contextWindowExceeded": (
        "the thread no longer fits the model's context window — start a new "
        "thread with --new, or raise --context-window if the model allows it"
    ),
    "sessionBudgetExceeded": "the thread's token budget is spent",
    "serverOverloaded": "Codex servers are overloaded — retry later",
    "unauthorized": "Codex is not signed in — run `codex login`",
    "badRequest": "Codex rejected the request as malformed",
    "internalServerError": "Codex internal error — retry once, then report",
    "sandboxError": "the sandbox failed to run a command",
    "cyberPolicy": "blocked by Codex cyber policy",
    "misalignmentPolicyViolation": "blocked by Codex misalignment policy",
    "threadRollbackFailed": "rolling the thread back failed",
    "httpConnectionFailed": "could not reach Codex — network problem",
    "responseStreamConnectionFailed": "the response stream never connected — network problem",
    "responseStreamDisconnected": "the response stream dropped mid-turn — retry the round",
    "responseTooManyFailedAttempts": "Codex gave up after repeated failed attempts",
    "activeTurnNotSteerable": "the running turn cannot be steered",
}


def explain_turn_error(error: Any) -> str:
    """One readable line for a `TurnError`, never a stack of JSON.

    A user whose quota ran out should read "quota is exhausted", not
    `{"codexErrorInfo": "usageLimitExceeded"}`.
    """
    if not isinstance(error, dict):
        return str(error) if error else "turn failed without a message"
    info = error.get("codexErrorInfo")
    kind: str | None = None
    if isinstance(info, str):
        kind = info
    elif isinstance(info, dict) and info:
        kind = next(iter(info))
    message = str(error.get("message") or "").strip()
    details = str(error.get("additionalDetails") or "").strip()
    parts: list[str] = []
    if kind:
        parts.append(_ERROR_EXPLANATIONS.get(kind, kind))
    if message and (not kind or message.lower() not in parts[0].lower()):
        parts.append(message)
    if details:
        parts.append(details)
    return " — ".join(parts) if parts else "turn failed without a message"


class AppServerCrashed(RuntimeError):
    """The child process exited while we were talking to it."""


class TurnTimeout(RuntimeError):
    """A turn was interrupted: either silent too long, or past a hard cap."""


# Answers to every server request we can receive. Nothing here is a real
# decision: the thread runs with the user's own Codex configuration, where
# approvals go to the reviewer they chose — "Approve for me" routes them to
# Codex's auto-reviewer — so a request that still reaches this client is one
# nobody upstream would settle, and the safe reply is the one that neither
# grants access nor wedges the turn.
#
# The enum values differ per method and are not interchangeable — the v2
# methods take `decline`, the legacy ones take `denied`.
_DENIALS: dict[str, dict[str, Any]] = {
    "item/commandExecution/requestApproval": {"decision": "decline"},
    "item/fileChange/requestApproval": {"decision": "decline"},
    "execCommandApproval": {"decision": "denied"},
    "applyPatchApproval": {"decision": "denied"},
    "mcpServer/elicitation/request": {"action": "decline"},
    "item/tool/requestUserInput": {"answers": []},
}


@dataclass
class TurnRecord:
    """Everything one turn produced, in the order it arrived.

    There is no ``turn/failed`` notification: a failure arrives as
    ``turn/completed`` carrying ``turn.status == "failed"``. The four statuses
    are ``completed``, ``interrupted``, ``failed`` and ``inProgress``.
    """

    agent_messages: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    status: str | None = None
    interrupted: bool = False
    turn_id: str | None = None
    # The variant name from `codexErrorInfo`, when the turn failed with one.
    error_kind: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == "completed" and not self.errors

    def final_text(self) -> str | None:
        """The assistant's terminal answer for the turn.

        ``phase`` distinguishes mid-turn commentary from the final answer, but
        the schema warns that providers do not emit it consistently and that
        callers must treat a missing phase as unknown. So: prefer the last
        explicitly-final message, otherwise fall back to the last message of
        any phase.
        """
        finals = [m for m in self.agent_messages if m.get("phase") == "final_answer"]
        pool = finals or self.agent_messages
        for message in reversed(pool):
            text = message.get("text")
            if isinstance(text, str) and text.strip():
                return text
        return None

    def absorb_completed_turn(self, turn: dict[str, Any]) -> None:
        """Take the authoritative item list from ``turn/completed``.

        The streamed ``item/completed`` notifications can be missed — a dropped
        event, a renamed field — whereas the completed turn carries its whole
        item list. When it has agent messages, they replace what was streamed.
        """
        self.status = turn.get("status") if isinstance(turn.get("status"), str) else None

        error = turn.get("error")
        if error:
            line = explain_turn_error(error)
            if line not in self.errors:
                self.errors.append(line)
            info = error.get("codexErrorInfo") if isinstance(error, dict) else None
            if isinstance(info, str):
                self.error_kind = info
            elif isinstance(info, dict) and info:
                self.error_kind = next(iter(info))

        items = turn.get("items")
        if not isinstance(items, list):
            return
        messages = [
            item
            for item in items
            if isinstance(item, dict) and item.get("type") == "agentMessage"
        ]
        if messages:
            self.agent_messages = messages


class AppServer:
    """One `codex app-server` child, driven synchronously.

    Use as a context manager; leaving the block terminates the child even if
    the body raised.
    """

    def __init__(
        self,
        codex_bin: Path,
        cwd: Path,
        *,
        client_name: str = "duo",
        client_version: str = "0.1.0",
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._bin = codex_bin
        self._cwd = cwd
        self._client_name = client_name
        self._client_version = client_version
        self._on_event = on_event

        self._proc: subprocess.Popen[str] | None = None
        self._inbox: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._stderr: list[str] = []
        self._next_id = 1
        self._initialized = False
        self._active_turn: TurnRecord | None = None
        # Monotonic time of the last message the server sent us; the idle
        # budget is measured from here, so a chatty turn never expires.
        self._last_activity = time.monotonic()

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> AppServer:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def start(self) -> None:
        self._proc = subprocess.Popen(
            [str(self._bin), "app-server", "--listen", "stdio://"],
            cwd=str(self._cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

    def close(self) -> None:
        proc = self._proc
        if proc is None:
            return
        self._proc = None
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

        # The reader threads are still holding these; they catch the resulting
        # ValueError. Closing them here is what keeps a long-lived caller from
        # leaking a file descriptor per round.
        for pipe in (proc.stdout, proc.stderr):
            try:
                if pipe is not None and not pipe.closed:
                    pipe.close()
            except OSError:
                pass

    def stderr_text(self) -> str:
        """Child diagnostics, for error messages worth reading."""
        return "".join(self._stderr).strip()

    # -- reader threads ----------------------------------------------------

    def _pump_stdout(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    # A non-JSON line on stdout is a protocol violation, not
                    # our data. Recording it beats crashing the reader.
                    self._stderr.append(f"[non-json stdout] {line}\n")
                    continue
                if isinstance(message, dict):
                    self._inbox.put(message)
        except (ValueError, OSError):
            pass
        finally:
            self._inbox.put(None)

    def _pump_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for line in proc.stderr:
                # Bounded so a chatty child cannot grow this without limit.
                if len(self._stderr) < 2000:
                    self._stderr.append(line)
        except (ValueError, OSError):
            pass

    # -- wire --------------------------------------------------------------

    def _write(self, payload: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdin.closed:
            raise AppServerCrashed("app-server stdin is closed")
        try:
            proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise AppServerCrashed(f"app-server went away: {exc}") from exc

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._write(payload)

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = CONTROL_TIMEOUT_SEC,
    ) -> Any:
        """Send a request and pump the pipe until its response arrives.

        Notifications and server requests that arrive meanwhile are handled in
        passing, which is what keeps a long turn from stalling.
        """
        request_id = self._next_id
        self._next_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._write(payload)
        return self._await_response(
            request_id, method, time.monotonic() + timeout, idle_timeout=timeout
        )

    def _await_response(
        self,
        request_id: int,
        method: str,
        hard_deadline: float | None,
        idle_timeout: float,
    ) -> Any:
        """Pump the pipe until the response with ``request_id`` shows up."""
        while True:
            message = self._receive(method, hard_deadline, idle_timeout)
            handled = self._route(message)
            if handled is None:
                continue
            if handled.get("id") != request_id:
                # A response to something we no longer wait on — the interrupt
                # issued from the timeout path, typically.
                continue
            if "error" in handled:
                error = handled["error"] or {}
                raise AppServerError(
                    method,
                    int(error.get("code", 0)),
                    str(error.get("message", "unknown error")),
                    error.get("data"),
                )
            return handled.get("result")

    def _await_turn_end(
        self, record: TurnRecord, hard_deadline: float | None, idle_timeout: float
    ) -> None:
        """Pump until the active turn reaches a terminal status.

        Whether ``turn/start`` answers immediately or only once the turn is
        done is not something to rely on: if the completion notification has
        already been seen, this returns at once; otherwise it waits for it.
        """
        while record.status in (None, "inProgress"):
            message = self._receive("turn/completed", hard_deadline, idle_timeout)
            self._route(message)

    def _receive(
        self, method: str, hard_deadline: float | None, idle_timeout: float
    ) -> dict[str, Any]:
        """One message off the queue, or an exception explaining why not.

        Two budgets: the idle one restarts on every message the server sends,
        so a turn that keeps streaming reasoning or command output is never
        cut; the hard one, when set, is an absolute cap.
        """
        while True:
            now = time.monotonic()
            idle_left = self._last_activity + idle_timeout - now
            if idle_left <= 0:
                raise TurnTimeout(
                    f"{method}: no message from Codex for {idle_timeout:.0f}s"
                )
            wait = min(idle_left, 1.0)
            if hard_deadline is not None:
                hard_left = hard_deadline - now
                if hard_left <= 0:
                    raise TurnTimeout(f"{method} passed the hard --timeout cap")
                wait = min(wait, hard_left)
            try:
                message = self._inbox.get(timeout=wait)
            except queue.Empty:
                continue
            if message is None:
                detail = self.stderr_text()
                suffix = f"\nstderr:\n{detail}" if detail else ""
                raise AppServerCrashed(f"app-server exited during {method}{suffix}")
            self._last_activity = time.monotonic()
            return message

    def _route(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Handle notifications and server requests; pass responses through."""
        if "method" in message and "id" in message:
            self._answer_server_request(message)
            return None
        if "method" in message:
            self._dispatch_notification(message)
            return None
        return message

    def _answer_server_request(self, message: dict[str, Any]) -> None:
        """Reply to everything, so the server is never left waiting."""
        method = str(message.get("method", ""))
        request_id = message.get("id")

        if method == "currentTime/read":
            result: dict[str, Any] = {"currentTimeAt": int(time.time())}
        elif method in _DENIALS:
            result = dict(_DENIALS[method])
            self._stderr.append(f"[auto-declined] {method}\n")
        else:
            # Unknown server request: answering with an error is honest and
            # unblocks the turn. Silence would not.
            self._write(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": f"duo does not implement {method}",
                    },
                }
            )
            self._stderr.append(f"[unanswered server request] {method}\n")
            return

        self._write({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _dispatch_notification(self, message: dict[str, Any]) -> None:
        method = str(message.get("method", ""))
        params = message.get("params")
        params = params if isinstance(params, dict) else {}

        record = self._active_turn
        if record is not None:
            if method == "item/completed":
                item = params.get("item")
                if isinstance(item, dict) and item.get("type") == "agentMessage":
                    record.agent_messages.append(item)
            elif method == "turn/completed":
                turn = params.get("turn")
                if isinstance(turn, dict):
                    record.absorb_completed_turn(turn)
                else:
                    record.status = "completed"
            elif method == "error":
                # The `error` notification wraps a TurnError under `error`;
                # explain that, do not dump the envelope.
                nested = params.get("error")
                if isinstance(nested, dict):
                    record.errors.append(explain_turn_error(nested))
                else:
                    message = params.get("message")
                    record.errors.append(
                        message.strip()
                        if isinstance(message, str) and message.strip()
                        else json.dumps(params, ensure_ascii=False)
                    )

        if self._on_event is not None:
            try:
                self._on_event(method, params)
            except Exception as exc:  # noqa: BLE001 - a bad hook must not kill the run
                self._stderr.append(f"[on_event raised] {exc}\n")

    # -- protocol ----------------------------------------------------------

    def initialize(self) -> dict[str, Any]:
        """Handshake. Any other request before this answers "Not initialized"."""
        if self._initialized:
            raise RuntimeError("initialize called twice on the same connection")
        result = self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": self._client_name,
                    "title": "Duo consultation loop",
                    "version": self._client_version,
                },
                # The docs gate `thread/turns/list` behind this; the measured
                # 0.153.0-alpha.5 does not enforce it. Declaring it costs
                # nothing and keeps recovery working on builds that do.
                "capabilities": {"experimentalApi": True},
            },
        )
        # `initialized` carries no params at all — the schema declares `method`
        # as its only property.
        self.notify("initialized")
        self._initialized = True
        return result if isinstance(result, dict) else {}

    def run_turn(
        self,
        thread_id: str,
        params: dict[str, Any],
        *,
        idle_timeout: float,
        timeout: float | None = None,
    ) -> TurnRecord:
        """Run one turn from an already-built ``turn/start`` params dict.

        The params are built by the caller so every protocol field stays
        reachable; this method owns only the waiting and the interrupt.

        ``idle_timeout`` is silence: how long the server may say nothing.
        ``timeout`` is an optional absolute cap. On either the turn is
        interrupted before raising, so the thread stays usable and no work is
        left running behind our back.
        """
        record = TurnRecord()
        self._active_turn = record
        self._last_activity = time.monotonic()
        deadline = None if timeout is None else time.monotonic() + timeout

        request_id = self._next_id
        self._next_id += 1
        self._write(
            {"jsonrpc": "2.0", "id": request_id, "method": "turn/start", "params": params}
        )

        try:
            result = self._await_response(request_id, "turn/start", deadline, idle_timeout)
            if isinstance(result, dict):
                turn = result.get("turn")
                if isinstance(turn, dict):
                    record.turn_id = turn.get("id")
                    if isinstance(turn.get("status"), str):
                        record.status = turn["status"]
            self._await_turn_end(record, deadline, idle_timeout)
        except TurnTimeout:
            record.interrupted = True
            record.status = "interrupted"
            self._interrupt_quietly(thread_id, record.turn_id)
            raise
        finally:
            self._active_turn = None

        return record

    def find_turn(
        self, thread_id: str, client_id: str, turn_id: str | None = None
    ) -> dict[str, Any] | None:
        """The turn whose user message carries `client_id`, with full items.

        Used to find out what happened to a turn whose result this process
        never saw: a match says our request was accepted, `turn.status` says
        how it ended, and the items carry the answer if there is one.

        Every page is read, newest first: the human may have started a turn
        after ours, so ours is not necessarily the last. A page that is not a
        list of turns is an error, never "not found" — "not found" is what
        licenses sending the request again. With `turn_id`, only that turn
        matches: the schema does not promise one turn per `clientId`.
        """
        cursor = None
        while True:
            result = self.request(
                "thread/turns/list",
                {
                    "threadId": thread_id,
                    "cursor": cursor,
                    "limit": 20,
                    "sortDirection": "desc",
                    "itemsView": "full",
                },
            )
            turns = result.get("data") if isinstance(result, dict) else None
            cursor = result.get("nextCursor") if isinstance(result, dict) else None
            if (
                not isinstance(turns, list)
                or not all(isinstance(t, dict) and isinstance(t.get("items"), list) for t in turns)
                or not (cursor is None or isinstance(cursor, str))
            ):
                raise AppServerError("thread/turns/list", 0, "the answer is not a page of turns")
            for turn in turns:
                user = next((i for i in turn["items"] if isinstance(i, dict) and i.get("type") == "userMessage"), None)
                if user is not None and user.get("clientId") == client_id and (
                    turn_id is None or turn.get("id") == turn_id
                ):
                    return turn
            if not cursor:
                return None

    def _interrupt_quietly(self, thread_id: str, turn_id: str | None) -> None:
        """Best effort: an interrupt that fails must not mask the timeout."""
        if not turn_id:
            return
        try:
            self.request(
                "turn/interrupt",
                {"threadId": thread_id, "turnId": turn_id},
                timeout=15,
            )
        except (AppServerError, AppServerCrashed, TurnTimeout):
            pass
