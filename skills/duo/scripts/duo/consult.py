#!/usr/bin/env python3
"""One round of the consultation, as a crash-safe state machine.

Every step below answers a failure that was either measured or argued out with
Codex on 04.09.2026. The invariant the whole file defends: **no visible orphan
thread and no duplicate turn**, whatever point the process dies at.

The visibility rules that shape thread creation, all silent when broken:

* ``ephemeral: false`` — an ephemeral thread lives in memory and never reaches
  a listing or the sidebar.
* a real first turn — ``thread/start`` alone writes no rollout, and a thread
  whose ``preview`` is empty is filtered out of every listing. That is also why
  an unconfirmed ``thread/start`` after a crash is tolerated: it is invisible.
* the app-server transport — ``codex app-server`` records an interactive
  session source, ``codex exec`` records ``Exec``, which the default listing
  filter excludes.
* ``modelProviders: []`` on any listing — with the field omitted the results
  are silently narrowed to the current default provider.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from appserver import AppServer, AppServerError, TurnRecord, TurnTimeout
from config import (
    read_chat_cwd,
    MAX_MESSAGE_CHARS,
    MAX_ROUNDS,
    PROFILES,
    Profile,
    STOP_HOLD_ROUNDS,
    TURN_HARD_TIMEOUT_SEC,
    TURN_IDLE_TIMEOUT_SEC,
    codex_binary,
    read_chat_title,
)
from options import ThreadOptions, TurnOptions
import state as st
from terminal import say
from state import (
    REQUEST_PENDING,
    REQUEST_UNACKNOWLEDGED,
    Creating,
    OpenRequest,
    SessionState,
    StateBlocked,
    new_id,
    now_iso,
)
from verdict import (
    FORMAT_JSON,
    FORMAT_TRAILER,
    TRAILER_CONTRACT,
    VERDICT_SCHEMA,
    LoopDecision,
    Verdict,
    decide,
    parse,
)

DESIGN = "design"
REVIEW = "review"
PHASES = (DESIGN, REVIEW)

# Read-only with approvals off: Codex may read the repository to ground an
# objection, and cannot change anything. Anything that would need a write is
# refused and the refusal goes back to the model, which is the behaviour we
# want from a reviewer.
# Not set: the thread inherits the user's own Codex configuration — on this
# laptop `sandbox_mode = "workspace-write"`, `approval_policy = "on-request"`,
# `approvals_reviewer = "auto_review"`, which is what Desktop calls "Approve
# for me". Approvals are then settled by Codex's own reviewer, and nobody has
# to sit and click; duo still declines whatever reaches the client. The flags
# override per thread when a tighter or looser box is wanted.
SANDBOX: str | None = None
APPROVAL_POLICY: str | None = None

# Test hook: when this names the point about to be passed, the process dies
# there with SIGKILL semantics. Off unless the variable is set; production
# code never sets it. It is how the crash-window tests are deterministic.
CRASH_AT_ENV = "DUO_TEST_CRASH_AT"

_ROLE = """\
Ты — оппонент в парном разборе. Твоя работа не соглашаться и не спорить, а \
искать то, что сломается.

Правила:

- Каждое возражение опирается на доказательство: строка кода, файл, документ, \
воспроизводимый сценарий. Без доказательства возражения нет.
- Severity: p0 — сломается в проде или потеряет данные; p1 — сломается на \
реальном сценарии; p2 — заметный недостаток; p3 — вкусовщина. Держи планку: \
p0 и p1 останавливают работу, поэтому не раздавай их щедро.
- У каждого возражения стабильный `id`. В следующих раундах отвечай по тем же \
id: `confirm` — собеседник прав, `dispute` — отклоняешь с обоснованием, \
`retract` — снимаешь своё. `dispute` без `rationale` не принимается.
- Если всё в порядке, так и скажи: `stance: agree`, `stop: true`. Соглашаться \
ради завершения — это брак. Возражать ради вида — тоже.
- Не меняй позицию оттого, что собеседник настаивает или звучит уверенно. \
Меняй, когда можешь назвать конкретное основание: строку кода, которой раньше \
не было, измерение, опровергающее твою посылку, разобранный сценарий. \
Основание пиши в `rationale` того же id. Нет основания — позиция прежняя, \
даже если разногласие уходит в последний раунд.
- Читать репозиторий и запускать тесты можно — права те же, что у тебя в \
Desktop. Файлы не меняй: исправления делает собеседник, и твоя правка в \
рабочем дереве смешается с его.

Свободный текст — по-русски.
"""

_FORMAT_JSON_NOTE = """\
Формат ответа: **только** JSON по заданной схеме, без прозы вокруг.
"""

_PHASE_BRIEF = {
    DESIGN: """\
Фаза: проектирование. Решение ещё не реализовано. Смотри на замысел: что не \
учтено, где предположение выдаётся за факт, что развалится на границах, какой \
отказ не обрабатывается.
""",
    REVIEW: """\
Фаза: ревью сделанного. Это тот же тред, где мы обсуждали замысел, — твои \
прежние возражения у тебя в контексте. Проверь две вещи: сделанное решает \
задачу, и твои прежние p0/p1 действительно закрыты, а не обойдены. Если \
прежнее возражение осталось, подними его под тем же id.
""",
}


class ConsultError(RuntimeError):
    """Something a human has to read and act on."""


class OpenRequestConflict(ConsultError):
    """A different request is pending or unacknowledged; nothing was sent."""

    def __init__(self, open_request: OpenRequest) -> None:
        status = "still running" if open_request.status == REQUEST_PENDING else "finished but not acknowledged"
        super().__init__(
            f"request {open_request.request_id} is {status}. Pass "
            f"--request-id {open_request.request_id} to resume or replay it, or "
            f"--ack-request-id {open_request.request_id} together with a new "
            "--request-id to start the next round"
        )
        self.open_request = open_request


@dataclass
class RoundOutcome:
    """One round's result, plus how it was obtained."""

    key: str
    record: SessionState
    request_id: str
    verdict: Verdict
    turn_status: str | None
    turn_errors: list[str]
    turn_error_kind: str | None
    decision: LoopDecision | None
    round_number: int
    created_thread: bool
    revealed: bool
    replayed: bool = False
    resumed: bool = False


# -- small helpers -----------------------------------------------------------


def crash_point(name: str) -> None:
    """Die here if a test asked for it. See CRASH_AT_ENV."""
    if os.environ.get(CRASH_AT_ENV) == name:
        sys.stderr.write(f"duo: test crash at {name}\n")
        sys.stderr.flush()
        os._exit(137)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_profile(name: str | None) -> Profile:
    key = name or "default"
    if key not in PROFILES:
        known = ", ".join(sorted(PROFILES))
        raise ConsultError(f"unknown profile {key!r}; known profiles: {known}")
    return PROFILES[key]


def developer_instructions(phase: str, verdict_format: str = FORMAT_TRAILER) -> str:
    """The opponent's role, the phase brief, and the answer contract."""
    if phase not in _PHASE_BRIEF:
        raise ConsultError(f"unknown phase {phase!r}; expected one of {', '.join(PHASES)}")
    contract = TRAILER_CONTRACT if verdict_format == FORMAT_TRAILER else _FORMAT_JSON_NOTE
    return f"{_ROLE}\n{_PHASE_BRIEF[phase]}\n{contract}"


def frame_message(phase: str, message: str) -> str:
    """Repeat the phase on every turn.

    ``developerInstructions`` is set once at thread creation, but both loops
    share one thread, so the review turns have to say which phase they are.
    """
    label = "ПРОЕКТИРОВАНИЕ" if phase == DESIGN else "РЕВЬЮ СДЕЛАННОГО"
    return f"[{label}]\n\n{message.strip()}\n"


def codex_version(binary: Path) -> str | None:
    try:
        done = subprocess.run(
            [str(binary), "--version"], capture_output=True, text=True, timeout=20, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (done.stdout or done.stderr or "").strip()
    return text.split()[-1] if text else None


def reveal_in_desktop(thread_id: str) -> bool:
    """Bring the thread up in Codex Desktop — at a price worth knowing.

    Measured on 03.09.2026: once Desktop loads a thread its app-server holds
    `~/.codex/thread-writer-locks/<id>.lock`, every later `thread/resume` from
    another process fails with -32600, and neither waiting nor navigating to a
    different thread gives it back. That is why revealing is opt-in and belongs
    at the *end* of a loop. macOS only.
    """
    if sys.platform != "darwin":
        return False
    try:
        done = subprocess.run(
            ["open", f"codex://threads/{thread_id}"],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


# -- protocol steps ----------------------------------------------------------


def _anchor_directory(claude_session_id: str, here: Path, explicit: bool) -> Path:
    """The directory a new thread is created in.

    Explicit `--cwd` wins. Otherwise the Claude chat's own directory, read from
    its transcript; only when there is no transcript at all — a run outside
    Claude — does the shell's cwd stand in, and it says so.
    """
    if explicit:
        if not here.is_dir():
            raise ConsultError(f"--cwd {here} is not a directory")
        return here
    found, chat_dir = read_chat_cwd(claude_session_id, here)
    if not found:
        say(f"no Claude transcript for this session; the thread is anchored on {here}")
        return here
    if chat_dir is None:
        raise ConsultError(
            "the Claude transcript for this session carries no working directory; "
            "pass --cwd <directory> for the thread"
        )
    if not chat_dir.is_dir():
        raise ConsultError(
            f"the Claude chat's directory {chat_dir} no longer exists; pass --cwd <directory>"
        )
    if chat_dir.resolve() != here.resolve():
        say(f"thread anchored on the chat's directory {chat_dir} (this shell is in {here})")
    return chat_dir


def _sync_thread_name(
    server: AppServer,
    key: str,
    record: SessionState,
    claude_session_id: str,
    cwd: Path,
    name_override: str | None,
    pin_name: bool,
    unpin_name: bool,
) -> None:
    """Send the thread its name, from the chat or from --name, before a turn.

    Pinned: `thread_name` is the wanted name, written to state before the RPC
    so a refused rename is retried next round rather than lost. Unpinned: the
    chat title, sent every round without comparing to state — the state cannot
    see a rename made in Desktop. A state from before the field existed is
    unpinned, and says so once.
    """
    if record.thread_id is None:
        return
    changed = False
    if pin_name and name_override:
        if record.thread_name != name_override or record.name_pinned is not True:
            record.thread_name = name_override
            record.name_pinned = True
            changed = True
    elif unpin_name and record.name_pinned is not False:
        record.name_pinned = False
        changed = True
    elif record.name_pinned is None:
        say("this thread predates name pinning: its name now follows the Claude chat title; "
            "pass --name to pin one")
        record.name_pinned = False
        changed = True
    if changed:
        st.save(key, record)

    if record.name_pinned:
        desired = record.thread_name
    else:
        desired = read_chat_title(claude_session_id, cwd)
        if desired is None:
            return
    # No shortcut for a freshly created thread: equality with the state does
    # not prove the server took the name — the creation path's own rename can
    # have been refused, and a retried creation carries a --name the server
    # has never seen. One extra RPC is cheaper than a name nobody set.
    if not set_thread_name(server, record.thread_id, desired):
        say(f"could not set the thread name to «{desired}»; will try again next round")
        return
    if desired != record.thread_name:
        record.thread_name = desired
        st.save(key, record)


def start_thread(server: AppServer, options: ThreadOptions) -> str:
    result = server.request("thread/start", options.to_params())
    if not isinstance(result, dict):
        raise ConsultError("thread/start returned something that is not an object")
    thread = result.get("thread")
    if not isinstance(thread, dict) or not thread.get("id"):
        raise ConsultError("thread/start returned no thread id")
    return str(thread["id"])


def set_thread_name(server: AppServer, thread_id: str, name: str) -> bool:
    cleaned = name.strip()
    if not cleaned:
        return False
    try:
        server.request("thread/name/set", {"threadId": thread_id, "name": cleaned})
    except AppServerError:
        return False
    return True


def fork_thread(server: AppServer, source_id: str, options: ThreadOptions) -> str:
    """Copy a thread's history into a new thread and return the new id.

    Used when Desktop holds the original: the human opened it between the
    design and review phases, which is the natural thing to do, and Desktop
    then keeps the writer lock until it restarts. A fork carries every prior
    turn — Codex's own objections included — into a thread nobody holds.
    """
    params = options.to_params()
    params.pop("cwd", None)
    params.pop("ephemeral", None)
    params = {k: v for k, v in params.items() if k in (
        "sandbox", "approvalPolicy", "permissions", "approvalsReviewer", "model",
        "modelProvider", "developerInstructions", "baseInstructions", "config",
        "runtimeWorkspaceRoots", "serviceTier", "threadSource",
    )}
    params.update({"threadId": source_id, "cwd": str(options.cwd), "ephemeral": False})
    result = server.request("thread/fork", params)
    if not isinstance(result, dict):
        raise ConsultError("thread/fork returned something that is not an object")
    thread = result.get("thread")
    if not isinstance(thread, dict) or not thread.get("id"):
        raise ConsultError("thread/fork returned no thread id")
    return str(thread["id"])


SUPERSEDED_PREFIX = "[старый] "


def mark_superseded(server: AppServer, thread_id: str, name: str) -> bool:
    """Rename a forked original so the sidebar shows which thread is live.

    Renaming works while Desktop holds the writer lock; archiving and deleting
    do not. Idempotent: an already marked name is left alone.
    """
    if name.startswith(SUPERSEDED_PREFIX):
        return True
    return set_thread_name(server, thread_id, SUPERSEDED_PREFIX + name)


def archive_thread(server: AppServer, thread_id: str) -> bool:
    try:
        server.request("thread/archive", {"threadId": thread_id})
    except AppServerError as exc:
        if exc.is_missing_thread:
            # Never materialised: nothing is in the sidebar to archive.
            return True
        say(f"could not archive thread {thread_id}: {exc}")
        return False
    return True


class ThreadUnmaterialised(ConsultError):
    """The id exists but no rollout does: nothing was ever appended."""


class ThreadHeld(ConsultError):
    """Another writer — in practice Codex Desktop — holds the thread."""


class RecoveryWaitExhausted(ConsultError):
    """The pending turn is still running; nothing was interrupted."""

    def __init__(self, request_id: str, waited: float) -> None:
        super().__init__(
            f"request {request_id} is still running after waiting {waited:.0f}s for its "
            "result; nothing was interrupted or resent. Run again later with the same "
            "--request-id, or raise --idle-timeout to wait longer"
        )


def resume_thread(server: AppServer, thread_id: str, cwd: Path) -> None:
    try:
        server.request(
            "thread/resume", {"threadId": thread_id, "cwd": str(cwd), "excludeTurns": True}
        )
    except AppServerError as exc:
        if exc.is_busy_thread:
            raise ThreadHeld(
                f"Codex thread {thread_id} is held by another writer, which is "
                "almost always Codex Desktop after the thread was opened there. "
                "Desktop does not give the lock back when you switch threads — "
                "quit and reopen Desktop, pass --new to start a fresh thread, or "
                "let duo fork it (the default) so the history carries over."
            ) from exc
        if exc.is_missing_thread:
            raise ThreadUnmaterialised(
                f"Codex thread {thread_id} has no rollout — it was never written to."
            ) from exc
        raise


# -- key resolution and migration ---------------------------------------------


@dataclass
class KeyRoute:
    """Where a session's binding lives, and everything that must be locked.

    ``start`` is the key the ids give; ``key`` is the end of the migration
    route and the one a command reads and writes. They differ after a move.
    """

    start: str
    key: str
    locked: list[str]


@dataclass
class SettledRoute:
    """The route after every half-done move under the lock was finished."""

    key: str
    blocked: list[str]


def resolve_key(claude_session_id: str, host_session_id: str | None) -> KeyRoute:
    """The canonical key and every key a command must lock to reach it.

    The canonical key is the *end* of the migration route, not its start: a
    key that was migrated away is not where the binding lives, and a round run
    under it would start a second thread. The route is walked from both the
    host key and the CLI id, because a legacy file lives under the latter and
    a move has to hold both sides.

    This peek takes no lock, so what it returns is a candidate. It is walked
    again under the lock by `settle_migrations`, from `start` rather than from
    the candidate — the route can have grown a new hop, or lost one to
    quarantine, in between.
    """
    start = st.state_key_for(host_session_id, claude_session_id)
    keys = {start, claude_session_id}
    for source in (claude_session_id, start):
        keys.update(st.migration_chain(source))
    chain = st.migration_chain(start)
    key = chain[-1] if chain else start
    keys.add(key)
    return KeyRoute(start=start, key=key, locked=sorted(keys))


def settle_migrations(route: KeyRoute, claude_session_id: str) -> SettledRoute:
    """Finish every half-done move under the locked keys; report the end key.

    Caller holds ``route.locked``. Idempotent and crash-resumable. Four things
    happen here, in this order, because each depends on the last:

    1. Every key in the locked set that is marked ``migrating`` is completed.
       Not just the two the caller started from: a move interrupted before its
       destination was written leaves the source marked and the destination
       absent, and reading that absence as "no session" starts a second thread.
    2. A legacy v1 file under the CLI id is pulled under the host key.
    3. The route is walked again, from ``start``. Everything it can now reach
       was locked, or the route changed under us and the command is asked to
       run again.
    4. Keys that are quarantined are reported rather than ignored. A blocked
       key may be the one that held the binding, so a caller about to create a
       thread has to refuse; `close` and `--new` are how a human gets past it.
    """
    locked = route.locked
    blocked: list[str] = []
    for _ in range(st.MAX_MIGRATION_CHAIN):
        progressed = False
        for source in locked:
            try:
                # Strict: the lock is held, so a source that cannot be read is
                # a fault to stop on, not a key with nothing pending.
                target = st.pending_migration_target(source, strict=True)
            except StateBlocked:
                # A quarantined key holds no live binding: its file was moved
                # aside with whatever move it recorded. It is reported below,
                # because ignoring it is how a fault becomes an empty session.
                if source not in blocked:
                    blocked.append(source)
                continue
            if not target:
                continue
            if target not in locked:
                raise st.StateLocked(
                    f"a half-done move from {source} to {target} appeared after locking; "
                    "run the command again"
                )
            st.migrate(source, target)
            progressed = True
        if not progressed:
            break

    # The end of the route first: the legacy pull below has to be judged
    # against the key that actually holds the binding, not against a key that
    # was migrated away and now only redirects.
    key = route.start
    settled = st.migration_chain(key)
    if settled:
        final = settled[-1]
        if final not in locked:
            raise st.StateLocked(
                f"the binding moved to {final} while the command was starting; run it again"
            )
        key = final

    if key != claude_session_id and st.state_path(claude_session_id).exists():
        try:
            existing = st.load(key)
        except StateBlocked:
            existing = None
        if existing is None:
            # The canonical key has no binding of its own, so the legacy file
            # becomes it. Otherwise the file stays put for `duo recover
            # --from` rather than being merged or clobbered.
            st.migrate(claude_session_id, key)

    return SettledRoute(key=key, blocked=blocked)


# -- the round -----------------------------------------------------------------


def _result_payload(turn: TurnRecord) -> dict[str, Any]:
    return {
        "final_text": turn.final_text(),
        "turn_status": turn.status,
        "turn_errors": list(turn.errors),
        "turn_error_kind": turn.error_kind,
        "turn_id": turn.turn_id,
        "recorded_at": now_iso(),
    }


def _apply_verdict(
    record: SessionState,
    phase: str,
    request_id: str,
    verdict: Verdict,
    *,
    max_rounds: int,
    stop_hold: int,
) -> tuple[LoopDecision, int]:
    """Advance the phase counters exactly once per request."""
    phase_state = record.phase(phase)
    if phase_state.outcome is not None:
        # A finished phase restarts at one: coming back later is a new
        # consultation on the same thread, not round four of three.
        phase_state.rounds = 0
        phase_state.stop_streak = 0
        phase_state.outcome = None
        phase_state.unresolved_ids = []
    previous = frozenset(phase_state.unresolved_ids) if phase_state.rounds else None
    phase_state.rounds += 1
    decision = decide(
        verdict,
        round_number=phase_state.rounds,
        stop_streak=phase_state.stop_streak,
        previous_unresolved=previous,
        max_rounds=max_rounds,
        stop_hold=stop_hold,
    )
    phase_state.stop_streak = decision.stop_streak
    phase_state.outcome = decision.outcome
    phase_state.unresolved_ids = sorted(verdict.unresolved_ids())
    record.last_applied_request_id = request_id
    return decision, phase_state.rounds


def _outcome_from_stored(
    key: str, record: SessionState, request_id: str, *, replayed: bool, resumed: bool
) -> RoundOutcome:
    """Rebuild the exact outcome a caller missed, decision included."""
    stored = (record.open_request.result if record.open_request else None) or {}
    verdict = parse(stored.get("final_text"))
    phase = record.open_request.phase if record.open_request else DESIGN
    decision = None
    raw = stored.get("decision")
    if isinstance(raw, dict):
        decision = LoopDecision(
            finished=bool(raw.get("finished")),
            outcome=raw.get("outcome"),
            stop_streak=int(raw.get("stop_streak", 0) or 0),
            reason=str(raw.get("reason", "")),
        )
    return RoundOutcome(
        key=key,
        record=record,
        request_id=request_id,
        verdict=verdict,
        turn_status=stored.get("turn_status"),
        turn_errors=list(stored.get("turn_errors") or []),
        turn_error_kind=stored.get("turn_error_kind"),
        decision=decision,
        round_number=int(stored.get("round_number") or record.phase(phase).rounds),
        created_thread=False,
        revealed=False,
        replayed=replayed,
        resumed=resumed,
    )


def _recover_from_history(
    server: AppServer, record: SessionState, idle_timeout: float
) -> dict[str, Any] | None:
    """Find out what became of the pending request without resending it.

    Returns the result payload when the turn is known to the thread (any
    terminal status, or completed after polling), ``None`` when the thread
    never accepted it — in which case sending it again is safe.
    """
    open_request = record.open_request
    if open_request is None or record.thread_id is None:
        return None
    turns = server.list_turns(record.thread_id, limit=1)
    if not turns:
        return None
    turn = turns[0]
    items = turn.get("items") or []
    user = next((i for i in items if isinstance(i, dict) and i.get("type") == "userMessage"), None)
    if user is None or user.get("clientId") != open_request.request_id:
        return None

    import time

    started = time.monotonic()
    deadline = started + idle_timeout
    while turn.get("status") == "inProgress" and time.monotonic() < deadline:
        time.sleep(2)
        turns = server.list_turns(record.thread_id, limit=1)
        if not turns:
            break
        turn = turns[0]
    if turn.get("status") == "inProgress":
        # Waiting is not interrupting: the turn keeps running, pending stays.
        raise RecoveryWaitExhausted(open_request.request_id, time.monotonic() - started)

    rec = TurnRecord()
    rec.absorb_completed_turn(turn)
    rec.turn_id = turn.get("id")
    return _result_payload(rec)


def run_round(
    *,
    claude_session_id: str,
    host_session_id: str | None,
    phase: str,
    message: str,
    thread_options: ThreadOptions,
    turn_options: TurnOptions,
    profile_name: str,
    request_id: str | None = None,
    ack_request_id: str | None = None,
    force_new: bool = False,
    keep_old: bool = False,
    name_override: str | None = None,
    reveal: bool = False,
    timeout: float | None = TURN_HARD_TIMEOUT_SEC,
    idle_timeout: float = TURN_IDLE_TIMEOUT_SEC,
    verdict_format: str = FORMAT_TRAILER,
    max_message_chars: int = MAX_MESSAGE_CHARS,
    max_rounds: int = MAX_ROUNDS,
    stop_hold: int = STOP_HOLD_ROUNDS,
    fork_on_busy: bool = True,
    explicit_cwd: bool = False,
    pin_name: bool = False,
    unpin_name: bool = False,
) -> RoundOutcome:
    """Find or create the session's thread and run exactly one round on it."""
    if phase not in PHASES:
        raise ConsultError(f"unknown phase {phase!r}; expected one of {', '.join(PHASES)}")
    if len(message) > max_message_chars:
        raise ConsultError(
            f"the message is {len(message)} characters; the limit is {max_message_chars}. "
            "Point at files instead of pasting them — Codex reads the repository — "
            "or raise --max-message-chars deliberately."
        )

    request_id = (request_id or "").strip() or new_id()
    route = resolve_key(claude_session_id, host_session_id)
    key, lock_keys = route.key, route.locked
    framed = frame_message(phase, message)
    message_hash = sha256(framed)

    with st.lock_many(lock_keys):
        settled = settle_migrations(route, claude_session_id)
        key = settled.key

        resolves_block: str | None = None
        try:
            record = st.load(key)
        except StateBlocked as blocked:
            if not force_new:
                raise
            record = None
            resolves_block = blocked.block_id

        if record is None and settled.blocked and not force_new:
            # Something on this route is quarantined, and the key we would
            # write to is empty. Starting a thread here would be a second one
            # next to whatever the blocked key was holding.
            raise st.StateBlocked(
                settled.blocked[0],
                "unknown",
                f"{', '.join(settled.blocked)} is quarantined and {key} holds no binding; "
                "recover it with `duo recover --from <key>`, or pass --new to start a new "
                "thread and leave the blocked one alone",
            )

        # -- acknowledgement and replay ---------------------------------------
        if record is not None and record.open_request is not None:
            open_request = record.open_request
            if ack_request_id and ack_request_id == open_request.request_id:
                if open_request.status == REQUEST_PENDING:
                    raise ConsultError(
                        f"request {open_request.request_id} is still pending; resume it "
                        f"with --request-id {open_request.request_id} before acknowledging"
                    )
                record.open_request = None
                st.save(key, record)
            elif request_id == open_request.request_id:
                # A request id names exactly one message. Checked before any
                # replay or recovery, so a reused id with another payload can
                # never be answered with the old result.
                if open_request.message_sha256 != message_hash:
                    raise ConsultError(
                        f"request {request_id} names a different message; a request id "
                        "belongs to one message, pick a new id for a new one"
                    )
                if open_request.status == REQUEST_UNACKNOWLEDGED:
                    return _outcome_from_stored(key, record, request_id, replayed=True, resumed=False)
                if record.thread_id is not None:
                    with AppServer(codex_binary(), Path(record.cwd)) as server:
                        server.initialize()
                        try:
                            resume_thread(server, record.thread_id, Path(record.cwd))
                        except ThreadUnmaterialised:
                            recovered = None
                        except ThreadHeld:
                            if not fork_on_busy:
                                raise
                            # The accepted turn may be in there. Desktop holds
                            # the thread, so fork first and ask the fork's
                            # history — never conclude "not accepted" from busy.
                            _fork_held_thread(server, key, record, request_id, thread_options)
                            recovered = _recover_from_history(server, record, idle_timeout)
                        else:
                            recovered = _recover_from_history(server, record, idle_timeout)
                        if recovered is not None:
                            # The thread accepted the turn, so it is
                            # materialised and any creation it was part of is
                            # confirmed: the same transition as the normal
                            # path, debt included. Still inside the
                            # connection, because settling debt needs it.
                            _confirm_creation(record, keep_old)
                            _commit_result(key, record, request_id, phase, recovered,
                                           max_rounds=max_rounds, stop_hold=stop_hold)
                            _settle_archive_debt(server, key, record, keep_old)
                            return _outcome_from_stored(
                                key, record, request_id, replayed=False, resumed=True
                            )
                # Not accepted by the thread: fall through and send under the same id.
            else:
                raise OpenRequestConflict(open_request)

        inherited_previous = None
        inherited_debt: list[str] = []
        if (
            record is not None
            and record.creating is not None
            and record.creating.request_id != request_id
        ):
            # Another round on top of an unconfirmed creation. Nothing was
            # confirmed under it, but the binding it was replacing is still
            # owed an archive — with or without --new, because the thread the
            # human asked to leave behind is visible either way.
            inherited_previous = record.creating.previous_binding
            inherited_debt = list(record.archive_debt)
            if force_new and record.thread_id:
                # --new abandons the half-started thread too. It never took a
                # turn, so it is invisible and the archive is a formality.
                inherited_debt.append(record.thread_id)
            if force_new or record.thread_id is None:
                record = None

        binary = codex_binary()
        # Whether this call continues a creation already on disk is decided
        # here, before anything looks at a directory, and it does not care
        # whether `thread/start` was reached: a crash after it is still the
        # same creation. Deciding it later let a retry with `--new --cwd B`
        # start the thread in B while the state kept saying A.
        continuing = (
            record is not None
            and record.creating is not None
            and record.creating.request_id == request_id
        )
        if continuing:
            force_new = False
        creating_now = (record is None or force_new) and not continuing
        if creating_now:
            # A thread is about to be created: anchor it on the Claude chat's
            # own directory, never on where the shell happens to be — Claude's
            # cwd drifts with every `cd`, and a thread started from a drifted
            # cwd lands in the wrong Desktop project.
            thread_options = replace(
                thread_options,
                cwd=_anchor_directory(claude_session_id, thread_options.cwd, explicit_cwd),
            )
            cwd = thread_options.cwd
        elif continuing:
            # The directory was chosen when the intent was written; it is the
            # one and only one this creation may use. A different --cwd is a
            # different intent.
            assert record is not None
            cwd = Path(record.cwd)
            if explicit_cwd and thread_options.cwd.resolve() != cwd.resolve():
                raise ConsultError(
                    f"this creation was started in {record.cwd}; run again without --cwd to "
                    f"continue it, or start over with --new --cwd {thread_options.cwd} "
                    "--request-id <new id>"
                )
            if not cwd.is_dir():
                raise ConsultError(
                    f"this creation was started in {record.cwd}, which no longer exists; "
                    "start over with --new --cwd <directory> --request-id <new id>"
                )
            thread_options = replace(thread_options, cwd=cwd)
        else:
            cwd = thread_options.cwd if record is None or not record.cwd else Path(record.cwd)
        if not creating_now and not continuing and record is not None and record.cwd and not cwd.is_dir():
            # The repository the thread was created in is gone — moved, or
            # deleted. Resuming there is impossible, and refusing would strand
            # a consultation that is otherwise fine. Carry on here and say so,
            # loudly, because Codex now reads a different tree.
            say(
                f"the thread's directory {record.cwd} no longer exists; continuing in "
                f"{thread_options.cwd}. Codex reads this tree from now on",
            )
            cwd = thread_options.cwd
            record.cwd = str(thread_options.cwd)
        elif (
            not creating_now and not continuing
            and record is not None
            and Path(record.cwd).resolve() != thread_options.cwd.resolve()
            and Path(record.cwd).resolve() not in thread_options.cwd.resolve().parents
        ):
            # Inside the thread's own root is the normal case after the
            # project placement above; only a directory elsewhere is worth a
            # line.
            say(
                f"thread was created in {record.cwd}, resuming there "
                f"(current directory is {thread_options.cwd})",
            )

        if thread_options.developer_instructions is None:
            thread_options.developer_instructions = developer_instructions(phase, verdict_format)
        if turn_options.output_schema is None and verdict_format == FORMAT_JSON:
            turn_options.output_schema = VERDICT_SCHEMA
        turn_options.client_user_message_id = request_id

        created = False
        with AppServer(binary, cwd) as server:
            server.initialize()

            resuming_intent = continuing
            if resuming_intent:
                # Same creation request after a crash: continue it, keep what
                # it already knows (the binding it replaces, the block it
                # resolves, the thread it may already have started), and do
                # not start a second --new. Decided above, before the
                # directory was chosen.
                pass
            needs_thread = record is None or force_new or record.thread_id is None
            if not needs_thread:
                try:
                    resume_thread(server, record.thread_id, cwd)
                except ThreadUnmaterialised:
                    if record.rollout_materialized:
                        raise
                    needs_thread = True
                except ThreadHeld:
                    if not fork_on_busy:
                        raise
                    _fork_held_thread(server, key, record, request_id, thread_options)

            if needs_thread and not resuming_intent:
                previous = inherited_previous
                if record is not None and record.thread_id and force_new:
                    previous = {"thread_id": record.thread_id, "thread_name": record.thread_name}
                title = name_override or read_chat_title(claude_session_id, thread_options.cwd)
                thread_name = title or f"duo {key[:8]}"
                fresh = SessionState(
                    name_pinned=bool(name_override),
                    state_key=key,
                    claude_session_id=claude_session_id,
                    claude_host_session_id=host_session_id,
                    thread_id=None,
                    thread_name=thread_name,
                    cwd=str(thread_options.cwd),
                    profile=profile_name,
                    created_at=now_iso(),
                    updated_at=now_iso(),
                    model=turn_options.model,
                    effort=turn_options.effort,
                    codex_version=codex_version(binary),
                    creating=Creating(
                        request_id=request_id,
                        started_at=now_iso(),
                        payload_sha256=message_hash,
                        previous_binding=previous,
                    ),
                    resolves_block_id=resolves_block,
                )
                if record is not None:
                    fresh.phases = record.phases if not force_new else {}
                    fresh.archive_debt = list(record.archive_debt)
                fresh.archive_debt.extend(inherited_debt)
                # Durable intent before the server is asked for anything.
                st.save(key, fresh)
                record = fresh
                crash_point("after_creating_intent")

            if needs_thread:
                assert record is not None and record.creating is not None
                thread_id = start_thread(server, thread_options)
                crash_point("after_thread_start_response")
                record.thread_id = thread_id
                st.save(key, record)
                set_thread_name(server, thread_id, record.thread_name)
                created = True
                cwd = thread_options.cwd
            else:
                assert record is not None
                record.claude_session_id = claude_session_id
                record.profile = profile_name
                record.model = turn_options.model
                record.effort = turn_options.effort

            # Archives owed from earlier rounds — a replaced binding, a held
            # original, an unconfirmed fork — are retried every round.
            _settle_archive_debt(server, key, record, keep_old)

            # The name follows the Claude chat — or stays pinned — on every
            # real round, sent regardless of what the state remembers: a
            # rename made in Desktop is not visible from here, and Claude is
            # the source of truth for the name.
            _sync_thread_name(server, key, record, claude_session_id, thread_options.cwd,
                              name_override, pin_name, unpin_name)

            # -- write-ahead, then the turn ----------------------------------
            record.open_request = OpenRequest(
                request_id=request_id,
                phase=phase,
                message_sha256=message_hash,
                status=REQUEST_PENDING,
                started_at=now_iso(),
            )
            st.save(key, record)
            crash_point("after_open_request")

            params = turn_options.to_params(record.thread_id, framed)
            try:
                turn = server.run_turn(record.thread_id, params, idle_timeout=idle_timeout, timeout=timeout)
            except TurnTimeout:
                st.save(key, record)
                raise

            result = _result_payload(turn)
            if turn.status is not None:
                _confirm_creation(record, keep_old)
            _commit_result(key, record, request_id, phase, result,
                           max_rounds=max_rounds, stop_hold=stop_hold)
            crash_point("after_save")
            _settle_archive_debt(server, key, record, keep_old)

        outcome = _outcome_from_stored(key, record, request_id, replayed=False, resumed=False)
        outcome.created_thread = created
        outcome.revealed = reveal_in_desktop(record.thread_id) if reveal and record.thread_id else False
        return outcome


def _confirm_creation(record: SessionState, keep_old: bool) -> None:
    """The thread accepted a turn: it exists on disk and its creation is done.

    One transition for the normal path and for recovery from history, so the
    binding a --new replaced becomes archive debt whichever way the first
    turn's result reached us.
    """
    record.rollout_materialized = True
    if record.creating is None:
        return
    previous = record.creating.previous_binding or {}
    prev_id = previous.get("thread_id")
    if prev_id and prev_id != record.thread_id and not keep_old:
        record.archive_debt.append(str(prev_id))
    record.creating = None


def _commit_result(
    key: str,
    record: SessionState,
    request_id: str,
    phase: str,
    result: dict[str, Any],
    *,
    max_rounds: int,
    stop_hold: int,
) -> None:
    """Apply the verdict and store the result in ONE durable save.

    Exactly-once application and exact replay both hang on this: the phase
    counters, the decision and the result flip to `result_unacknowledged`
    together, so no crash point can separate "result stored" from "round
    counted", and a replay reproduces the decision the caller never saw.
    """
    verdict = parse(result.get("final_text"))
    terminal_failure = result.get("turn_status") in ("failed", "interrupted")
    decision: LoopDecision | None = None
    round_number = record.phase(phase).rounds
    if not terminal_failure and record.last_applied_request_id != request_id:
        decision, round_number = _apply_verdict(
            record, phase, request_id, verdict, max_rounds=max_rounds, stop_hold=stop_hold
        )
    result = dict(result)
    result["round_number"] = round_number
    result["decision"] = (
        {"finished": decision.finished, "outcome": decision.outcome,
         "stop_streak": decision.stop_streak, "reason": decision.reason}
        if decision else None
    )
    assert record.open_request is not None
    record.open_request.turn_id = result.get("turn_id")
    record.open_request.status = REQUEST_UNACKNOWLEDGED
    record.open_request.result = result
    st.save(key, record)


def _fork_held_thread(
    server: AppServer, key: str, record: SessionState, request_id: str, options: ThreadOptions
) -> None:
    """Desktop holds the thread: fork it and move the binding to the copy.

    A fork carries every prior turn — Codex's own objections included — so the
    consultation continues where it was. The original goes into `archive_debt`:
    it cannot be archived while another writer holds it.

    Measured on 0.153.4: a fork is absent from `thread/list` until it takes a
    turn of its own, and `forkedFromId` is not populated in this store. So a
    crash between the fork response and the save below leaves an invisible
    thread, not an orphan in the sidebar — the same window `thread/start`
    already has, and nothing to sweep. Identifying a stray fork by name or by
    time would archive a fork the human made themselves.
    """
    source = record.thread_id
    assert source is not None
    # The fork inherits the thread's own working directory, not the one this
    # round happens to run from. A resume already does that, and a fork that
    # quietly moved would give Codex a different repository to read.
    recorded = Path(record.cwd) if record.cwd else options.cwd
    if not recorded.is_dir():
        # Falling back to wherever this round runs would hand Codex a
        # different repository under the same thread. The round refuses; the
        # human moves the directory back, or passes --new.
        raise ConsultError(
            f"the thread's directory {recorded} disappeared while the round was running; "
            "the fork would land in a different repository. Restore it, or start a new "
            "thread with --new"
        )
    fork_options = replace(options, cwd=recorded)
    forked_id = fork_thread(server, source, fork_options)
    crash_point("after_fork_response")
    record.forked_from = source
    record.thread_id = forked_id
    record.rollout_materialized = False
    record.archive_debt.append(source)
    st.save(key, record)
    set_thread_name(server, forked_id, record.thread_name)
    # The original cannot be archived or deleted while Desktop holds it, but
    # it can be renamed — so from this moment the sidebar shows one live
    # thread under the chat's name and one plainly marked as old.
    mark_superseded(server, source, record.thread_name)
    say(
        f"thread {source} is held by Codex Desktop; forked it into {forked_id} with "
        "the full history. The original is marked old and will be archived once Desktop lets "
        f"go; anything written into it after the fork stays there, not in {forked_id}",
    )


def _settle_archive_debt(server: AppServer, key: str, record: SessionState, keep_old: bool) -> None:
    """Settle what earlier rounds could not.

    A replaced binding (`--new`) and an original forked because Desktop held
    it are both archived, never deleted: the human may have written into the
    original after the fork, and the archive keeps that. While Desktop still
    holds a thread, archiving is refused and the next round tries again.
    """
    if keep_old or not record.archive_debt:
        return
    remaining: list[str] = []
    for thread_id in dict.fromkeys(record.archive_debt):
        if thread_id == record.thread_id:
            continue
        if archive_thread(server, thread_id):
            say(f"archived thread {thread_id}; it stays in Codex's archive")
            continue
        # Still held. The mark set at fork time can have failed once; renaming
        # works under the lock, so make sure it reads as old.
        mark_superseded(server, thread_id, record.thread_name)
        remaining.append(thread_id)
    if remaining != record.archive_debt:
        record.archive_debt = remaining
        st.save(key, record)


# -- other commands ------------------------------------------------------------


def load_for_read(claude_session_id: str, host_session_id: str | None) -> tuple[str, SessionState | None]:
    route = resolve_key(claude_session_id, host_session_id)
    with st.lock_many(route.locked):
        settled = settle_migrations(route, claude_session_id)
        return settled.key, st.load(settled.key)


def acknowledge(claude_session_id: str, host_session_id: str | None, request_id: str) -> bool:
    route = resolve_key(claude_session_id, host_session_id)
    with st.lock_many(route.locked):
        key = settle_migrations(route, claude_session_id).key
        record = st.load(key)
        if record is None or record.open_request is None:
            return False
        if record.open_request.request_id != request_id:
            return False
        if record.open_request.status == REQUEST_PENDING:
            raise ConsultError(f"request {request_id} is still pending; it cannot be acknowledged yet")
        record.open_request = None
        st.save(key, record)
        return True


def recover(claude_session_id: str, host_session_id: str | None, from_key: str) -> SessionState:
    """Pull a mapping filed under an old key under the current one.

    Both routes are resolved before the lock and walked again after it, from
    their starts, and every key either can reach is held. A route that grew a
    hop in between is not followed under the old lock set: the command says so
    and asks to be run again. `st.migrate` refuses an unheld key anyway, but
    the check here is what turns that refusal into a clear message.
    """
    route = resolve_key(claude_session_id, host_session_id)
    if from_key == route.key:
        raise ConsultError("--from names the current key; nothing to recover")

    def reachable() -> set[str]:
        keys = set(route.locked) | {from_key, route.start} | set(st.migration_chain(from_key))
        keys.update(st.migration_chain(route.start))
        return keys

    lock_set = reachable()
    with st.lock_many(sorted(lock_set)):
        grew = reachable() - lock_set
        if grew:
            raise st.StateLocked(
                "the set of keys to move changed while it was being locked "
                f"({', '.join(sorted(grew))} appeared); run the command again"
            )
        # The destination is re-resolved under the lock too: it can have moved
        # on since, and writing to where it used to be is what leaves two live
        # bindings for one thread. Settlement gets the *full* set — the source
        # side included — or a move marked on the source would never be
        # finished and its intended destination would pass for a real one.
        settled = settle_migrations(
            KeyRoute(start=route.start, key=route.key, locked=sorted(lock_set)),
            claude_session_id,
        )
        key = settled.key
        if key not in lock_set:
            raise st.StateLocked(
                f"the binding moved to {key} while the command was starting; run it again"
            )
        if from_key == key:
            raise ConsultError("--from names the current key; nothing to recover")

        # Settling above finished every move that was already under way, this
        # one included. So the source is wherever its own route ends now.
        source_chain = st.migration_chain(from_key)
        source = source_chain[-1] if source_chain else from_key
        if source not in lock_set:
            raise st.StateLocked(
                f"{from_key} now leads to {source}, which this command does not hold; "
                "run it again"
            )
        if source == key:
            # It already landed here. Nothing left to do but adopt it: asking
            # `migrate` to move a key onto itself is an error, and this is the
            # success case.
            landed_here = st.load(key)
            if landed_here is not None:
                landed_here.claude_session_id = claude_session_id
                landed_here.claude_host_session_id = host_session_id
                st.save(key, landed_here)
                return landed_here
            raise ConsultError(
                f"{from_key} was already moved to {key}, and {key} holds no state; "
                "there is nothing to recover"
            )
        blocked = st.current_block_id(key)
        moved = st.migrate(source, key, resolves_block_id=blocked)
        # `migrate` follows a destination that has itself moved on, so the
        # binding may have landed past `key`. Write it where it landed.
        landed = moved.state_key
        moved.claude_session_id = claude_session_id
        moved.claude_host_session_id = host_session_id
        st.save(landed, moved)
        return moved


def repair_redirect(key: str, destination: str | None) -> str | None:
    """Set aside or rewrite a migration redirect that cannot be read.

    Every other command resolves the key before it does anything, so a
    redirect that fails to parse stops even the commands meant to recover.
    This one names the key itself and takes only that lock, which is what
    makes it usable when resolution is exactly what is broken.

    Dropping a redirect is not free: the redirect is the only thing that says
    the binding moved. Dropped from a key that has no state of its own, it
    turns a fault into an empty session and the next round opens a second
    thread. So a key with nothing local is blocked instead, and the human
    picks: `--to` the key that holds the binding, or `--new` to start over.
    """
    keys = {key} | ({destination} if destination else set())
    with st.lock_many(sorted(keys)):
        try:
            local = st.load(key)
        except StateBlocked:
            # Quarantined: the file is aside, there is no live binding here,
            # and restoring the route is exactly what this command is for.
            local = None
        # StateCorrupt and StateUnreadable are deliberately not caught. A file
        # that cannot be read may hold the binding, and hiding it behind a
        # redirect would lose whatever it owes.

        if destination is None:
            if local is not None:
                st.repair_redirect(key, None)
                return None
            block = st.quarantine(
                key, "the migration redirect could not be read and the key holds no binding"
            )
            st.repair_redirect(key, None)
            raise ConsultError(
                f"{key} has no binding of its own, so dropping its redirect would read as a "
                f"session that never existed. The key is blocked instead (block {block[:8]}). "
                "Point the redirect at the key that holds the thread with "
                f"`duo repair-redirect {key} --to <key>`, or pass --new to start a new thread"
            )

        if destination == key:
            raise ConsultError("a redirect that points at its own key is the thing being repaired")
        target = st.load(destination)
        if target is None:
            raise ConsultError(
                f"{destination} holds no state; point the redirect at a key that exists, "
                "or start over with --new"
            )
        if local is not None:
            if local.thread_id and local.thread_id != target.thread_id:
                raise ConsultError(
                    f"{key} still holds thread {local.thread_id} while {destination} holds "
                    f"{target.thread_id}; redirecting would hide a live binding. Close one of "
                    "them first"
                )
            # A binding is not only `thread_id`. An unconfirmed creation
            # remembers the thread it was replacing, and a debt names threads
            # nobody else knows about; both would go behind the redirect.
            owed = list(local.archive_debt)
            previous = (local.creating.previous_binding or {}).get("thread_id") if local.creating else None
            if previous:
                owed.append(str(previous))
            owed = [t for t in dict.fromkeys(owed) if t != target.thread_id]
            if owed:
                raise ConsultError(
                    f"{key} still owes an archive to {', '.join(owed)}; redirecting would "
                    "hide that. Settle it with `duo close --archive` under that key first"
                )
        if key in st.migration_chain(destination):
            raise ConsultError(
                f"{destination} already leads back to {key}; that redirect would be a loop"
            )
        return st.repair_redirect(key, destination)


def close(
    claude_session_id: str, host_session_id: str | None, *, archive: bool, force: bool = False
) -> SessionState | None:
    route = resolve_key(claude_session_id, host_session_id)
    with st.lock_many(route.locked):
        # A move left half done has to finish before anything is forgotten, or
        # the source would resurrect the binding this command just closed.
        key = settle_migrations(route, claude_session_id).key
        try:
            record = st.load(key)
        except StateBlocked:
            # Closing is not the same as starting over: the tombstone stays
            # unless the caller says --force.
            st.discard(key, keep_tombstone=not force)
            return None
        if record is None:
            return None
        if record.creating is not None:
            # The thread a --new was replacing lives only here until the new
            # one takes a turn. Closing must not forget it.
            previous = (record.creating.previous_binding or {}).get("thread_id")
            if previous and previous != record.thread_id:
                record.archive_debt.append(str(previous))
        if archive and (record.thread_id or record.archive_debt):
            with AppServer(codex_binary(), Path(record.cwd or ".")) as server:
                server.initialize()
                # Debts first: those ids exist nowhere else, so discarding the
                # state would lose them. If one cannot be paid, nothing else
                # happens either — closing is all or nothing, or the binding
                # would point at a thread that is already archived.
                _settle_archive_debt(server, key, record, keep_old=False)
                stuck = list(record.archive_debt)
                if not stuck and record.thread_id and not archive_thread(server, record.thread_id):
                    stuck.append(record.thread_id)
                if stuck:
                    raise ConsultError(
                        "could not archive " + ", ".join(stuck) + " (see above); nothing was "
                        "archived and the mapping is kept. Retry when Desktop releases the "
                        "thread, or close without --archive"
                    )
        st.discard(key)
        return record
