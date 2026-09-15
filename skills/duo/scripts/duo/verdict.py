#!/usr/bin/env python3
"""The structured answer each round must produce, and the rule that ends a loop.

Two decisions are encoded here, both taken from what the surveyed
implementations actually do rather than from what reads well:

* A round ends with a machine-readable verdict, not prose to be interpreted.
  ``turn/start`` accepts an ``outputSchema`` that constrains the final
  assistant message, so this costs nothing.
* Agreement has to hold for two consecutive rounds before the loop exits early.
  One round of "looks good" is cheap to produce and means very little; three
  independent implementations converged on the same two-round rule.

Deliberately absent: any notion that agreement proves correctness. Unresolved
disagreement is reported to the human rather than ground down into a false
consensus, and even a clean convergence is a recommendation, not a licence to
execute.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any

from config import MAX_ROUNDS, STOP_HOLD_ROUNDS

# Severities that block. p2/p3 are recorded and shown but never hold the loop.
BLOCKING = ("p0", "p1")

# A finding is unresolved while it is newly raised or actively disputed.
UNRESOLVED = ("new", "dispute")

# Structured-output schemas are validated strictly: every property has to be
# listed in `required`, objects have to set `additionalProperties: false`, and
# numeric bounds are best avoided. Confidence is an enum for that reason — and
# because three honest buckets beat a fabricated 0.87.
VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["stance", "stop", "confidence", "summary", "findings", "open_questions"],
    "properties": {
        "stance": {
            "type": "string",
            "enum": ["agree", "disagree", "needs_info"],
            "description": (
                "agree — the proposal is sound as it stands. "
                "disagree — at least one objection remains. "
                "needs_info — cannot judge until the open questions are answered."
            ),
        },
        "stop": {
            "type": "boolean",
            "description": "True only when there is nothing further worth discussing.",
        },
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "summary": {
            "type": "string",
            "description": "Two or three sentences a human can act on.",
        },
        "findings": {
            "type": "array",
            "description": "One entry per objection. Carry ids across rounds.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "severity", "claim", "evidence", "response", "rationale"],
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Stable across rounds, e.g. f1, f2.",
                    },
                    "severity": {"type": "string", "enum": ["p0", "p1", "p2", "p3"]},
                    "claim": {"type": "string"},
                    "evidence": {
                        "type": "string",
                        "description": "What supports the claim. No evidence, no finding.",
                    },
                    "response": {
                        "type": "string",
                        "enum": ["new", "confirm", "dispute", "retract"],
                        "description": (
                            "new — raised this round. confirm — the other side was right. "
                            "dispute — rejected, with rationale. retract — withdrawn."
                        ),
                    },
                    "rationale": {
                        "type": "string",
                        "description": "Required for dispute; a bare disagreement does not count.",
                    },
                },
            },
        },
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
}

# Outcomes, in the order they are checked.
CONVERGED = "converged"
# Agreement reached on the final round but never held for two. Distinct from
# both `converged` and `max_rounds`: calling it convergence would overclaim,
# calling it "no clean stop" would be plainly false when the last verdict was
# a clean agree with nothing blocking.
AGREED_UNHELD = "agreed_unheld"
STABLE_DISAGREEMENT = "stable_disagreement"
NEEDS_INFO = "needs_info"
MAX_ROUNDS_REACHED = "max_rounds"
UNPARSEABLE = "unparseable"

# Two answer formats, chosen per run:
#
# `trailer` — prose for the human, then one fenced ```json block carrying the
#   verdict. This is what a person sees in Codex Desktop, so it has to read
#   like an answer, not a payload. Default.
# `json`    — the whole final message is the verdict, enforced by
#   `outputSchema`. Strictest parsing, ugliest thread. For probing and for
#   models that will not hold a trailer.
FORMAT_TRAILER = "trailer"
FORMAT_JSON = "json"
VERDICT_FORMATS = (FORMAT_TRAILER, FORMAT_JSON)

# Instruction appended to the role prompt in trailer mode. Written as a
# contract the model can check itself against, not as a plea.
TRAILER_CONTRACT = """\
Формат ответа — два блока, строго в этом порядке.

1. Ответ человеку: обычная проза. Что не так, почему, чем доказано. Это то,
   что читают в окне Codex.
2. Последней строкой ответа — один блок ```json с вердиктом по схеме ниже.
   Без текста после него. Поля: stance (agree|disagree|needs_info),
   stop (boolean), confidence (low|medium|high), summary (строка),
   findings (массив объектов id, severity строго p0..p3 — значение вне шкалы
   считается блокирующим, claim, evidence,
   response new|confirm|dispute|retract, rationale), open_questions (массив
   строк). Все поля обязательны, лишних нет.

Смена позиции по прежнему id — `confirm` после `dispute`, `retract` после
`new` — требует `rationale`, называющего, что именно изменилось. «Согласен с
доводами» основанием не считается.

Блок с вердиктом обязан быть последним в ответе и стоять на верхнем уровне: не
внутри списка, не в цитате, без отступа. После его закрывающей строки допустимы
только пробелы и переводы строки. Примеры кода выше по тексту — на
здоровье, в том числе с меткой языка. Ответ, где после вердикта что-то ещё
есть, считается не отвеченным: иначе строка «а вот это возражение остаётся»
после блока потерялась бы вместе с возражением.

Ответ без блока ```json в конце считается не отвеченным.
"""

# Fences are found by walking lines, not by one regular expression. Every
# attempt at the latter lost a real answer: three backticks inside a JSON
# string, a `python` example before the verdict, CRLF endings, two blocks in a
# row. This pair of patterns only classifies a single line.
# The info string is whatever the writer put there — `c#`, `objective-c++`,
# `text {highlight}`. Restricting it made a fence with an unusual label
# invisible as an opening, and its closing line looked like an opening
# instead. Whether a block is the verdict is decided later, by its label.
# Up to three spaces of indent still opens a fence; four make it an indented
# code block, where the backticks are literal text — that is the difference
# between an example of a verdict and the verdict.
# One greedy capture for the info string, stripped afterwards. The earlier
# spelling had three overlapping quantifiers, and a line of backticks and
# spaces made the match cost grow eightfold with every doubling of its length.
_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,})([^`]*)$")
_FENCE_CLOSE = re.compile(r"^ {0,3}(`{3,})[ \t]*$")


@dataclass
class Finding:
    id: str
    severity: str
    claim: str
    evidence: str
    response: str
    rationale: str = ""

    @property
    def is_blocking(self) -> bool:
        return self.severity in BLOCKING

    @property
    def is_unresolved(self) -> bool:
        return self.response in UNRESOLVED

    @property
    def holds_the_loop(self) -> bool:
        """A blocking objection that nobody has settled."""
        return self.is_blocking and self.is_unresolved


@dataclass
class Verdict:
    stance: str = "needs_info"
    stop: bool = False
    confidence: str = "low"
    summary: str = ""
    findings: list[Finding] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    raw_text: str = ""
    # The human-facing part of a trailer-format answer; empty in json format.
    prose: str = ""
    parse_error: str | None = None

    @property
    def parsed(self) -> bool:
        return self.parse_error is None

    @property
    def blocking_findings(self) -> list[Finding]:
        return [f for f in self.findings if f.holds_the_loop]

    @property
    def wants_stop(self) -> bool:
        """The full early-exit condition for a single round.

        All three parts matter: an unparseable answer never counts, and a
        `stop: true` sitting next to an open p0 is a contradiction the loop
        resolves against stopping.
        """
        return (
            self.parsed
            and self.stop
            and self.stance == "agree"
            and not self.blocking_findings
        )

    def unresolved_ids(self) -> frozenset[str]:
        return frozenset(f.id for f in self.blocking_findings)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build the object, refusing a key that appears twice.

    `json.loads` keeps the last value and says nothing, so a verdict listing a
    p1 in `findings` and then repeating `findings: []` decodes as agreement —
    before any structural check can see the contradiction. Both spellings are
    valid JSON; neither is a verdict anyone can act on.
    """
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"the key {key!r} appears twice")
        seen[key] = value
    return seen


def _encodable(value: str) -> UnicodeEncodeError | None:
    """The reason a string cannot be written as UTF-8, or None."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        return exc
    return None


def _check_strings(node: Any) -> None:
    """Walk the whole value without recursion, refusing an unencodable string.

    `json` decodes a lone escape for U+D800 into an unpaired surrogate: a valid
    `str` that cannot be encoded, which survives parsing and then breaks the
    save or the print — after the turn was accepted. A real surrogate pair
    decodes to one character and is fine.

    The walk is iterative, because a recursive one blew the stack one level
    past the decoder. And the path used in the error message is built only
    when there is an error: carrying a ready-made path on every stack entry
    cost memory proportional to key length times array width, 64 MiB for a
    37 KB input.
    """
    # Each entry: (value, parent entry, this hop's label). The label is short
    # on purpose — a key is cut for the message, never for the check.
    root: tuple[Any, Any, str] = (node, None, "the verdict")
    stack: list[tuple[Any, Any, str]] = [root]

    def path(entry: tuple[Any, Any, str]) -> str:
        hops: list[str] = []
        while entry is not None:
            hops.append(entry[2])
            entry = entry[1]
        return "".join(reversed(hops))

    while stack:
        entry = stack.pop()
        current = entry[0]
        if isinstance(current, str):
            bad = _encodable(current)
            if bad is not None:
                raise ValueError(f"{path(entry)} contains a character that cannot be written: {bad}")
        elif isinstance(current, dict):
            for key, value in current.items():
                text = str(key)
                bad = _encodable(text)
                if bad is not None:
                    raise ValueError(f"{path(entry)}: a key contains a character that cannot be written: {bad}")
                stack.append((value, entry, "." + text[:60]))
        elif isinstance(current, list):
            for index, item in enumerate(current):
                stack.append((item, entry, f"[{index}]"))


def _loads(text: str) -> Any:
    """Decode a verdict, refusing anything that cannot be acted on.

    Duplicate keys at any depth, strings that cannot be encoded, and nesting
    deep enough to exhaust the stack all become one thing: a `ValueError` the
    caller turns into a parse error. A `RecursionError` escaping from here
    used to kill the process with the turn still pending, which no retry could
    clear.
    """
    try:
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except RecursionError:
        raise ValueError("the verdict is nested too deeply to read") from None
    _check_strings(payload)
    return payload


def split_trailer(text: str) -> tuple[str, str | None]:
    """Separate the prose from the trailing fenced JSON block.

    Returns ``(prose, json_text)``. The *last* fenced block wins, because the
    prose may legitimately quote JSON earlier; when the whole message is one
    fence there is no prose. ``json_text`` is ``None`` when no fence exists.
    """
    stripped = text.strip()
    try:
        _loads(stripped)
    except ValueError:
        pass
    else:
        # The whole message is the verdict: the `json` format, or a model that
        # answered with the object and nothing else. Decided by parsing it,
        # not by looking at the first character — an answer can open with an
        # example object and go on to the real trailer.
        return "", stripped

    lines = text.splitlines(keepends=True)
    blocks: list[tuple[int, int, str, str]] = []
    opened_at: int | None = None
    ticks = ""
    info = ""
    body: list[str] = []
    for index, line in enumerate(lines):
        # Only the line ending is stripped: the left margin decides whether
        # these backticks are a fence at all.
        bare = line.rstrip("\r\n")
        if opened_at is None:
            match = _FENCE_OPEN.match(bare)
            if match:
                ticks, info = match.group(1), match.group(2).strip().lower()
                opened_at, body = index, []
            continue
        close = _FENCE_CLOSE.match(bare)
        if close and len(close.group(1)) >= len(ticks):
            blocks.append((opened_at, index, info, "".join(body)))
            opened_at = None
            continue
        body.append(line)

    # A `python` or `diff` example is a block like any other; only an unmarked
    # or json one can be the verdict, and of those the *last* one is it.
    # Preferring a labelled block over an unmarked one — which is what I tried
    # next — loses an unmarked verdict that follows a `json` example.
    candidates = [b for b in blocks if b[2] in ("", "json")]
    if not candidates:
        return stripped, None
    opened, closed, _info, content = candidates[-1]
    if "".join(lines[closed + 1:]).strip():
        # Text after the block means this is an example inside the answer, not
        # the verdict that closes it. Reading it as the verdict throws away
        # everything said afterwards — an open objection included.
        return stripped, None
    prose = "".join(lines[:opened]).rstrip()
    return prose, content.strip()


def parse(text: str | None) -> Verdict:
    """Turn the model's final message into a verdict, never raising.

    Accepts both formats: a message that *is* JSON, and prose ending in a
    fenced JSON block. A malformed answer is a normal outcome, not an
    exception: the loop keeps the raw text, tells the human, and declines to
    treat it as agreement.
    """
    if text is None or not text.strip():
        return Verdict(raw_text="", parse_error="the turn produced no final message")

    raw = text.strip()
    # The original text, not the stripped one: `strip()` eats the first line's
    # indent, and the indent is what separates a fence from a literal example
    # of one.
    prose, candidate = split_trailer(text)

    payload: Any = None
    if candidate is not None:
        try:
            payload = _loads(candidate)
        except (json.JSONDecodeError, ValueError) as exc:
            return Verdict(
                raw_text=raw,
                prose=prose,
                parse_error=f"the ```json block is not a usable verdict: {exc}",
            )
    if payload is None:
        # No usable fence: the message might still be bare JSON. Strict here
        # too — this path decodes a verdict like any other.
        try:
            payload = _loads(raw)
            prose = ""
        except (json.JSONDecodeError, ValueError) as exc:
            if candidate is not None:
                reason = f"the ```json block is not valid JSON: {exc}"
            elif raw.startswith("{"):
                # The message is meant to be the verdict itself, so say what
                # is wrong with it rather than complaining about a missing
                # fence that was never expected.
                reason = f"the verdict is not usable: {exc}"
            else:
                reason = "final message has no ```json verdict block"
            return Verdict(raw_text=raw, prose=prose, parse_error=reason)

    if not isinstance(payload, dict):
        return Verdict(raw_text=raw, prose=prose, parse_error="verdict is not a JSON object")

    verdict = Verdict(raw_text=raw, prose=prose)

    # Structure is checked before anything is normalised. A field of the wrong
    # shape is a verdict nobody can act on, and the careful reading of it is
    # "unparseable", not "agreed": `bool("false")` is True, and a `findings`
    # object silently dropped would turn a round full of p1 into a clean stop.
    # Two classes of field, and the difference is what a missing one can hide.
    # `stance`, `stop` and `findings` carry the decision: absent or null, they
    # would read as "nothing to object to", which is the one reading that must
    # never be guessed. The rest is prose — a verdict that omits `summary` is
    # still a verdict, and measured against every real answer in this thread,
    # Codex omits it. Type is checked for all of them.
    for name, kinds, label, required in (
        ("stance", (str,), "a string", True),
        ("stop", (bool,), "a boolean", True),
        ("findings", (list,), "an array", True),
        ("confidence", (str,), "a string", False),
        ("summary", (str,), "a string", False),
        ("open_questions", (list,), "an array", False),
    ):
        if name not in payload or payload[name] is None:
            if required:
                return Verdict(
                    raw_text=raw, prose=prose, parse_error=f"the verdict has no {name}"
                )
            continue
        value = payload[name]
        if not isinstance(value, kinds) or (isinstance(value, bool) and kinds != (bool,)):
            return Verdict(
                raw_text=raw,
                prose=prose,
                parse_error=f"{name} is {type(value).__name__}, not {label}",
            )

    stance = payload.get("stance")
    verdict.stance = stance if stance in ("agree", "disagree", "needs_info") else "needs_info"

    verdict.stop = payload.get("stop") is True

    confidence = payload.get("confidence")
    verdict.confidence = confidence if confidence in ("low", "medium", "high") else "low"

    summary = payload.get("summary")
    verdict.summary = summary.strip() if isinstance(summary, str) else ""

    questions = payload.get("open_questions")
    if isinstance(questions, list):
        verdict.open_questions = [str(q).strip() for q in questions if str(q).strip()]

    findings = payload.get("findings")
    if isinstance(findings, list):
        for index, item in enumerate(findings, start=1):
            if not isinstance(item, dict):
                return Verdict(
                    raw_text=raw,
                    prose=prose,
                    parse_error=f"finding {index} is {type(item).__name__}, not an object",
                )
            if "id" in item and not isinstance(item["id"], str):
                # A number here collides after `str()`: 1e309 and 1e310 both
                # spell "inf", and two objections merge into one id.
                return Verdict(
                    raw_text=raw,
                    prose=prose,
                    parse_error=f"finding {index} has a non-string id",
                )
            severity = item.get("severity")
            response = item.get("response")
            verdict.findings.append(
                Finding(
                    # A severity outside the scale is treated as blocking, not
                    # as the middle of it. "critical" downgraded to p2 would
                    # let the loop stop on the objection that mattered most.
                    id=item["id"] if isinstance(item.get("id"), str) and item["id"] else f"f{index}",
                    severity=severity if severity in ("p0", "p1", "p2", "p3") else "p1",
                    claim=str(item.get("claim") or "").strip(),
                    evidence=str(item.get("evidence") or "").strip(),
                    response=response if response in ("new", "confirm", "dispute", "retract") else "new",
                    rationale=str(item.get("rationale") or "").strip(),
                )
            )

    if not verdict.summary and not verdict.findings and not verdict.prose:
        verdict.parse_error = "verdict has neither a summary nor any findings"

    return verdict


@dataclass
class LoopDecision:
    """What the loop should do after a round, and why."""

    finished: bool
    outcome: str | None
    stop_streak: int
    reason: str


def decide(
    verdict: Verdict,
    *,
    round_number: int,
    stop_streak: int,
    previous_unresolved: frozenset[str] | None,
    max_rounds: int = MAX_ROUNDS,
    stop_hold: int = STOP_HOLD_ROUNDS,
) -> LoopDecision:
    """Apply the stopping rule to one round.

    ``round_number`` is 1-based. ``stop_streak`` is how many consecutive
    previous rounds already wanted to stop. ``previous_unresolved`` is the
    blocking-finding id set from the round before, used to spot a loop that is
    restating itself instead of progressing.
    """
    if not verdict.parsed:
        # An unreadable answer is worth one more round; two in a row is a
        # pattern, not an accident.
        if round_number >= max_rounds:
            return LoopDecision(True, UNPARSEABLE, 0, str(verdict.parse_error))
        return LoopDecision(False, None, 0, f"unreadable verdict: {verdict.parse_error}")

    streak = stop_streak + 1 if verdict.wants_stop else 0

    if streak >= stop_hold:
        return LoopDecision(
            True, CONVERGED, streak, f"agreement held {streak} rounds running"
        )

    current = verdict.unresolved_ids()

    # No-progress guard: the same blocking objections restated, with neither
    # side moving. Continuing costs quota and produces the same round again.
    if (
        previous_unresolved is not None
        and current
        and current == previous_unresolved
        and round_number >= 2
    ):
        return LoopDecision(
            True,
            STABLE_DISAGREEMENT,
            streak,
            "the same blocking objections came back unchanged",
        )

    if round_number >= max_rounds:
        if verdict.wants_stop:
            return LoopDecision(
                True,
                AGREED_UNHELD,
                streak,
                f"agreed on the last round, held {streak} of {stop_hold}",
            )
        if current:
            return LoopDecision(
                True, STABLE_DISAGREEMENT, streak, "round limit reached with objections open"
            )
        if verdict.stance == "needs_info":
            return LoopDecision(
                True, NEEDS_INFO, streak, "round limit reached, still waiting on answers"
            )
        return LoopDecision(
            True, MAX_ROUNDS_REACHED, streak, "round limit reached without a clean stop"
        )

    if verdict.wants_stop:
        return LoopDecision(
            False, None, streak, f"agreement round {streak} of {stop_hold}"
        )
    return LoopDecision(False, None, streak, "objections remain")


def outcome_headline(outcome: str | None) -> str:
    """One line a human reads first."""
    return {
        CONVERGED: "Сошлись. Это рекомендация, а не разрешение выполнять.",
        AGREED_UNHELD: (
            "Согласие на последнем раунде, но оно не продержалось два раунда — "
            "смотри сам, стоит ли добавить раунд."
        ),
        STABLE_DISAGREEMENT: "Расхождение зафиксировано — решать тебе.",
        NEEDS_INFO: "Codex не хватает данных, смотри открытые вопросы.",
        MAX_ROUNDS_REACHED: "Раунды кончились без чистого согласия.",
        UNPARSEABLE: "Codex не вернул разбираемый вердикт.",
    }.get(outcome or "", "Раунд не завершён.")
