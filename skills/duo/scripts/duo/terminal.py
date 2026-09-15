"""The boundary between what Codex said and what the terminal is allowed to do.

Codex's answer is assembled from whatever it read in the repository, so a
line of it can carry ``ESC[8m`` — conceal — and every objection printed after
it vanishes from the screen while staying in the transcript. Everything a
human reads goes through `out` or `say`; JSON output and the stored state do
not, because they carry the answer verbatim by contract.
"""

from __future__ import annotations

import re
import sys

# C0 without LF and TAB, then DEL and C1. LF and TAB are layout; the rest is
# either a terminal command or noise, and both are spelled out as `\xNN`.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def visible(text: object) -> str:
    """Spell control characters out so they are seen rather than executed."""
    return _CONTROL.sub(lambda m: f"\\x{ord(m.group(0)):02x}", str(text))


def out(text: object = "", *, end: str = "\n") -> None:
    """Print one line of human-facing output, sanitised whole."""
    sys.stdout.write(visible(text) + end)


def say(text: object) -> None:
    """One `duo:` diagnostic line on stderr, sanitised whole."""
    sys.stderr.write("duo: " + visible(text) + "\n")
    sys.stderr.flush()
