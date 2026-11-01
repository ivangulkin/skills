#!/usr/bin/env python3
"""The ``thread/start`` and ``turn/start`` fields duo sets, in one typed place.

The skills pass a model, an effort, a speed and a context window, and at most
``--sandbox read-only``. That is the whole surface: every other protocol knob
used to be a flag, and none was ever passed.

``effort`` and ``model`` are free strings in the protocol, so they are checked
against the installed model catalogue and *warned* about rather than rejected:
the catalogue can be older than the service it describes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Enum values taken verbatim from the generated schema of codex-cli 0.152.1.
SANDBOX_MODES = ("read-only", "workspace-write", "danger-full-access")


@dataclass
class ThreadOptions:
    """What a thread is created with. Everything optional is omitted when unset."""

    cwd: Path
    model: str | None = None
    # Unset means whatever the user's Codex config says. Codex does not restore
    # an unnamed sandbox on resume, so the state keeps it and every resume and
    # fork sends it again.
    sandbox: str | None = None
    developer_instructions: str | None = None
    config: dict[str, Any] = field(default_factory=dict)

    def to_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "cwd": str(self.cwd),
            # An ephemeral thread lives in memory and reaches neither a
            # listing nor the sidebar.
            "ephemeral": False,
        }
        optional = {
            "model": self.model,
            "sandbox": self.sandbox,
            "developerInstructions": self.developer_instructions,
        }
        params.update({k: v for k, v in optional.items() if v is not None})
        if self.config:
            params["config"] = self.config
        return params


@dataclass
class TurnOptions:
    """What a single turn is run with. Overrides stick to later turns."""

    model: str | None = None
    effort: str | None = None
    service_tier_for_turn: str | None = None
    # Caller-stable id, echoed back by history as `userMessage.clientId`. This
    # is how a retry after a lost result finds the turn it already started.
    client_user_message_id: str | None = None
    additional_context: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] | None = None

    def to_params(self, thread_id: str, text: str) -> dict[str, Any]:
        """Build ``turn/start`` params. ``input`` and ``threadId`` are required."""
        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": text, "text_elements": []}],
        }
        optional = {
            "model": self.model,
            "effort": self.effort,
            # Per-turn speed override; "default" is documented as standard.
            "serviceTierForTurn": self.service_tier_for_turn,
            "clientUserMessageId": self.client_user_message_id,
        }
        params.update({k: v for k, v in optional.items() if v is not None})
        if self.additional_context:
            params["additionalContext"] = self.additional_context
        if self.output_schema is not None:
            params["outputSchema"] = self.output_schema
        return params
