#!/usr/bin/env python3
"""Every knob ``thread/start`` and ``turn/start`` accept, in one typed place.

The point of exposing all of them is that the loop can be tuned from the
command line instead of by editing code — a different model, a looser sandbox,
a different history contract — which is what makes an evening of experiments
possible once quota is back.

Two rules come straight from the schema and are enforced here rather than
discovered as a server error:

* ``permissions`` names a profile and **cannot be combined with** ``sandbox``;
* every path in ``runtimeWorkspaceRoots`` must be absolute.

Values are validated against enums where the schema has one. ``effort`` and
``model`` are free strings in the protocol, so they are checked against the
installed model catalogue and *warned* about rather than rejected: the
catalogue can be older than the service it describes, and a hard failure on a
model that actually works would be worse than a note.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

# Enum values taken verbatim from the generated schema of codex-cli 0.152.1.
SANDBOX_MODES = ("read-only", "workspace-write", "danger-full-access")
APPROVAL_POLICIES = ("untrusted", "on-request", "never")
PERSONALITIES = ("none", "friendly", "pragmatic")
APPROVALS_REVIEWERS = ("user", "auto_review", "guardian_subagent")
HISTORY_MODES = ("legacy", "paginated")
REASONING_SUMMARIES = ("auto", "none")
SESSION_START_SOURCES = ("startup", "clear")

# `serviceTier` is a free string; these are the two that matter. "default" is
# documented as standard speed, "priority" is the catalogue default and is
# described as 1.5x speed with increased usage.
SERVICE_TIERS = ("default", "priority")


class OptionError(ValueError):
    """A combination the schema forbids, caught before the round starts."""


def parse_config_override(raw: str) -> tuple[list[str], Any]:
    """Split ``a.b.c=value`` the way the CLI's ``-c`` does.

    The value is parsed as JSON when it can be, so ``-c tools.web_search=true``
    yields a boolean and ``-c model_reasoning_effort=high`` yields the string.
    """
    if "=" not in raw:
        raise OptionError(f"config override {raw!r} is not key=value")
    key, _, value = raw.partition("=")
    key = key.strip()
    if not key:
        raise OptionError(f"config override {raw!r} has an empty key")
    try:
        parsed: Any = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        parsed = value
    return key.split("."), parsed


def nest_overrides(pairs: list[str]) -> dict[str, Any]:
    """Turn ``["a.b=1", "a.c=2"]`` into ``{"a": {"b": 1, "c": 2}}``."""
    root: dict[str, Any] = {}
    for raw in pairs:
        path, value = parse_config_override(raw)
        cursor = root
        for segment in path[:-1]:
            existing = cursor.get(segment)
            if not isinstance(existing, dict):
                existing = {}
                cursor[segment] = existing
            cursor = existing
        cursor[path[-1]] = value
    return root


def _check(name: str, value: str | None, allowed: tuple[str, ...]) -> str | None:
    if value is None:
        return None
    if value not in allowed:
        raise OptionError(f"{name}={value!r} is not one of {', '.join(allowed)}")
    return value


@dataclass
class ThreadOptions:
    """What a thread is created with. Everything optional is omitted when unset."""

    cwd: Path
    model: str | None = None
    model_provider: str | None = None
    sandbox: str | None = None
    approval_policy: str | None = None
    permissions: str | None = None
    approvals_reviewer: str | None = None
    personality: str | None = None
    history_mode: str | None = None
    thread_source: str | None = None
    session_start_source: str | None = None
    project_id: str | None = None
    service_tier: str | None = None
    service_name: str | None = None
    base_instructions: str | None = None
    developer_instructions: str | None = None
    runtime_workspace_roots: list[Path] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)
    allow_provider_model_fallback: bool = False
    # Exposed so the "temporary chats nobody can see" behaviour can be
    # reproduced deliberately. The loop never sets it.
    ephemeral: bool = False

    def validate(self) -> None:
        _check("sandbox", self.sandbox, SANDBOX_MODES)
        _check("approval-policy", self.approval_policy, APPROVAL_POLICIES)
        _check("approvals-reviewer", self.approvals_reviewer, APPROVALS_REVIEWERS)
        _check("personality", self.personality, PERSONALITIES)
        _check("history-mode", self.history_mode, HISTORY_MODES)
        _check("session-start-source", self.session_start_source, SESSION_START_SOURCES)

        if self.permissions and self.sandbox:
            raise OptionError(
                "permissions names a profile and cannot be combined with sandbox; "
                "pass one or the other"
            )
        for root in self.runtime_workspace_roots:
            if not root.is_absolute():
                raise OptionError(f"--add-dir {root} must be an absolute path")

    def to_params(self) -> dict[str, Any]:
        self.validate()
        params: dict[str, Any] = {
            "cwd": str(self.cwd),
            # Never true unless asked for: an ephemeral thread lives in memory
            # and reaches neither a listing nor the sidebar.
            "ephemeral": self.ephemeral,
        }
        optional = {
            "model": self.model,
            "modelProvider": self.model_provider,
            "sandbox": self.sandbox,
            "approvalPolicy": self.approval_policy,
            "permissions": self.permissions,
            "approvalsReviewer": self.approvals_reviewer,
            "personality": self.personality,
            "historyMode": self.history_mode,
            "threadSource": self.thread_source,
            "sessionStartSource": self.session_start_source,
            "projectId": self.project_id,
            "serviceTier": self.service_tier,
            "serviceName": self.service_name,
            "baseInstructions": self.base_instructions,
            "developerInstructions": self.developer_instructions,
        }
        params.update({k: v for k, v in optional.items() if v is not None})

        if self.runtime_workspace_roots:
            params["runtimeWorkspaceRoots"] = [str(p) for p in self.runtime_workspace_roots]
        if self.config:
            params["config"] = self.config
        if self.allow_provider_model_fallback:
            params["allowProviderModelFallback"] = True
        return params


@dataclass
class TurnOptions:
    """What a single turn is run with. Overrides stick to later turns."""

    model: str | None = None
    effort: str | None = None
    service_tier: str | None = None
    service_tier_for_turn: str | None = None
    personality: str | None = None
    summary: str | None = None
    sandbox: str | None = None
    approval_policy: str | None = None
    permissions: str | None = None
    approvals_reviewer: str | None = None
    turn_trigger: str | None = None
    # Caller-stable id, echoed back by history as `userMessage.clientId`. This
    # is how a retry after a lost result finds the turn it already started.
    client_user_message_id: str | None = None
    cwd: Path | None = None
    runtime_workspace_roots: list[Path] = field(default_factory=list)
    additional_context: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] | None = None

    def validate(self) -> None:
        _check("sandbox", self.sandbox, SANDBOX_MODES)
        _check("approval-policy", self.approval_policy, APPROVAL_POLICIES)
        _check("approvals-reviewer", self.approvals_reviewer, APPROVALS_REVIEWERS)
        _check("personality", self.personality, PERSONALITIES)
        _check("summary", self.summary, REASONING_SUMMARIES)

        if self.permissions and self.sandbox:
            raise OptionError(
                "permissions names a profile and cannot be combined with sandbox; "
                "pass one or the other"
            )
        for root in self.runtime_workspace_roots:
            if not root.is_absolute():
                raise OptionError(f"--add-dir {root} must be an absolute path")

    def to_params(self, thread_id: str, text: str) -> dict[str, Any]:
        """Build ``turn/start`` params. ``input`` and ``threadId`` are required."""
        self.validate()
        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": text, "text_elements": []}],
        }
        optional = {
            "model": self.model,
            "effort": self.effort,
            "serviceTier": self.service_tier,
            # Per-turn speed override; "default" is documented as standard.
            "serviceTierForTurn": self.service_tier_for_turn,
            "personality": self.personality,
            "summary": self.summary,
            # Note the name: on a turn the field is `sandboxPolicy`, not
            # `sandbox` as it is on a thread.
            "sandboxPolicy": self.sandbox,
            "approvalPolicy": self.approval_policy,
            "permissions": self.permissions,
            "approvalsReviewer": self.approvals_reviewer,
            "turnTrigger": self.turn_trigger,
            "clientUserMessageId": self.client_user_message_id,
        }
        params.update({k: v for k, v in optional.items() if v is not None})

        if self.cwd is not None:
            params["cwd"] = str(self.cwd)
        if self.runtime_workspace_roots:
            params["runtimeWorkspaceRoots"] = [str(p) for p in self.runtime_workspace_roots]
        if self.additional_context:
            params["additionalContext"] = self.additional_context
        if self.output_schema is not None:
            params["outputSchema"] = self.output_schema
        return params
