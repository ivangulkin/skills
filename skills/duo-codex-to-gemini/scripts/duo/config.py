#!/usr/bin/env python3
"""Every tunable value the consultation loop has, in one place.

In ``duo-codex-to-gemini``, the caller is Codex (identified by
``CODEX_THREAD_ID`` and ``~/.codex/state_5.sqlite``) and the opponent is
Gemini / Antigravity driven through ``agy --app_data_dir=antigravity``.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3


VERIFIED_CODEX_VERSIONS = (
    "0.152.1",
    "0.153.0-alpha.5",
    "0.153.4",
    "0.160.1",
    "0.166.0-alpha.5.1",
    "1.4.12",
)

BUNDLED_AGY = Path.home() / ".gemini" / "bin" / "agy"
BUNDLED_CODEX = BUNDLED_AGY

MAX_ROUNDS = 3
STOP_HOLD_ROUNDS = 2
TURN_IDLE_TIMEOUT_SEC = 900
TURN_HARD_TIMEOUT_SEC: float | None = None
CONTROL_TIMEOUT_SEC = 60
MAX_MESSAGE_CHARS = 120_000
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


# Models supported by `agy --app_data_dir=antigravity models`.
_GEMINI_CATALOGUE: tuple[ModelInfo, ...] = (
    ModelInfo(
        "gemini-4-argon",
        "Gemini 4 Argon (Pro)",
        "high",
        ("low", "medium", "high"),
        (),
        None,
    ),
    ModelInfo(
        "gemini-3.8-flash",
        "Gemini 3.8 Flash",
        "medium",
        ("low", "medium", "high"),
        (),
        None,
    ),
    ModelInfo(
        "claude-opus-5-5",
        "Claude Opus 4.6",
        "high",
        ("low", "medium", "high"),
        (),
        None,
    ),
    ModelInfo(
        "claude-sonnet-5-5",
        "Claude Sonnet 4.6",
        "medium",
        ("low", "medium", "high"),
        (),
        None,
    ),
    ModelInfo(
        "gpt-oss-120b",
        "GPT-OSS 120B",
        "medium",
        ("medium",),
        (),
        None,
    ),
)

_FALLBACK_CATALOGUE: tuple[ModelInfo, ...] = _GEMINI_CATALOGUE + (
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
    """Models available for consultation.

    By default returns the Antigravity model catalogue (`_GEMINI_CATALOGUE`).
    When ``DUO_MODELS_CACHE`` is set explicitly, reads that file; when
    ``DUO_CODEX_BIN`` is set in inherited contract tests, includes fallback
    Codex entries so legacy test model specs resolve cleanly.
    """
    override = os.environ.get("DUO_MODELS_CACHE")
    if not override:
        if os.environ.get("DUO_CODEX_BIN") and not os.environ.get("DUO_AGY_BIN"):
            return _FALLBACK_CATALOGUE
        return _GEMINI_CATALOGUE
    try:
        payload = json.loads(Path(override).expanduser().read_text(encoding="utf-8"))
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

MODEL_ALIASES: dict[str, str] = {
    "pro": "gemini-4-argon",
    "argon": "gemini-4-argon",
    "flash": "gemini-3.8-flash",
    "opus": "claude-opus-5-5",
    "sonnet": "claude-sonnet-5-5",
}


def normalize_agy_model_and_effort(
    model: str | None, effort: str | None
) -> tuple[str | None, str | None]:
    """Split combined agy slugs like ``gemini-4-argon-high`` into base + effort.

    ``agy`` rejects ``--model gemini-4-argon-medium --effort high`` with a
    conflict error; passing the base model slug and ``--effort`` separately is
    always accepted.
    """
    if not model:
        return None, effort
    cleaned = model.strip()
    for base in ("gemini-4-argon", "gemini-3.8-flash", "claude-opus-5-5", "claude-sonnet-5-5"):
        for suffix in ("low", "medium", "high"):
            if cleaned == f"{base}-{suffix}":
                return base, effort or suffix
    return cleaned, effort


def _tokens(text: str) -> list[str]:
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
    """Turn human model specs like ``pro high`` or ``flash low`` into ``(slug, effort)``."""
    cleaned = spec.strip()
    if not cleaned:
        raise ValueError("empty model spec")

    base_cleaned, suffix_effort = normalize_agy_model_and_effort(cleaned, None)
    if base_cleaned != cleaned and suffix_effort is not None:
        return base_cleaned or cleaned, suffix_effort

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
        elif word in MODEL_ALIASES:
            hints.extend(_tokens(MODEL_ALIASES[word]))
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
        matches.sort(key=lambda info: len(_tokens(info.slug)))
        if len(_tokens(matches[0].slug)) == len(_tokens(matches[1].slug)):
            names = ", ".join(info.slug for info in matches)
            raise ValueError(f"{spec!r} is ambiguous between {names}")

    return matches[0].slug, effort


_INTENT_PROFILES: dict[str, Profile] = {
    "default": Profile(name="default", model="gemini-4-argon", effort="high"),
    "test": Profile(name="test", model="gemini-3.8-flash", effort="low"),
}


def profiles() -> dict[str, Profile]:
    built = dict(_INTENT_PROFILES)
    for info in model_catalogue():
        built.setdefault(
            info.slug,
            Profile(name=info.slug, model=info.slug, effort=info.default_effort),
        )
    return built


PROFILES: dict[str, Profile] = profiles()
DEFAULT_PROFILE = "default"


def codex_home_dir() -> Path:
    """Root directory of the calling Codex environment (`$CODEX_HOME` or `~/.codex`)."""
    override = os.environ.get("CODEX_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".codex"


def state_dir() -> Path:
    """Directory holding one JSON file per Codex session."""
    override = os.environ.get("DUO_STATE_DIR")
    if override:
        return Path(override).expanduser()
    return codex_home_dir() / "state" / "duo"


def codex_state_db_path() -> Path:
    """SQLite database where Codex stores threads and spawn edges."""
    override = os.environ.get("DUO_CODEX_STATE_DB")
    if override:
        return Path(override).expanduser()
    return codex_home_dir() / "state_5.sqlite"


def codex_session_index_path() -> Path:
    """JSONL file where Codex Desktop/CLI records thread_name updates."""
    override = os.environ.get("DUO_CODEX_SESSION_INDEX")
    if override:
        return Path(override).expanduser()
    return codex_home_dir() / "session_index.jsonl"


def summaries_db_path() -> Path:
    """Antigravity SQLite database for naming the opponent conversation."""
    override = os.environ.get("DUO_SUMMARIES_DB")
    if override:
        return Path(override).expanduser()
    app_data = os.environ.get("ANTIGRAVITY_APP_DATA_DIR", "").strip()
    if app_data:
        return Path(app_data).expanduser() / "conversation_summaries.db"
    return Path.home() / ".gemini" / "antigravity" / "conversation_summaries.db"


def brain_dir() -> Path:
    """Antigravity brain directory where conversation transcripts live."""
    override = os.environ.get("DUO_BRAIN_DIR")
    if override:
        return Path(override).expanduser()
    app_data = os.environ.get("ANTIGRAVITY_APP_DATA_DIR", "").strip()
    if app_data:
        return Path(app_data).expanduser() / "brain"
    return Path.home() / ".gemini" / "antigravity" / "brain"


def codex_binary() -> Path:
    """Locate the opponent CLI (`agy`, or `DUO_CODEX_BIN` in test suites)."""
    for env_name in ("DUO_AGY_BIN", "DUO_CODEX_BIN"):
        override = os.environ.get(env_name)
        if override:
            candidate = Path(override).expanduser()
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
            raise FileNotFoundError(
                f"{env_name} points at {candidate}, which is not an executable file"
            )

    if BUNDLED_AGY.is_file() and os.access(BUNDLED_AGY, os.X_OK):
        return BUNDLED_AGY

    on_path = shutil.which("agy")
    if on_path:
        return Path(on_path)

    raise FileNotFoundError(
        "Antigravity CLI (agy) not found. Looked at "
        f"{BUNDLED_AGY}, then PATH. Set DUO_AGY_BIN to its location."
    )


agy_binary = codex_binary


def claude_session_id() -> str | None:
    """The durable key tying a Codex thread to a Gemini/Antigravity conversation.

    ``CODEX_THREAD_ID`` is injected into every Codex shell tool call. Falls
    back to ``CLAUDE_CODE_SESSION_ID`` and ``DUO_SESSION_ID`` for test suites
    and manual runs.
    """
    for name in ("CODEX_THREAD_ID", "CLAUDE_CODE_SESSION_ID", "DUO_SESSION_ID"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


codex_session_id = claude_session_id


def _query_codex_thread_chain(thread_id: str) -> list[tuple[str, str, str]]:
    """Walk ``thread_spawn_edges`` from ``thread_id`` to root, returning ``(id, cwd, title)``."""
    db = codex_state_db_path()
    if not db.is_file():
        return []
    chain_ids: list[str] = []
    seen: set[str] = set()
    current = thread_id.strip()
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        raise ConfigError(f"cannot read {db}: {exc}") from None
    out: list[tuple[str, str, str]] = []
    try:
        with conn:
            while current and current not in seen and len(chain_ids) < 16:
                seen.add(current)
                chain_ids.append(current)
                edge = conn.execute(
                    "SELECT parent_thread_id FROM thread_spawn_edges WHERE child_thread_id = ?",
                    (current,),
                ).fetchone()
                if edge is None or not edge[0]:
                    break
                current = str(edge[0]).strip()
            for tid in chain_ids:
                row = conn.execute(
                    "SELECT cwd, title FROM threads WHERE id = ?",
                    (tid,),
                ).fetchone()
                if row is not None:
                    out.append((tid, str(row[0] or "").strip(), str(row[1] or "").strip()))
                else:
                    out.append((tid, "", ""))
    except sqlite3.Error as exc:
        raise ConfigError(f"cannot read {db}: {exc}") from None
    finally:
        conn.close()
    return out


def project_slug(cwd: Path) -> str:
    return str(cwd.resolve()).replace("/", "-").replace(".", "-")


def projects_root() -> Path:
    override = os.environ.get("DUO_PROJECTS_ROOT")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".claude" / "projects"


def read_chat_cwd(session_id: str, cwd_hint: Path) -> tuple[bool, Path | None]:
    """The directory the Codex thread was started in, from ``state_5.sqlite`` or fallback transcript."""
    chain = _query_codex_thread_chain(session_id)
    if chain:
        for _tid, raw_cwd, _title in chain:
            if raw_cwd and Path(raw_cwd).is_absolute():
                return True, Path(raw_cwd)
        return True, None

    path = transcript_path(session_id, cwd_hint)
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return False, None
    except OSError as exc:
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
            if isinstance(value, str) and value and Path(value).is_absolute():
                return True, Path(value)
            return True, None
    return True, None


def host_session_id(session_id: str | None = None) -> str | None:
    """Root parent Codex thread id when called from a spawned subagent, or explicit override."""
    ambient = claude_session_id()
    if session_id is None or session_id == ambient:
        for name in ("CLAUDE_CODE_HOST_SESSION_ID", "DUO_HOST_SESSION_ID"):
            value = os.environ.get(name, "").strip()
            if value:
                return value
    target = (session_id or "").strip() or ambient
    if not target:
        return None
    chain = _query_codex_thread_chain(target)
    if len(chain) > 1:
        root_id = chain[-1][0]
        if root_id and root_id != target:
            return root_id
    return None


def transcript_path(session_id: str, cwd: Path) -> Path:
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


_MY_REQUEST_RE = re.compile(r"##\s*My request:\s*(.+)", re.DOTALL)


def _clean_codex_title(raw: str) -> str | None:
    text = raw.strip()
    if not text:
        return None
    match = _MY_REQUEST_RE.search(text)
    if match:
        text = match.group(1).strip()
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if not first_line:
        return None
    return first_line[:120]


def _read_session_index_title(ordered_ids: list[str]) -> str | None:
    idx_path = codex_session_index_path()
    if not idx_path.is_file() or not ordered_ids:
        return None
    target_set = set(ordered_ids)
    by_id: dict[str, str] = {}
    try:
        with idx_path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not any(tid in line for tid in target_set):
                    continue
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(record, dict):
                    continue
                tid = record.get("id")
                if isinstance(tid, str) and tid in target_set:
                    name = record.get("thread_name")
                    if isinstance(name, str) and name.strip():
                        by_id[tid] = name.strip()
    except OSError:
        return None
    for tid in ordered_ids:
        if tid in by_id:
            return by_id[tid]
    return None


def read_chat_title(session_id: str, cwd: Path) -> str | None:
    """Best-effort human-readable name of the current Codex thread (root first)."""
    chain = _query_codex_thread_chain(session_id)
    if chain:
        for tid, _raw_cwd, raw_title in reversed(chain):
            indexed = _read_session_index_title([tid])
            if indexed:
                return indexed
            cleaned = _clean_codex_title(raw_title)
            if cleaned:
                return cleaned
    else:
        indexed = _read_session_index_title([session_id])
        if indexed:
            return indexed

    path = transcript_path(session_id, cwd)
    custom: str | None = None
    generated: str | None = None

    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
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
