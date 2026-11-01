#!/usr/bin/env python3
"""Durable state for "one Claude chat, one Codex thread" — version 2.

The design was argued out with Codex over three rounds on 04.09.2026 (see
``references/known-gaps.md``); every mechanism here answers a named crash
window from that debate:

* **Key.** The mapping is filed under an immutable ``state_key`` — the host
  session id when Claude Desktop provides one, else the CLI session id at
  creation. The CLI id changes on every restart of a conversation; the host id
  was measured to survive it. The path is derived from the key, never from a
  field that later changes.
* **Lock.** A persistent ``<key>.lock`` held with ``flock`` from the first load
  to the last save of a command. The kernel drops it when the process dies, so
  there is no PID liveness to get wrong and no "stale after N seconds" guess.
* **Tombstone.** A corrupt file is quarantined by first writing
  ``<key>.blocked`` (with a ``block_id``) and only then renaming the file
  aside. The loader checks the marker before it checks for absence, so a
  second run cannot mistake a quarantined session for a fresh one. The marker
  is released by a new state that carries the matching ``resolves_block_id``.
* **Intent.** A thread is created in two durable steps — ``creating`` before
  ``thread/start``, ``thread_id`` after — so a crash between them leaves a
  state that says exactly what was in flight.
* **Open request.** One in-flight or unacknowledged request per session, keyed
  by a caller-supplied ``request_id``. It is what makes a retry after a lost
  stdout replay the result instead of starting a second turn.
* **Migration.** Moving a file between keys is a two-phase move with a durable
  ``migrating`` record on the source, so a crash mid-move leaves one binding,
  never two.

Writes are atomic: temporary file beside the target, ``fsync``, ``os.replace``,
``fsync`` of the directory. Timestamps are ISO 8601; this is a machine file.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import datetime as dt
import errno
import json
import os
from pathlib import Path
import tempfile
from typing import Any
import uuid

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows; documented as unsupported
    fcntl = None  # type: ignore[assignment]

from config import state_dir

STATE_VERSION = 2
SUPPORTED_VERSIONS = (1, 2)

REQUEST_PENDING = "pending"
REQUEST_UNACKNOWLEDGED = "result_unacknowledged"


# -- errors ----------------------------------------------------------------


class StateError(RuntimeError):
    """Base for everything a caller must show a human."""


class StateLocked(StateError):
    """Another duo process holds this session right now."""


class StateBlocked(StateError):
    """A quarantine marker is in place; only --new or recover may clear it."""

    def __init__(self, key: str, block_id: str, reason: str) -> None:
        super().__init__(
            f"session {key} is blocked after quarantine ({reason}); "
            "pass --new to start a fresh thread, or `duo recover` to restore one"
        )
        self.key = key
        self.block_id = block_id
        self.reason = reason


class StateCorrupt(StateError):
    """The file was unreadable and has just been quarantined."""


class StateUnreadable(StateError):
    """An I/O error that is not "the file does not exist"."""


# -- records ---------------------------------------------------------------


@dataclass
class PhaseState:
    """Progress of one loop — `design` before the work, `review` after it."""

    rounds: int = 0
    outcome: str | None = None
    stop_streak: int = 0
    # Blocking finding ids left open by the previous round. Comparing this set
    # across rounds is how a loop that restates itself is caught.
    unresolved_ids: list[str] = field(default_factory=list)


@dataclass
class Creating:
    """A thread/start that has been decided on but not yet confirmed."""

    request_id: str
    started_at: str
    payload_sha256: str
    previous_binding: dict[str, Any] | None = None


@dataclass
class OpenRequest:
    """The single request that is in flight or whose result nobody has acked."""

    request_id: str
    phase: str
    message_sha256: str
    status: str
    started_at: str
    turn_id: str | None = None
    result: dict[str, Any] | None = None


@dataclass
class SessionState:
    """The mapping, plus enough context to explain it to a human later."""

    state_key: str
    claude_session_id: str
    thread_name: str
    cwd: str
    profile: str
    created_at: str
    updated_at: str
    claude_host_session_id: str | None = None
    thread_id: str | None = None
    rollout_materialized: bool = False
    model: str | None = None
    effort: str | None = None
    # The sandbox the thread was created with, re-sent on resume and fork.
    sandbox: str | None = None
    codex_version: str | None = None
    creating: Creating | None = None
    open_request: OpenRequest | None = None
    last_applied_request_id: str | None = None
    migrating: dict[str, Any] | None = None
    migrated_from: str | None = None
    # The thread this one was forked from when Desktop held the original.
    forked_from: str | None = None
    # Threads we owe an archive to — a replaced binding, an original forked
    # because Desktop held it — retried on later rounds until it succeeds.
    archive_debt: list[str] = field(default_factory=list)
    # True: the name was set by --name and stays; False: it follows the Claude
    # chat title; None: a state written before the field existed.
    name_pinned: bool | None = None
    resolves_block_id: str | None = None
    phases: dict[str, PhaseState] = field(default_factory=dict)
    version: int = STATE_VERSION

    def phase(self, name: str) -> PhaseState:
        if name not in self.phases:
            self.phases[name] = PhaseState()
        return self.phases[name]

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.name_pinned is None:
            # Never written as null: absent is the only spelling of "this
            # state predates name pinning".
            payload.pop("name_pinned", None)
        payload["phases"] = {k: asdict(v) for k, v in self.phases.items()}
        payload["creating"] = asdict(self.creating) if self.creating else None
        payload["open_request"] = asdict(self.open_request) if self.open_request else None
        return payload

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> SessionState:
        """Parse any supported version; v1 is upgraded in memory."""
        version = payload.get("version", 1)
        if not isinstance(version, int) or version not in SUPPORTED_VERSIONS:
            raise ValueError(f"unsupported state version {version!r}")

        # v2 is read without coercion. `str(x)` on a number turns a wrong
        # value into a plausible one — `thread_id: 0` became `None`, which
        # reads as "no thread yet" and starts a second one. A v1 file is
        # legacy and still coerced, because it was written that way.
        strict = version >= 2

        def text(name: str, *, required: bool = False, container: dict | None = None) -> str | None:
            source = payload if container is None else container
            value = source.get(name)
            if value is None:
                if required:
                    raise ValueError(f"{name} is missing")
                return None
            if strict and not isinstance(value, str):
                raise ValueError(f"{name} is {type(value).__name__}, not a string")
            value = str(value)
            if not value and (required or strict):
                # An empty string is a value that lost its content. Reading it
                # as "not set" is how a second thread gets started next to a
                # live one.
                raise ValueError(f"{name} is empty")
            return value

        thread_id = text("thread_id")
        claude_session_id = text("claude_session_id", required=True)
        assert claude_session_id is not None
        if version == 1:
            state_key = claude_session_id
        else:
            state_key = text("state_key", required=True)
            assert state_key is not None

        # Nested records are parsed strictly. A malformed one is a schema
        # error that quarantines the file — silently dropping, say, an
        # `open_request` would let an already-accepted request be sent again.
        phases: dict[str, PhaseState] = {}
        raw_phases = payload.get("phases", {})
        if raw_phases is None:
            raw_phases = {}
        if not isinstance(raw_phases, dict):
            raise ValueError("phases is not an object")
        for name, value in raw_phases.items():
            if not isinstance(value, dict):
                raise ValueError(f"phase {name!r} is not an object")
            raw_ids = value.get("unresolved_ids", [])
            if not isinstance(raw_ids, list):
                raise ValueError(f"phase {name!r}: unresolved_ids is not a list")
            if strict and not all(isinstance(i, str) and i for i in raw_ids):
                raise ValueError(f"phase {name!r}: unresolved_ids holds something that is not an id")
            counters = {}
            for field_name in ("rounds", "stop_streak"):
                raw = value.get(field_name, 0)
                if raw is None:
                    raw = 0
                if strict and (not isinstance(raw, int) or isinstance(raw, bool) or raw < 0):
                    raise ValueError(f"phase {name!r}: {field_name} is not a count")
                counters[field_name] = int(raw)
            outcome = value.get("outcome")
            if strict and outcome is not None and not isinstance(outcome, str):
                raise ValueError(f"phase {name!r}: outcome is not a string")
            phases[str(name)] = PhaseState(
                rounds=counters["rounds"],
                outcome=outcome,
                stop_streak=counters["stop_streak"],
                unresolved_ids=[str(i) for i in raw_ids],
            )

        creating = None
        raw_creating = payload.get("creating")
        if raw_creating is not None:
            if not isinstance(raw_creating, dict) or not raw_creating.get("request_id"):
                raise ValueError("creating is malformed")
            if strict and not isinstance(raw_creating["request_id"], str):
                raise ValueError("creating.request_id is not a string")
            creating = Creating(
                request_id=str(raw_creating["request_id"]),
                started_at=str(raw_creating.get("started_at", "")),
                payload_sha256=str(raw_creating.get("payload_sha256", "")),
                previous_binding=raw_creating.get("previous_binding"),
            )
            if creating.previous_binding is not None and not isinstance(creating.previous_binding, dict):
                raise ValueError("creating.previous_binding is malformed")

        open_request = None
        raw_open = payload.get("open_request")
        if raw_open is not None:
            if not isinstance(raw_open, dict) or not raw_open.get("request_id"):
                raise ValueError("open_request is malformed")
            status = str(raw_open.get("status", ""))
            if status not in (REQUEST_PENDING, REQUEST_UNACKNOWLEDGED):
                raise ValueError(f"open_request.status {status!r} is unknown")
            result = raw_open.get("result")
            if result is not None and not isinstance(result, dict):
                raise ValueError("open_request.result is malformed")
            if strict and not isinstance(raw_open["request_id"], str):
                raise ValueError("open_request.request_id is not a string")
            open_request = OpenRequest(
                request_id=str(raw_open["request_id"]),
                phase=str(raw_open.get("phase", "design")),
                message_sha256=str(raw_open.get("message_sha256", "")),
                status=status,
                started_at=str(raw_open.get("started_at", "")),
                turn_id=raw_open.get("turn_id"),
                result=result,
            )

        migrating = _optional_record(payload, "migrating", ("migration_id", "destination_key"))
        raw_pinned = payload.get("name_pinned")
        if "name_pinned" in payload and not isinstance(raw_pinned, bool):
            # Absent means "written before this field existed"; present and
            # not a boolean — null included — is a value that lost its
            # content, and reading that as legacy would quietly unpin a name.
            raise ValueError("name_pinned is not a boolean")
        # Files written before forked originals were archived kept them in a
        # list of their own, to be deleted. They are archived now, like any
        # other debt: the human may have written into the original after the
        # fork, and deleting it would lose that.
        raw_super = payload.get("supersede_debt")
        if raw_super is None:
            if strict and "supersede_debt" in payload:
                raise ValueError("supersede_debt is null")
            raw_super = []
        if not isinstance(raw_super, list) or not all(isinstance(x, str) and x for x in raw_super):
            raise ValueError("supersede_debt is not a list of thread ids")
        raw_debt = payload.get("archive_debt")
        if raw_debt is None:
            # Absent is a v1 file that never had the field. Present and null
            # in a v2 file is a value that lost its content on the way in.
            if strict and "archive_debt" in payload:
                raise ValueError("archive_debt is null")
            raw_debt = []
        if not isinstance(raw_debt, list) or not all(isinstance(x, str) and x for x in raw_debt):
            raise ValueError("archive_debt is not a list of thread ids")
        for name in (
            "claude_host_session_id", "last_applied_request_id", "migrated_from",
            "forked_from", "resolves_block_id", "sandbox",
        ):
            if payload.get(name) is not None and not isinstance(payload.get(name), str):
                raise ValueError(f"{name} is not a string")

        return cls(
            state_key=state_key,
            claude_session_id=claude_session_id,
            claude_host_session_id=payload.get("claude_host_session_id"),
            thread_id=thread_id or None,
            thread_name=str(payload.get("thread_name", "")),
            cwd=str(payload.get("cwd", "")),
            profile=str(payload.get("profile", "default")),
            created_at=str(payload.get("created_at", "")),
            updated_at=str(payload.get("updated_at", "")),
            # v1 only ever saved after a completed turn, so its rollout exists.
            rollout_materialized=bool(payload.get("rollout_materialized", version == 1)),
            model=payload.get("model"),
            effort=payload.get("effort"),
            sandbox=payload.get("sandbox"),
            codex_version=payload.get("codex_version"),
            creating=creating,
            open_request=open_request,
            last_applied_request_id=payload.get("last_applied_request_id"),
            migrating=migrating,
            migrated_from=payload.get("migrated_from"),
            forked_from=payload.get("forked_from"),
            archive_debt=list(dict.fromkeys([*raw_debt, *raw_super])),
            name_pinned=raw_pinned,
            resolves_block_id=payload.get("resolves_block_id"),
            phases=phases,
            version=STATE_VERSION,
        )


# -- helpers ---------------------------------------------------------------


def _optional_record(payload: dict[str, Any], name: str, required: tuple[str, ...]) -> dict[str, Any] | None:
    """A nested recovery record: absent is fine, malformed is a schema error.

    Dropping a half-parsed ``migrating`` or ``forking`` would forget a move or
    a fork that is already on disk, so anything but a well-formed object is
    refused and the file goes to quarantine.
    """
    raw = payload.get(name)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"{name} is not an object")
    for field_name in required:
        value = raw.get(field_name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name}.{field_name} is missing or not a string")
    return dict(raw)


def _crash_point(name: str) -> None:
    """Test hook mirroring consult.crash_point; off unless DUO_TEST_CRASH_AT is set."""
    if os.environ.get("DUO_TEST_CRASH_AT") == name:
        os._exit(137)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return str(uuid.uuid4())


def state_key_for(host_session_id: str | None, cli_session_id: str) -> str:
    """The key a session files under: host id when present, else CLI id."""
    host = (host_session_id or "").strip()
    return host if host else cli_session_id.strip()


def _safe(key: str) -> str:
    """Refuse anything that is not a plain identifier.

    Keys come from the environment. Rejecting instead of sanitising means a
    surprising value can never escape the state directory.
    """
    cleaned = key.strip()
    if not cleaned or not all(c.isalnum() or c in "-_" for c in cleaned):
        raise ValueError(f"refusing to use {key!r} as a state file name")
    return cleaned


def state_path(key: str) -> Path:
    return state_dir() / f"{_safe(key)}.json"


def lock_path(key: str) -> Path:
    return state_dir() / f"{_safe(key)}.lock"


def blocked_path(key: str) -> Path:
    return state_dir() / f"{_safe(key)}.blocked"


def _fsync_directory(directory: Path) -> None:
    """Make a rename or unlink itself durable, not just a file's contents.

    Not every filesystem supports it, so a refusal is tolerated — but the
    descriptor is closed in ``finally``, because a leak here accumulates one
    lost descriptor per save.
    """
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _write_atomic(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=f".{path.stem}.",
        suffix=".tmp",
        delete=False,
    )
    tmp_path = Path(handle.name)
    try:
        with handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        _fsync_directory(path.parent)
    except BaseException:
        # Cleanup must never mask the error that got us here.
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


# -- locking ---------------------------------------------------------------


# Keys this process holds a lock on right now. Bookkeeping beside the kernel's
# lock, so a function that writes across two keys can refuse when one of them
# is not held — the class of bug where a route is re-resolved after locking and
# the write lands on a key nobody took.
_HELD_KEYS: set[str] = set()


def held_keys() -> frozenset[str]:
    return frozenset(_HELD_KEYS)


def require_held(*keys: str) -> None:
    missing = [key for key in keys if key not in _HELD_KEYS]
    if missing:
        raise StateError(
            "refusing to write " + ", ".join(missing) + " without holding the lock: "
            "the route changed after it was resolved. Run the command again"
        )


class SessionLock:
    """``flock`` on one or more keys, taken in a stable order.

    The lock file is persistent and never deleted: deleting it would let a
    second process lock a *different* inode and both would believe they own
    the session.
    """

    def __init__(self, keys: list[str]) -> None:
        if fcntl is None:
            raise StateError("session locking needs fcntl; Windows is not supported")
        self._keys = sorted(set(keys))
        self._fds: list[int] = []
        self._registered: list[str] = []

    def __enter__(self) -> SessionLock:
        state_dir().mkdir(parents=True, exist_ok=True)
        for key in self._keys:
            fd = os.open(lock_path(key), os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                os.close(fd)
                self.__exit__(None, None, None)
                if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                    raise StateLocked(
                        f"session {key} is in use by another duo process; "
                        "wait for it, or check `duo status`"
                    ) from exc
                raise
            # Diagnostics only; ownership is the kernel's lock, not this text.
            try:
                os.ftruncate(fd, 0)
                os.write(fd, f"{os.getpid()} {now_iso()}\n".encode())
            except OSError:
                pass
            self._fds.append(fd)
            _HELD_KEYS.add(key)
            self._registered.append(key)
        return self

    def __exit__(self, *_exc: object) -> None:
        for fd in reversed(self._fds):
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(fd)
            except OSError:
                pass
        self._fds = []
        for key in self._registered:
            _HELD_KEYS.discard(key)
        self._registered = []


def lock(key: str) -> SessionLock:
    return SessionLock([key])


def lock_many(keys: list[str]) -> SessionLock:
    return SessionLock(keys)


# -- load / save -------------------------------------------------------------


def _read_marker(key: str) -> dict[str, Any] | None:
    path = blocked_path(key)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StateUnreadable(f"cannot read quarantine marker {path}: {exc}") from exc
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        payload = {}
    if not isinstance(payload, dict) or not payload.get("block_id"):
        # A marker we cannot read is still a marker: fail closed with an id
        # nobody will ever match, so only --new or recover can clear it.
        return {"block_id": "unreadable", "reason": "marker unreadable"}
    return payload


def _read_file(key: str) -> dict[str, Any] | None:
    """Raw JSON of the state file, ``None`` only for ENOENT."""
    path = state_path(key)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StateUnreadable(f"cannot read {path}: {exc}") from exc
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("state is not a JSON object")
    return payload


def quarantine(key: str, reason: str) -> str:
    """Block the key, then move the bad file aside. Returns the block id.

    Order matters: the marker goes first and is fsynced, so a crash after it
    leaves the session blocked rather than looking absent. If the marker
    cannot be written, the file is left untouched and the error propagates.
    """
    block_id = new_id()
    _write_atomic(
        blocked_path(key),
        json.dumps(
            {"block_id": block_id, "reason": reason, "quarantined_at": now_iso()},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    path = state_path(key)
    stamp = now_iso().replace(":", "-")
    try:
        os.replace(path, path.with_name(f"{path.name}.corrupt-{stamp}"))
    except FileNotFoundError:
        pass
    _fsync_directory(path.parent)
    return block_id


def _release_marker(key: str) -> None:
    try:
        blocked_path(key).unlink()
    except FileNotFoundError:
        return
    except OSError:
        return
    _fsync_directory(state_dir())


def load(key: str) -> SessionState | None:
    """The state under ``key``, ``None`` only when nothing was ever there.

    Raises ``StateBlocked`` when a quarantine marker is in force,
    ``StateCorrupt`` after quarantining an unreadable file, and
    ``StateUnreadable`` for any I/O error that is not ENOENT. None of those
    is "absent": treating them as absent is how a recoverable fault turns
    into a second thread.
    """
    marker = _read_marker(key)
    payload = None
    parse_error: str | None = None
    try:
        payload = _read_file(key)
    except ValueError as exc:
        parse_error = str(exc)

    if marker is not None:
        block_id = str(marker.get("block_id"))
        # A valid replacement that names this block finishes the recovery a
        # crash may have cut short between save and unlink.
        if payload is not None and parse_error is None:
            try:
                candidate = SessionState.from_json(payload)
            except (KeyError, TypeError, ValueError):
                candidate = None
            if candidate is not None and candidate.resolves_block_id == block_id:
                _release_marker(key)
                return candidate
        raise StateBlocked(key, block_id, str(marker.get("reason", "unknown")))

    # Order matters: an unparseable file is not an absent one. Checking for
    # absence first was exactly the fail-open the design forbids.
    if parse_error is not None:
        block_id = quarantine(key, parse_error)
        raise StateCorrupt(
            f"state for {key} was corrupt ({parse_error}); it has been moved aside "
            f"and the session is blocked (block {block_id[:8]}). "
            "Pass --new to start over, or `duo recover` to restore a copy."
        )

    if payload is None:
        return None

    try:
        return SessionState.from_json(payload)
    except (KeyError, TypeError, ValueError) as exc:
        block_id = quarantine(key, f"invalid schema: {exc}")
        raise StateCorrupt(
            f"state for {key} did not match the schema ({exc}); it has been moved "
            f"aside and the session is blocked (block {block_id[:8]}). "
            "Pass --new to start over, or `duo recover` to restore a copy."
        ) from exc


def save(key: str, state: SessionState) -> Path:
    """Write the state atomically under ``key`` and return where it landed.

    The path comes from the key, never from the payload: a field that changes
    across rounds must not move the file.
    """
    state.updated_at = now_iso()
    state.version = STATE_VERSION
    # A key being written again is a live binding, not a retired one.
    try:
        moved_path(key).unlink()
    except FileNotFoundError:
        pass
    path = state_path(key)
    _write_atomic(path, json.dumps(state.to_json(), ensure_ascii=False, indent=2) + "\n")
    if state.resolves_block_id:
        marker = _read_marker(key)
        if marker is not None and marker.get("block_id") == state.resolves_block_id:
            _release_marker(key)
    return path


def discard(key: str, *, keep_tombstone: bool = True) -> bool:
    """Forget the mapping. The quarantine marker stays unless told otherwise.

    `close` is not `--new`: a session that was blocked after corruption stays
    blocked, or the next plain `ask` would treat it as fresh.
    """
    removed = False
    paths = [state_path(key)] if keep_tombstone else [state_path(key), blocked_path(key)]
    for path in paths:
        try:
            path.unlink()
            removed = True
        except FileNotFoundError:
            continue
        except OSError:
            continue
    if removed:
        _fsync_directory(state_dir())
    return removed


def current_block_id(key: str) -> str | None:
    marker = _read_marker(key)
    return str(marker["block_id"]) if marker else None


# -- migration ---------------------------------------------------------------


def migrate(src_key: str, dst_key: str, *, resolves_block_id: str | None = None) -> SessionState:
    """Move a mapping between keys as a two-phase, crash-resumable step.

    Callers hold ``lock_many([src, dst])``. Phases:

    1. The source is rewritten with a durable ``migrating`` record naming the
       destination and a ``migration_id``. From here on it is never served as
       a binding of its own — the loader finishes the move instead.
    2. The destination is written as version 2 with ``migrated_from``.
    3. The source is renamed to ``<src>.json.migrated-<ISO>``.

    A destination that already holds a *different* thread is a conflict, and
    the function fails closed rather than merging or overwriting.
    """
    # Before anything that can write: `load` below quarantines a corrupt file,
    # and a caller that never took the lock must not get that far.
    require_held(src_key, dst_key)
    if src_key == dst_key:
        raise StateError("source and destination keys are the same")

    src = load(src_key)
    if src is None:
        raise StateError(f"nothing to migrate: no state under {src_key}")

    forwarded = moved_target(dst_key)
    if forwarded:
        require_held(forwarded)
        # The destination has itself moved on since. Follow it, so an old move
        # cannot resurrect a key that was already retired. resolve_key locks
        # the whole chain, so the forward target is held too.
        return migrate(src_key, forwarded, resolves_block_id=resolves_block_id)

    migration_id = (src.migrating or {}).get("migration_id") or new_id()

    dst_existing: SessionState | None = None
    try:
        dst_existing = load(dst_key)
    except StateBlocked as blocked:
        if resolves_block_id != blocked.block_id:
            raise StateError(
                f"destination {dst_key} is blocked after quarantine; pass the block through "
                "`duo recover` or clear it with --new first"
            ) from None
    if dst_existing is not None:
        same_move = dst_existing.migrated_from == migration_id
        same_thread = bool(dst_existing.thread_id) and dst_existing.thread_id == src.thread_id
        if same_move or same_thread:
            # A destination that already exists is newer than the source by
            # construction — it was written after the source was marked. It
            # wins; only the source's retirement is still pending.
            _retire_source(src_key, dst_key)
            return dst_existing
        raise StateError(
            f"destination {dst_key} already holds thread {dst_existing.thread_id}; "
            "refusing to overwrite it"
        )

    # Phase 1: mark the source. Durable before anything touches the destination.
    # A source already marked for somewhere else is mid-move there, and a move
    # redirected halfway leaves two live bindings for one thread.
    marked_for = (src.migrating or {}).get("destination_key")
    if marked_for and marked_for != dst_key:
        raise StateError(
            f"{src_key} is mid-move to {marked_for}; that move has to finish "
            "before it can go anywhere else"
        )
    if not src.migrating:
        src.migrating = {"migration_id": migration_id, "destination_key": dst_key}
        save(src_key, src)
    _crash_point("after_migration_mark")

    # Phase 2: write the destination as the canonical binding. A matching
    # resolves_block_id releases a quarantine marker on the destination in
    # the same save, so recover into a blocked key is one durable step.
    moved = SessionState.from_json(src.to_json())
    moved.state_key = dst_key
    moved.migrating = None
    moved.migrated_from = migration_id
    if resolves_block_id:
        moved.resolves_block_id = resolves_block_id
    save(dst_key, moved)
    _crash_point("after_migration_dst")

    # Phase 3: retire the source, leaving the redirect behind.
    _retire_source(src_key, dst_key)
    return moved


def _retire_source(src_key: str, dst_key: str) -> None:
    """Move the source file aside and leave a pointer to where it went.

    Without the pointer a retired key reads as absent, and an older move that
    still names it as its destination would recreate it — a second live
    binding for one thread. The redirect is written first, so a crash between
    the two leaves a pointer to a key that still exists, which is harmless.
    """
    _write_atomic(
        moved_path(src_key),
        json.dumps({"destination_key": dst_key, "at": now_iso()}, ensure_ascii=False) + "\n",
    )
    src_path = state_path(src_key)
    stamp = now_iso().replace(":", "-")
    try:
        os.replace(src_path, src_path.with_name(f"{src_path.name}.migrated-{stamp}"))
    except FileNotFoundError:
        pass
    _fsync_directory(src_path.parent)


def moved_path(key: str) -> Path:
    return state_dir() / f"{_safe(key)}.moved"


def moved_target(key: str) -> str | None:
    """Where a retired key was migrated to, if it was.

    Only "the file is not there" means there is no redirect. An unreadable or
    unparseable one is a fault: swallowing it turns a retired key back into a
    free one, and the binding it points at gets recreated beside the live one.
    """
    try:
        raw = moved_path(key).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StateUnreadable(f"could not read the migration redirect for {key}: {exc}") from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise StateUnreadable(f"the migration redirect for {key} is not JSON: {exc}") from None
    target = payload.get("destination_key") if isinstance(payload, dict) else None
    if target is None:
        raise StateUnreadable(f"the migration redirect for {key} names no destination")
    if not isinstance(target, str) or not target:
        raise StateUnreadable(f"the migration redirect for {key} names {target!r}")
    if target == key:
        raise StateError(
            f"the migration redirect for {key} points at itself. Repair it with "
            "`duo repair-redirect` before continuing"
        )
    return target


MAX_MIGRATION_CHAIN = 32


def migration_chain(key: str) -> list[str]:
    """Every key a move filed under ``key`` can reach, in order.

    Follows both a half-done move recorded in the state and the redirect left
    by a finished one, so a caller can lock the whole chain before touching any
    of it.

    A cycle or a chain past the limit raises. Stopping the walk quietly would
    hand the caller a key that is not the end of the route, and a round under
    a key that is not the end of the route starts a second thread.
    """
    chain: list[str] = []
    seen = [key]
    current = key
    for _ in range(MAX_MIGRATION_CHAIN):
        nxt = pending_migration_target(current) or moved_target(current)
        if not nxt:
            return chain
        if nxt in seen:
            raise StateError(
                "the migration redirects form a loop: "
                + " -> ".join(seen + [nxt])
                + ". Repair it with `duo repair-redirect <key>` before continuing"
            )
        chain.append(nxt)
        seen.append(nxt)
        current = nxt
    raise StateError(
        f"the migration chain from {key} is longer than {MAX_MIGRATION_CHAIN} keys; "
        "repair it with `duo repair-redirect <key>` before continuing"
    )


def repair_redirect(key: str, destination: str | None) -> str | None:
    """Set aside or rewrite the redirect filed under ``key``.

    The way out of a redirect that cannot be read. Without a destination the
    file is quarantined and the key reads as live again; with one it is
    rewritten to point there. The caller holds the lock.
    """
    path = moved_path(key)
    if destination:
        _write_atomic(
            path,
            json.dumps({"destination_key": destination, "at": now_iso()}, ensure_ascii=False) + "\n",
        )
        return destination
    stamp = now_iso().replace(":", "-")
    try:
        os.replace(path, path.with_name(f"{path.name}.corrupt-{stamp}"))
    except FileNotFoundError:
        return None
    _fsync_directory(path.parent)
    return None


def pending_migration_target(key: str, *, strict: bool = False) -> str | None:
    """The destination of a half-done move filed under ``key``, if any.

    A source left with a ``migrating`` record is never used as a live binding;
    the caller takes both locks and calls ``migrate`` to finish the move.
    ``flock`` conflicts with itself across descriptors in one process, so
    this function takes no lock of its own.

    ``strict`` is for a caller that already holds the lock: the state is read
    the strict way, so a file that cannot be read raises instead of reading as
    "no move pending" — which would let the destination's absence pass for a
    session that never existed.
    """
    state = load(key) if strict else _peek(key)
    if state is None or not state.migrating:
        return None
    dst_key = str(state.migrating.get("destination_key") or "")
    if not dst_key:
        raise StateError(f"the move filed under {key} names no destination")
    if dst_key == key:
        raise StateError(f"the move filed under {key} points at itself")
    return dst_key


def _peek(key: str) -> SessionState | None:
    """Parse the file under ``key`` with no side effect at all.

    ``load`` quarantines a corrupt file and releases a marker — both writes,
    and this runs before any lock is held. Anything that is not a clean,
    unblocked record reads as None here; the locked ``load`` that follows is
    where a fault is acted on.
    """
    if _read_marker(key) is not None:
        return None
    try:
        payload = _read_file(key)
        return SessionState.from_json(payload) if payload is not None else None
    except (StateError, OSError, KeyError, TypeError, ValueError):
        return None
