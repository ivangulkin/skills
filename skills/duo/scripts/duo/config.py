#!/usr/bin/env python3
"""Every tunable value the consultation loop has, in one place.

Model ids and reasoning efforts are read from the installed Codex model
catalogue rather than guessed: ``~/.codex/models_cache.json`` lists the slugs
and the efforts each slug advertises. The defaults below were checked against
that file on 03.09.2026.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil


# The app-server protocol is versioned with the binary, so every version the
# code has actually been checked against is listed. `verify` reports anything
# else instead of failing at the first unknown field: a newer Codex usually
# still works, it just has not been checked.
#
# 0.153.0-alpha.5 was diffed against 0.152.1 field by field: one new method
# (`plugin/reconcile`), and no change to any parameter, enum or notification
# this code touches.
VERIFIED_CODEX_VERSIONS = ("0.152.1", "0.153.0-alpha.5", "0.153.4")
TESTED_CODEX_VERSION = VERIFIED_CODEX_VERSIONS[-1]

# Where the Codex binary lives when ChatGPT.app ships it. `codex` is normally
# absent from PATH, so the bundle path is tried first.
BUNDLED_CODEX = Path("/Applications/ChatGPT.app/Contents/Resources/codex")

# Hard backstop on a debate. Three is the ceiling every surveyed implementation
# converged on; it is a backstop, not the mechanism — see STOP_HOLD_ROUNDS.
MAX_ROUNDS = 3

# A stop flag has to survive this many consecutive rounds before the loop exits
# early. One round of "we agree" is cheap to produce and means little.
STOP_HOLD_ROUNDS = 2

# A working turn is never cut for taking long: gpt-5.6-sol at `max` runs for
# thirty minutes or an hour on a real brief, streaming events the whole way.
# What a stuck turn does is go silent. So the budget that matters is silence —
# no message of any kind from the server for this long — and the wall-clock
# cap is off unless a caller asks for one.
TURN_IDLE_TIMEOUT_SEC = 900
TURN_HARD_TIMEOUT_SEC: float | None = None

# Handshake and short control calls. A separate, much smaller budget: if
# `initialize` has not answered in this long, the process is wedged.
CONTROL_TIMEOUT_SEC = 60

# A message this long is almost always a pasted diff or file. Codex reads the
# repository itself, so a pointer beats a copy — and a copy this size is what
# blows the context window mid-round. Raised deliberately with a flag.
MAX_MESSAGE_CHARS = 120_000

# Standard speed. The catalogue's default tier is `priority`, described as
# "1.5x speed, increased usage" — the loop should not silently spend extra
# quota, so both profiles ask for the standard tier explicitly.
SERVICE_TIER_STANDARD = "default"


@dataclass(frozen=True)
class Profile:
    """A model choice for one side of the conversation."""

    name: str
    model: str
    effort: str
    service_tier: str = SERVICE_TIER_STANDARD

    def describe(self) -> str:
        return f"{self.model} effort={self.effort} tier={self.service_tier}"


@dataclass(frozen=True)
class ModelInfo:
    """One entry of the installed model catalogue."""

    slug: str
    display_name: str
    default_effort: str
    efforts: tuple[str, ...]
    speed_tiers: tuple[str, ...]
    default_service_tier: str | None


# Used when the catalogue cannot be read. Copied from
# ~/.codex/models_cache.json on 05.09.2026; a fallback, not a contract.
_FALLBACK_CATALOGUE: tuple[ModelInfo, ...] = (
    ModelInfo("gpt-6-astra", "GPT-6-Astra", "medium",
              ('low', 'medium', 'high', 'xhigh', 'max', 'ultra'), ('fast',), None),
    ModelInfo("gpt-5.6-sol", "GPT-5.6-Sol", "low",
              ('low', 'medium', 'high', 'xhigh', 'max', 'ultra'), ('fast',), None),
    ModelInfo("gpt-5.6-terra", "GPT-5.6-Terra", "medium",
              ('low', 'medium', 'high', 'xhigh', 'max', 'ultra'), ('fast',), None),
    ModelInfo("gpt-5.6-luna", "GPT-5.6-Luna", "medium",
              ('low', 'medium', 'high', 'xhigh', 'max'), ('fast',), None),
    ModelInfo("gpt-5.5", "GPT-5.5", "medium",
              ('low', 'medium', 'high', 'xhigh'), ('fast',), None),
    ModelInfo("gpt-5.4-mini", "GPT-5.4-Mini", "medium",
              ('low', 'medium', 'high', 'xhigh'), (), None),
    ModelInfo("gpt-5.3-codex-spark", "GPT-5.3-Codex-Spark", "high",
              ('low', 'medium', 'high', 'xhigh'), (), None),
)


class ConfigError(RuntimeError):
    """Something about this machine's setup that a human has to fix."""


def models_cache_path() -> Path:
    override = os.environ.get("DUO_MODELS_CACHE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".codex" / "models_cache.json"


def model_catalogue() -> tuple[ModelInfo, ...]:
    """Models the installed Codex knows about, hidden ones excluded.

    Read from disk rather than pinned in code: Codex refreshes this file
    itself, so it is the freshest truth available locally.
    """
    try:
        payload = json.loads(models_cache_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return _FALLBACK_CATALOGUE

    entries = payload.get("models")
    if not isinstance(entries, list):
        return _FALLBACK_CATALOGUE

    found: list[ModelInfo] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("visibility") == "hide":
            continue
        slug = entry.get("slug")
        if not isinstance(slug, str) or not slug:
            continue
        levels = entry.get("supported_reasoning_levels")
        efforts = (
            tuple(
                str(item.get("effort"))
                for item in levels
                if isinstance(item, dict) and item.get("effort")
            )
            if isinstance(levels, list)
            else ()
        )
        tiers = entry.get("additional_speed_tiers")
        found.append(
            ModelInfo(
                slug=slug,
                display_name=str(entry.get("display_name") or slug),
                default_effort=str(entry.get("default_reasoning_level") or "medium"),
                efforts=efforts,
                speed_tiers=tuple(str(t) for t in tiers) if isinstance(tiers, list) else (),
                default_service_tier=entry.get("default_service_tier"),
            )
        )
    return tuple(found) or _FALLBACK_CATALOGUE


def describe_model(slug: str) -> ModelInfo | None:
    for info in model_catalogue():
        if info.slug == slug:
            return info
    return None


def effort_warning(model: str | None, effort: str | None) -> str | None:
    """Note an effort the catalogue does not list, without refusing it.

    Both fields are free strings in the protocol. The catalogue is the best
    local check and it can lag the service, so this returns a line to print
    rather than raising.
    """
    if not model or not effort:
        return None
    info = describe_model(model)
    if info is None:
        return f"model {model!r} is not in the local catalogue; sending it anyway"
    if info.efforts and effort not in info.efforts:
        return (
            f"effort {effort!r} is not listed for {model}; "
            f"the catalogue has {', '.join(info.efforts)}"
        )
    return None


# Words people actually say, mapped onto the efforts the protocol takes.
# "light" is the user's own word for the cheap end; the rest are the schema's.
EFFORT_ALIASES: dict[str, str] = {
    "light": "low",
    "lite": "low",
    "low": "low",
    "med": "medium",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "extra": "xhigh",
    "max": "max",
    "ultra": "ultra",
}


def _tokens(text: str) -> list[str]:
    """Split a human model spec into comparable pieces.

    Anything that is not a letter, digit or dot separates: "5.6 luna light",
    "5.6-luna-light" and "luna/light" all come apart the same way.
    """
    out: list[str] = []
    current: list[str] = []
    for char in text.lower():
        if char.isalnum() or char == ".":
            current.append(char)
        elif current:
            out.append("".join(current))
            current = []
    if current:
        out.append("".join(current))
    return out


def resolve_model_spec(spec: str) -> tuple[str, str | None]:
    """Turn "5.6 luna light" into ("gpt-5.6-luna", "low").

    An exact slug passes through untouched. Otherwise the words are matched
    against the installed catalogue, so this keeps working when models change
    without anyone editing a table here.

    Returns the model and, when the spec named one, the effort.
    """
    cleaned = spec.strip()
    if not cleaned:
        raise ValueError("empty model spec")

    catalogue = model_catalogue()
    for info in catalogue:
        if cleaned == info.slug:
            return info.slug, None

    words = _tokens(cleaned)
    effort: str | None = None
    hints: list[str] = []
    for word in words:
        if word in EFFORT_ALIASES and effort is None:
            effort = EFFORT_ALIASES[word]
        else:
            hints.append(word)

    if not hints:
        raise ValueError(f"{spec!r} names an effort but no model")

    matches = [
        info
        for info in catalogue
        if all(hint in set(_tokens(info.slug)) for hint in hints)
    ]
    if not matches:
        known = ", ".join(info.slug for info in catalogue)
        raise ValueError(f"{spec!r} matches no model. Known: {known}")
    if len(matches) > 1:
        # "5.4" hits both gpt-5.4 and gpt-5.4-mini. The shortest slug is the
        # plain one, which is what a bare version number means.
        matches.sort(key=lambda info: len(_tokens(info.slug)))
        if len(_tokens(matches[0].slug)) == len(_tokens(matches[1].slug)):
            names = ", ".join(info.slug for info in matches)
            raise ValueError(f"{spec!r} is ambiguous between {names}")

    return matches[0].slug, effort


# `default` is what real consultations use. `test` exists to exercise the loop
# without spending the good quota: a cheaper model at the lowest effort, still
# on standard speed. Everything else is one profile per catalogue model at its
# own default effort, so any of them can be reached by name.
_INTENT_PROFILES: dict[str, Profile] = {
    "default": Profile(name="default", model="gpt-6-astra", effort="max"),
    "test": Profile(name="test", model="gpt-5.6-luna", effort="low"),
}


def profiles() -> dict[str, Profile]:
    """Named profiles, rebuilt from the catalogue on each call."""
    built = dict(_INTENT_PROFILES)
    for info in model_catalogue():
        built.setdefault(
            info.slug,
            Profile(name=info.slug, model=info.slug, effort=info.default_effort),
        )
    return built


# A module-level name because the CLI builds its `--profile` choices while the
# parser is being constructed.
PROFILES: dict[str, Profile] = profiles()

DEFAULT_PROFILE = "default"


def state_dir() -> Path:
    """Directory holding one JSON file per Claude Code session.

    Overridable so tests never touch the real one.
    """
    override = os.environ.get("DUO_STATE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".claude" / "state" / "duo"


def codex_binary() -> Path:
    """Locate the Codex CLI, preferring an explicit override.

    Raises ``FileNotFoundError`` with a directly actionable message: a missing
    binary is the one failure a user can fix without reading any code.
    """
    override = os.environ.get("DUO_CODEX_BIN")
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
        raise FileNotFoundError(
            f"DUO_CODEX_BIN points at {candidate}, which is not an executable file"
        )

    if BUNDLED_CODEX.is_file() and os.access(BUNDLED_CODEX, os.X_OK):
        return BUNDLED_CODEX

    on_path = shutil.which("codex")
    if on_path:
        return Path(on_path)

    raise FileNotFoundError(
        "Codex CLI not found. Looked at "
        f"{BUNDLED_CODEX}, then PATH. Set DUO_CODEX_BIN to its location."
    )


def claude_session_id() -> str | None:
    """The one durable key tying a Claude conversation to a Codex thread.

    `CLAUDE_CODE_SESSION_ID` is present in the Bash tool environment. The
    variable is *not* called `CLAUDE_SESSION_ID`; that name does not exist.
    """
    value = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    if value:
        return value
    # For running outside Claude Code — tests, another host, a shell — the
    # key can be supplied by hand. Same durability rule applies: pick one and
    # keep using it.
    return os.environ.get("DUO_SESSION_ID", "").strip() or None


def project_slug(cwd: Path) -> str:
    """Claude Code's directory name for a project.

    Both ``/`` and ``.`` become ``-``: ``/Users/jane.doe/git/x`` is stored as
    ``-Users-jane-doe-git-x``. Checked against the real directory, because the
    obvious "replace the slashes" rule gets the username wrong.
    """
    return str(cwd.resolve()).replace("/", "-").replace(".", "-")


def projects_root() -> Path:
    override = os.environ.get("DUO_PROJECTS_ROOT")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".claude" / "projects"


def read_chat_cwd(session_id: str, cwd_hint: Path) -> tuple[bool, Path | None]:
    """The directory the Claude chat was started in, from its transcript.

    Returns ``(transcript_found, directory)``. The first record carrying a
    top-level absolute ``cwd`` is the chat's project directory: checked on
    every transcript on this machine on 08.09.2026, it matched the directory
    the transcript is filed under in all 56, and in 23 of them the shell's cwd
    drifted later — which is exactly why `$PWD` at call time is the wrong
    anchor. The field is not on the first line: it arrives with the first
    `user` record, third to ninth line, once in a `system` one.
    """
    path = transcript_path(session_id, cwd_hint)
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return False, None
    except OSError as exc:
        # The transcript is there and unreadable. Falling back to the shell's
        # directory would silently anchor the thread somewhere else; the
        # caller is told to name a directory instead.
        raise ConfigError(f"cannot read the Claude transcript {path}: {exc}") from None
    with handle:
        for line in handle:
            if '"cwd"' not in line:
                continue
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(record, dict) or "cwd" not in record:
                continue
            value = record.get("cwd")
            # The *first* record carrying the field decides. A later one is
            # the shell's drift, which is the whole reason this function
            # exists; a first one that is not an absolute path is a fault to
            # report, not something to skip past.
            if isinstance(value, str) and value and Path(value).is_absolute():
                return True, Path(value)
            return True, None
    return True, None


def host_session_id() -> str | None:
    """The id that survives a restart of the conversation, when there is one.

    Measured 04.09.2026: Claude Desktop restarted the session, the CLI id
    changed, `CLAUDE_CODE_HOST_SESSION_ID` (`local_<uuid>`) did not. It is the
    key a mapping is filed under whenever it is present. Overridable with
    `DUO_HOST_SESSION_ID` for tests and other hosts.
    """
    for name in ("CLAUDE_CODE_HOST_SESSION_ID", "DUO_HOST_SESSION_ID"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def transcript_path(session_id: str, cwd: Path) -> Path:
    """Locate the JSONL transcript for a session.

    The slug is a fast path, not a contract — it is undocumented and one
    unexpected character would break it silently. A session id is a UUID and
    therefore unique across every project, so a miss falls back to searching
    for the file by name. The returned path may not exist; callers treat a
    missing transcript as "no title yet".
    """
    root = projects_root()
    guess = root / project_slug(cwd) / f"{session_id}.jsonl"
    if guess.is_file():
        return guess

    try:
        for candidate in root.glob(f"*/{session_id}.jsonl"):
            if candidate.is_file():
                return candidate
    except OSError:
        pass
    return guess


def read_chat_title(session_id: str, cwd: Path) -> str | None:
    """Best-effort human-readable name of the current Claude chat.

    Two record types carry it, both appended repeatedly as the chat goes on, so
    the last occurrence wins and a human-set title beats a generated one. The
    transcript format is internal and undocumented: every failure here returns
    ``None`` and the caller falls back to a synthetic name.
    """
    path = transcript_path(session_id, cwd)
    custom: str | None = None
    generated: str | None = None

    try:
        # Streamed, not slurped: a long conversation's transcript runs to
        # megabytes and the title is two short records inside it.
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                # Both record types end in `-title`; anything else is skipped
                # without paying for a JSON parse.
                if "-title" not in line:
                    continue
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(record, dict):
                    continue
                if record.get("type") == "custom-title":
                    value = record.get("customTitle")
                    if isinstance(value, str) and value.strip():
                        custom = value.strip()
                elif record.get("type") == "ai-title":
                    value = record.get("aiTitle")
                    if isinstance(value, str) and value.strip():
                        generated = value.strip()
    except OSError:
        return None

    return custom or generated
