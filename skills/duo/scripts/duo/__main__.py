#!/usr/bin/env python3
"""Command line for the Duo consultation loop.

Run it as a directory, which makes Python execute this file with the package
directory on ``sys.path``::

    python3 <skill>/scripts/duo verify
    python3 <skill>/scripts/duo ask --phase design --message-file q.md

One invocation runs **one** round. The loop over rounds belongs to the skill,
because the other participant in the debate is Claude: the script cannot write
the next message, it can only carry it and say whether the loop is finished.

Every round has a caller-supplied ``--request-id``. That id is what makes a
retry after a lost result *replay* the stored answer instead of starting a
second turn, and what makes a request that is still running resumable. A new
round needs the previous one acknowledged: ``--ack-request-id <old>``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any

from appserver import AppServerCrashed, AppServerError, TurnTimeout
from config import (
    ConfigError,
    MAX_MESSAGE_CHARS,
    MAX_ROUNDS,
    PROFILES,
    SERVICE_TIER_STANDARD,
    STOP_HOLD_ROUNDS,
    TURN_HARD_TIMEOUT_SEC,
    TURN_IDLE_TIMEOUT_SEC,
    VERIFIED_CODEX_VERSIONS,
    claude_session_id,
    codex_binary,
    effort_warning,
    host_session_id,
    model_catalogue,
    read_chat_title,
    resolve_model_spec,
    state_dir,
    transcript_path,
)
import consult
from consult import ConsultError, OpenRequestConflict, PHASES, RecoveryWaitExhausted, crash_point, resolve_profile
from options import (
    APPROVAL_POLICIES,
    APPROVALS_REVIEWERS,
    HISTORY_MODES,
    OptionError,
    PERSONALITIES,
    REASONING_SUMMARIES,
    SANDBOX_MODES,
    SESSION_START_SOURCES,
    ThreadOptions,
    TurnOptions,
    nest_overrides,
)
import state as st
from terminal import out, say, visible
from state import StateBlocked, StateCorrupt, StateError, StateLocked, StateUnreadable
from verdict import (
    FORMAT_TRAILER,
    VERDICT_FORMATS,
    VERDICT_SCHEMA,
    Verdict,
    outcome_headline,
)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except OpenRequestConflict as exc:
        _print_conflict(exc, as_json=bool(getattr(args, "json", False)))
        return 7
    except RecoveryWaitExhausted as exc:
        say(exc)
        return 10
    except StateLocked as exc:
        say(exc)
        return 8
    except (StateBlocked, StateCorrupt, StateUnreadable) as exc:
        say(exc)
        return 9
    except (ConsultError, OptionError, StateError, ConfigError) as exc:
        say(exc)
        return 2
    except AppServerError as exc:
        say(exc)
        return 3
    except TurnTimeout as exc:
        say(
            f"{exc} (the turn was interrupted). The message is already in "
            "the thread — do not resend it; rerun with the same --request-id to "
            "resume, or send a short continuation as a new request"
        )
        return 4
    except AppServerCrashed as exc:
        say(exc)
        return 5
    except FileNotFoundError as exc:
        say(exc)
        return 6
    except KeyboardInterrupt:
        say(f"interrupted")
        return 130


# -- parser ----------------------------------------------------------------


def _positive(raw: str) -> int:
    """A count that must be at least one.

    `--stop-hold 0` used to mean "converged before the first answer": the
    streak comparison is `>=`, and zero satisfies it immediately.
    """
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{raw!r} is not a number") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"{value} is less than 1")
    return value


def _add_thread_flags(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group(
        "thread (applied when the thread is created, ignored on later rounds)"
    )
    group.add_argument("--cwd", type=Path, help="working root for the thread; default: the Claude chat's own directory, else $PWD")
    group.add_argument("--model-provider", help="provider id, e.g. openai or a proxy")
    group.add_argument("--sandbox", choices=SANDBOX_MODES, help="default: whatever your Codex config says")
    group.add_argument("--approval-policy", choices=APPROVAL_POLICIES, help="default never")
    group.add_argument(
        "--permissions",
        help="named permission profile; the schema forbids combining it with --sandbox",
    )
    group.add_argument("--approvals-reviewer", choices=APPROVALS_REVIEWERS)
    group.add_argument("--personality", choices=PERSONALITIES)
    group.add_argument("--history-mode", choices=HISTORY_MODES)
    group.add_argument("--thread-source", help="analytics label, e.g. user or subagent")
    group.add_argument("--session-start-source", choices=SESSION_START_SOURCES)
    group.add_argument("--project-id", help="attach the thread to an existing Codex project")
    group.add_argument("--service-name", help="client service name recorded on the thread")
    group.add_argument("--base-instructions-file", type=Path, help="replace the built-in base instructions")
    group.add_argument("--developer-instructions-file", type=Path, help="replace the opponent role prompt")
    group.add_argument(
        "--add-dir", type=Path, action="append", default=[], metavar="DIR",
        help="extra runtime workspace root, absolute path only, repeatable",
    )
    group.add_argument(
        "-c", "--config", action="append", default=[], metavar="KEY=VALUE",
        help="config override with a dotted key, repeatable: -c tools.web_search=true",
    )
    group.add_argument("--context-window", type=int, metavar="TOKENS", help="model_context_window")
    group.add_argument("--auto-compact-limit", type=int, metavar="TOKENS", help="model_auto_compact_token_limit")
    group.add_argument("--allow-provider-model-fallback", action="store_true")
    group.add_argument(
        "--ephemeral", action="store_true",
        help="in-memory thread that will NOT appear in the sidebar; for reproducing that",
    )


def _add_turn_flags(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("turn (applied to this round and the ones after)")
    group.add_argument(
        "--model", metavar="SPEC",
        help='model slug or a spoken one: "5.6 luna light", "sol max", "mini"',
    )
    group.add_argument("--effort", help="low, medium, high, xhigh, max, ultra — model dependent")
    group.add_argument("--service-tier", help='sticky speed tier: "default" or "priority"')
    group.add_argument("--service-tier-for-turn", help=f"speed for this turn only; default {SERVICE_TIER_STANDARD}")
    group.add_argument("--turn-personality", choices=PERSONALITIES)
    group.add_argument("--summary", choices=REASONING_SUMMARIES, help="reasoning summary")
    group.add_argument("--turn-sandbox", choices=SANDBOX_MODES, help="sandboxPolicy for the turn")
    group.add_argument("--turn-approval-policy", choices=APPROVAL_POLICIES)
    group.add_argument("--turn-permissions")
    group.add_argument("--turn-approvals-reviewer", choices=APPROVALS_REVIEWERS)
    group.add_argument("--turn-trigger", help="analytics label for what started this turn")
    group.add_argument("--no-output-schema", action="store_true", help="do not constrain the answer")
    group.add_argument("--output-schema-file", type=Path, help="your own JSON Schema instead of the verdict")


class _Parser(argparse.ArgumentParser):
    """An ArgumentParser whose own diagnostics cross the terminal boundary.

    Usage errors echo argv back — "unrecognized arguments: …" — and argv is
    the caller's, not Codex's. Still a way for a control sequence to reach the
    screen, so the message goes through `visible` on its way out.
    """

    def _print_message(self, message: str, file=None) -> None:  # type: ignore[override]
        super()._print_message(visible(message) if message else message, file)


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="duo", description="Consult Codex from Claude Code in a persistent, named thread."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    verify = sub.add_parser("verify", help="check the setup without spending any quota")
    verify.set_defaults(handler=cmd_verify)

    schema = sub.add_parser("schema", help="print the verdict JSON Schema")
    schema.set_defaults(handler=cmd_schema)

    models = sub.add_parser("models", help="list the installed model catalogue")
    models.add_argument("--json", action="store_true")
    models.set_defaults(handler=cmd_models)

    status = sub.add_parser("status", help="show this session's thread and progress")
    status.add_argument("--json", action="store_true", help="machine-readable output")
    status.set_defaults(handler=cmd_status)

    ask = sub.add_parser("ask", help="run one round against the session's thread")
    ask.add_argument("--phase", choices=PHASES, required=True)
    message = ask.add_mutually_exclusive_group(required=True)
    message.add_argument("--message", help="the message text")
    message.add_argument("--message-file", type=Path, help="read the message from a file, '-' for stdin")
    ask.add_argument(
        "--request-id",
        help=(
            "caller-stable id for this round. Repeat it to resume a running turn or "
            "replay a finished one; a fresh id starts a new round. Generated if absent"
        ),
    )
    ask.add_argument(
        "--ack-request-id",
        help="acknowledge the previous round's result so a new round may start",
    )
    ask.add_argument("--profile", choices=sorted(PROFILES), default="default")
    ask.add_argument("--session", help="Claude session id; default $CLAUDE_CODE_SESSION_ID")
    ask.add_argument("--host-session", help="host session id; default $CLAUDE_CODE_HOST_SESSION_ID")
    ask.add_argument("--verdict-format", choices=VERDICT_FORMATS, default=FORMAT_TRAILER)
    ask.add_argument("--max-message-chars", type=int, default=MAX_MESSAGE_CHARS)
    ask.add_argument("--new", action="store_true", help="start a new thread; the previous one is archived")
    ask.add_argument("--keep-old", action="store_true", help="with --new: leave the previous thread unarchived")
    ask.add_argument(
        "--no-fork", action="store_true",
        help="fail instead of forking when Codex Desktop holds the thread",
    )
    naming = ask.add_mutually_exclusive_group()
    naming.add_argument("--name", help="pin the thread name; without it the name follows the Claude chat title")
    naming.add_argument("--name-from-chat", action="store_true",
                        help="unpin a name set earlier with --name and follow the chat title again")
    ask.add_argument("--reveal", action="store_true", help="open the thread in Desktop — after the last round only")
    ask.add_argument("--idle-timeout", type=float, default=TURN_IDLE_TIMEOUT_SEC, help="seconds of silence before interrupting")
    ask.add_argument("--timeout", type=float, default=TURN_HARD_TIMEOUT_SEC, help="optional hard wall-clock cap")
    ask.add_argument("--max-rounds", type=_positive, default=MAX_ROUNDS)
    ask.add_argument("--stop-hold", type=_positive, default=STOP_HOLD_ROUNDS)
    ask.add_argument("--json", action="store_true", help="machine-readable output")
    ask.add_argument("--dry-run", action="store_true", help="print what would be sent and exit")
    _add_thread_flags(ask)
    _add_turn_flags(ask)
    ask.set_defaults(handler=cmd_ask)

    ack = sub.add_parser("ack", help="acknowledge a finished request without starting a new one")
    ack.add_argument("request_id")
    ack.set_defaults(handler=cmd_ack)

    recover = sub.add_parser("recover", help="pull a mapping filed under an old key under the current one")
    recover.add_argument("--from", dest="from_key", required=True, help="the old state key (session id)")
    recover.set_defaults(handler=cmd_recover)

    repair = sub.add_parser(
        "repair-redirect",
        help="set aside or rewrite a migration redirect that cannot be read",
    )
    repair.add_argument("key", help="the state key whose redirect is broken")
    repair.add_argument("--to", dest="destination", help="rewrite it to point at this key")
    repair.set_defaults(handler=cmd_repair_redirect)

    close = sub.add_parser("close", help="forget the mapping; optionally archive the thread")
    close.add_argument("--archive", action="store_true")
    close.add_argument("--force", action="store_true", help="also drop a quarantine marker")
    close.set_defaults(handler=cmd_close)

    return parser


# -- option assembly -------------------------------------------------------


def _read_text_file(path: Path, label: str) -> str:
    """Read a file the protocol expects to be UTF-8, and say so when it is not.

    `utf-8-sig` drops a byte-order mark, which an editor on another platform
    adds silently and which would otherwise travel into the message. A file in
    another encoding is a message a human can act on, not a traceback.
    """
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ConsultError(
            f"{label} at {path} is not UTF-8 (byte {exc.start} is {exc.object[exc.start]:#04x}); "
            "save it as UTF-8 and run the round again"
        ) from None
    except OSError as exc:
        raise ConsultError(f"cannot read {label} from {path}: {exc}") from exc


def _read_optional(path: Path | None, label: str) -> str | None:
    if path is None:
        return None
    return _read_text_file(path, label)


def _output_schema(args: argparse.Namespace) -> dict[str, Any] | None:
    if args.no_output_schema or args.output_schema_file is None:
        return None
    raw = _read_optional(args.output_schema_file, "output schema")
    try:
        parsed = json.loads(raw or "")
    except (json.JSONDecodeError, ValueError) as exc:
        raise ConsultError(f"{args.output_schema_file} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ConsultError(f"{args.output_schema_file} must contain a JSON object")
    return parsed


def _config_with_limits(args: argparse.Namespace) -> dict[str, Any]:
    overrides = nest_overrides(args.config)
    if args.context_window is not None:
        if args.context_window <= 0:
            raise ConsultError("--context-window must be a positive number of tokens")
        overrides["model_context_window"] = args.context_window
    if args.auto_compact_limit is not None:
        if args.auto_compact_limit <= 0:
            raise ConsultError("--auto-compact-limit must be a positive number of tokens")
        overrides["model_auto_compact_token_limit"] = args.auto_compact_limit
    return overrides


def _build_options(args: argparse.Namespace) -> tuple[ThreadOptions, TurnOptions, str]:
    profile = resolve_profile(args.profile)
    model = profile.model
    alias_effort: str | None = None
    if args.model:
        try:
            model, alias_effort = resolve_model_spec(args.model)
        except ValueError as exc:
            raise ConsultError(str(exc)) from exc
    effort = args.effort or alias_effort or profile.effort
    roots = [p.expanduser() for p in args.add_dir]

    thread = ThreadOptions(
        cwd=(args.cwd or Path.cwd()).resolve(),
        model=model,
        model_provider=args.model_provider,
        sandbox=args.sandbox or (None if args.permissions else consult.SANDBOX),
        approval_policy=args.approval_policy or consult.APPROVAL_POLICY,
        permissions=args.permissions,
        approvals_reviewer=args.approvals_reviewer,
        personality=args.personality,
        history_mode=args.history_mode,
        thread_source=args.thread_source,
        session_start_source=args.session_start_source,
        project_id=args.project_id,
        service_tier=args.service_tier,
        service_name=args.service_name,
        base_instructions=_read_optional(args.base_instructions_file, "base instructions"),
        developer_instructions=_read_optional(args.developer_instructions_file, "developer instructions"),
        runtime_workspace_roots=roots,
        config=_config_with_limits(args),
        allow_provider_model_fallback=args.allow_provider_model_fallback,
        ephemeral=args.ephemeral,
    )
    turn = TurnOptions(
        model=model,
        effort=effort,
        service_tier=args.service_tier,
        service_tier_for_turn=args.service_tier_for_turn or profile.service_tier,
        personality=args.turn_personality,
        summary=args.summary,
        sandbox=args.turn_sandbox,
        approval_policy=args.turn_approval_policy,
        permissions=args.turn_permissions,
        approvals_reviewer=args.turn_approvals_reviewer,
        turn_trigger=args.turn_trigger,
        runtime_workspace_roots=roots,
        output_schema=_output_schema(args),
    )
    thread.validate()
    turn.validate()
    return thread, turn, profile.name


def _ids(args: argparse.Namespace) -> tuple[str, str | None]:
    session = (getattr(args, "session", None) or "").strip() or claude_session_id()
    if not session:
        raise ConsultError(
            "CLAUDE_CODE_SESSION_ID is not set. Run inside a Claude Code session or pass --session."
        )
    host = (getattr(args, "host_session", None) or "").strip() or host_session_id()
    return session, host


# -- commands --------------------------------------------------------------


def cmd_schema(_args: argparse.Namespace) -> int:
    print(json.dumps(VERDICT_SCHEMA, ensure_ascii=False, indent=2))
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    catalogue = model_catalogue()
    if args.json:
        print(json.dumps([{
            "slug": m.slug, "display_name": m.display_name, "default_effort": m.default_effort,
            "efforts": list(m.efforts), "speed_tiers": list(m.speed_tiers),
            "default_service_tier": m.default_service_tier,
        } for m in catalogue], ensure_ascii=False, indent=2))
        return 0
    width = max((len(m.slug) for m in catalogue), default=10)
    for info in catalogue:
        out(f"{info.slug:<{width}}  default={info.default_effort:<7} efforts: {', '.join(info.efforts) or '(not listed)'}")
    return 0


def cmd_verify(_args: argparse.Namespace) -> int:
    problems: list[str] = []
    lines: list[str] = []
    try:
        binary = codex_binary()
        lines.append(f"codex binary      {binary}")
        installed = consult.codex_version(binary)
        lines.append(f"codex version     {installed or 'unknown'}")
        if installed and installed not in VERIFIED_CODEX_VERSIONS:
            lines.append(
                f"                  (checked against {', '.join(VERIFIED_CODEX_VERSIONS)}; "
                "regenerate the protocol schema if something behaves oddly)"
            )
    except FileNotFoundError as exc:
        problems.append(str(exc))

    session = claude_session_id()
    host = host_session_id()
    if session:
        lines.append(f"claude session    {session}")
        lines.append(f"host session      {host or '(none — the CLI id is the key)'}")
        lines.append(f"state key         {st.state_key_for(host, session)}")
    else:
        problems.append("CLAUDE_CODE_SESSION_ID is not set — run this from a Claude Code session")

    cwd = Path.cwd()
    lines.append(f"cwd               {cwd}")
    if session:
        transcript = transcript_path(session, cwd)
        lines.append(f"transcript        {transcript} ({'found' if transcript.is_file() else 'missing'})")
        lines.append(f"chat title        {read_chat_title(session, cwd) or '(none yet)'}")
        try:
            key, record = consult.load_for_read(session, host)
        except StateError as exc:
            lines.append(f"thread            {exc}")
        else:
            if record is None:
                lines.append("thread            none yet for this session")
            else:
                lines.append(f"thread            {record.thread_id or '(creating)'}  «{record.thread_name}»")
                if record.open_request:
                    lines.append(f"open request      {record.open_request.request_id} ({record.open_request.status})")

    lines.append(f"state dir         {state_dir()}")
    lines.append(f"models known      {len(model_catalogue())} (see `duo models`)")
    for name in sorted(PROFILES):
        lines.append(f"profile {name:<13} {PROFILES[name].describe()}")
    lines.append(f"rounds            max {MAX_ROUNDS}, stop must hold {STOP_HOLD_ROUNDS}")
    out("\n".join(lines))
    if problems:
        say("")
        for problem in problems:
            say(f"{problem}")
        return 1
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    session, host = _ids(args)
    key, record = consult.load_for_read(session, host)
    if record is None:
        if args.json:
            print(json.dumps({"state_key": key, "thread": None}, ensure_ascii=False))
        else:
            out("Нет треда для этой сессии. Первый же `ask` его создаст.")
        return 0
    if args.json:
        print(json.dumps(record.to_json(), ensure_ascii=False, indent=2))
        return 0
    used = " ".join(x for x in (record.model, record.effort) if x)
    out(f"ключ    {record.state_key}")
    out(f"тред    {record.thread_id or '(создаётся)'}")
    out(f"имя     {visible(record.thread_name)}")
    out(f"профиль {record.profile}" + (f"  ({used})" if used else ""))
    out(f"создан  {record.created_at}")
    if record.open_request:
        o = record.open_request
        out(f"запрос  {o.request_id}  {o.status}  ({o.phase}, с {o.started_at})")
    for name in PHASES:
        phase = record.phases.get(name)
        if phase is None:
            out(f"{name:<7} не начиналась")
            continue
        out(f"{name:<7} раундов {phase.rounds}, итог {phase.outcome or 'в работе'}")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    session, host = _ids(args)
    if args.idle_timeout <= 0:
        raise ConsultError("--idle-timeout must be a positive number of seconds")
    if args.timeout is not None and args.timeout <= 0:
        raise ConsultError("--timeout must be a positive number of seconds when given")
    message = _read_message(args)
    thread_options, turn_options, profile_name = _build_options(args)

    note = effort_warning(turn_options.model, turn_options.effort)
    if note:
        say(f"{note}")
    if thread_options.ephemeral:
        say(f"--ephemeral was passed; this thread will not appear in the sidebar")

    if args.dry_run:
        return _dry_run(session, host, args, thread_options, turn_options, message)

    outcome = consult.run_round(
        claude_session_id=session,
        host_session_id=host,
        phase=args.phase,
        message=message,
        thread_options=thread_options,
        turn_options=turn_options,
        profile_name=profile_name,
        request_id=args.request_id,
        ack_request_id=args.ack_request_id,
        force_new=args.new,
        keep_old=args.keep_old,
        name_override=args.name,
        reveal=args.reveal,
        timeout=args.timeout,
        idle_timeout=args.idle_timeout,
        verdict_format=args.verdict_format,
        max_message_chars=args.max_message_chars,
        max_rounds=args.max_rounds,
        stop_hold=args.stop_hold,
        fork_on_busy=not args.no_fork,
        explicit_cwd=args.cwd is not None,
        pin_name=args.name is not None,
        unpin_name=bool(args.name_from_chat),
    )
    crash_point("before_stdout")

    if args.json:
        print(json.dumps(_outcome_json(outcome, args), ensure_ascii=False, indent=2))
        return 0
    _print_round(outcome, args.phase, args.max_rounds)
    return 0


def cmd_ack(args: argparse.Namespace) -> int:
    session, host = _ids(args)
    if consult.acknowledge(session, host, args.request_id):
        out(f"Запрос {args.request_id} подтверждён; следующий раунд открыт.")
        return 0
    say(f"no unacknowledged request {args.request_id} for this session")
    return 2


def cmd_recover(args: argparse.Namespace) -> int:
    session, host = _ids(args)
    record = consult.recover(session, host, args.from_key.strip())
    out(f"Восстановлено: тред {record.thread_id} «{visible(record.thread_name)}» теперь под ключом {record.state_key}.")
    return 0


def cmd_repair_redirect(args: argparse.Namespace) -> int:
    landed = consult.repair_redirect(args.key, args.destination)
    if landed:
        out(f"Редирект {args.key} теперь указывает на {landed}.")
    else:
        out(f"Редирект {args.key} убран в сторону; ключ снова читается как живой.")
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    session, host = _ids(args)
    record = consult.close(session, host, archive=args.archive, force=args.force)
    if record is None:
        out("Нечего закрывать: треда для этой сессии нет.")
        return 0
    if args.archive:
        out(f"Тред {record.thread_id} заархивирован.")
    out(f"Связь сессии с тредом {record.thread_id} забыта.")
    return 0


# -- helpers ---------------------------------------------------------------


def _read_message(args: argparse.Namespace) -> str:
    if args.message is not None:
        text = args.message
    elif str(args.message_file) == "-":
        text = sys.stdin.read()
    else:
        text = _read_text_file(args.message_file, "the message")
    if not text.strip():
        raise ConsultError("the message is empty")
    return text


def _verdict_json(verdict: Verdict) -> dict[str, Any]:
    return {
        "parsed": verdict.parsed,
        "parse_error": verdict.parse_error,
        "stance": verdict.stance,
        "stop": verdict.stop,
        "confidence": verdict.confidence,
        "summary": verdict.summary,
        "prose": verdict.prose,
        "open_questions": verdict.open_questions,
        "findings": [
            {"id": f.id, "severity": f.severity, "claim": f.claim, "evidence": f.evidence,
             "response": f.response, "rationale": f.rationale}
            for f in verdict.findings
        ],
        "raw_text": verdict.raw_text if not verdict.parsed else None,
    }


def _outcome_json(outcome: consult.RoundOutcome, args: argparse.Namespace) -> dict[str, Any]:
    decision = outcome.decision
    return {
        "state_key": outcome.key,
        "thread_id": outcome.record.thread_id,
        "thread_name": outcome.record.thread_name,
        "request_id": outcome.request_id,
        "replayed": outcome.replayed,
        "resumed": outcome.resumed,
        "phase": args.phase,
        "round": outcome.round_number,
        "finished": bool(decision and decision.finished),
        "outcome": decision.outcome if decision else None,
        "reason": decision.reason if decision else None,
        "stop_streak": decision.stop_streak if decision else None,
        "turn_status": outcome.turn_status,
        "turn_errors": outcome.turn_errors,
        "turn_error_kind": outcome.turn_error_kind,
        "verdict_format": args.verdict_format,
        "verdict": _verdict_json(outcome.verdict),
    }


def _print_conflict(exc: OpenRequestConflict, *, as_json: bool = False) -> None:
    open_request = exc.open_request
    say(exc)
    result = open_request.result or {}
    text = result.get("final_text")
    stored = bool(open_request.status == st.REQUEST_UNACKNOWLEDGED and text)
    if as_json:
        # A caller that asked for JSON gets JSON on this path too; it used to
        # get the stored answer raw, control characters included.
        print(json.dumps(
            {"conflict": str(exc), "request_id": open_request.request_id,
             "status": open_request.status, "stored_answer": text if stored else None},
            ensure_ascii=False, indent=2,
        ))
        return
    if stored:
        out(f"--- сохранённый ответ на запрос {open_request.request_id} ---")
        out(visible(text))


def _dry_run(
    session: str,
    host: str | None,
    args: argparse.Namespace,
    thread_options: ThreadOptions,
    turn_options: TurnOptions,
    message: str,
) -> int:
    key, record = consult.load_for_read(session, host)
    reuse = record is not None and record.thread_id and not args.new
    thread_id = record.thread_id if reuse and record else "<created on first run>"
    title = args.name or read_chat_title(session, thread_options.cwd) or f"duo {key[:8]}"
    request_id = args.request_id or "<generated uuid4>"

    if thread_options.developer_instructions is None:
        thread_options.developer_instructions = consult.developer_instructions(args.phase, args.verdict_format)
    if turn_options.output_schema is None and args.verdict_format == "json":
        turn_options.output_schema = VERDICT_SCHEMA
    turn_options.client_user_message_id = request_id

    calls: list[dict[str, Any]] = [
        {"method": "initialize", "params": {
            "clientInfo": {"name": "duo", "title": "Duo consultation loop", "version": "0.1.0"},
            "capabilities": {"experimentalApi": True},
        }},
        {"notification": "initialized"},
    ]
    if reuse:
        calls.append({"method": "thread/resume", "params": {
            "threadId": thread_id, "cwd": str(thread_options.cwd), "excludeTurns": True}})
    else:
        calls.append({"method": "thread/start", "params": thread_options.to_params()})
        calls.append({"method": "thread/name/set", "params": {"threadId": "<new>", "name": title}})
    calls.append({"method": "turn/start", "params": turn_options.to_params(
        thread_id, consult.frame_message(args.phase, message))})
    print(json.dumps({"dry_run": True, "state_key": key, "request_id": request_id, "calls": calls},
                     ensure_ascii=False, indent=2))
    return 0


def _print_round(outcome: consult.RoundOutcome, phase: str, max_rounds: int) -> None:
    record = outcome.record
    verdict = outcome.verdict
    out(f"тред   {record.thread_id}  «{visible(record.thread_name)}»")
    out(f"запрос {outcome.request_id}" + ("  (повтор из state)" if outcome.replayed else "  (восстановлен из истории)" if outcome.resumed else ""))
    out(f"фаза   {phase}, раунд {outcome.round_number} из {max_rounds}")
    if not outcome.revealed:
        out(f'смотреть: open "codex://threads/{record.thread_id}"  (после последнего раунда)')
    if outcome.turn_status and outcome.turn_status != "completed":
        out(f"ход    завершился со статусом {outcome.turn_status}")
    for error in outcome.turn_errors:
        out(f"ошибка хода: {visible(error)}")

    if not verdict.parsed:
        out(f"ОШИБКА разбора: {visible(verdict.parse_error)}")
        if verdict.raw_text:
            out("--- что ответил Codex ---")
            out(visible(verdict.raw_text[:4000]))
        decision = outcome.decision
        if decision:
            out(f"дальше {'стоп' if decision.finished else 'ещё раунд'} — {decision.reason}")
        out(f"подтвердить: --ack-request-id {outcome.request_id}")
        return

    if verdict.prose:
        out()
        out(visible(verdict.prose))
        out()
    out(f"позиция {verdict.stance}, stop={verdict.stop}, уверенность {verdict.confidence}")
    if verdict.summary and verdict.summary not in verdict.prose:
        out(f"\n{visible(verdict.summary)}\n")
    if verdict.findings:
        out("возражения:")
        for finding in verdict.findings:
            mark = "!" if finding.holds_the_loop else " "
            out(f" {mark} [{visible(finding.severity)}] {visible(finding.id)} {visible(finding.response)}: {visible(finding.claim)}")
            if finding.evidence:
                out(f"     доказательство: {visible(finding.evidence)}")
            if finding.rationale:
                out(f"     обоснование: {visible(finding.rationale)}")
    if verdict.open_questions:
        out("открытые вопросы:")
        for question in verdict.open_questions:
            out(f" - {visible(question)}")
    out()
    decision = outcome.decision
    if decision is None:
        out("ИТОГ: (результат воспроизведён; счётчики раундов не менялись)")
    elif decision.finished:
        out(f"ИТОГ: {decision.outcome} — {decision.reason}")
        out(outcome_headline(decision.outcome))
    else:
        out(f"ПРОДОЛЖАТЬ: {decision.reason}")
    out(f"следующий раунд: --ack-request-id {outcome.request_id} --request-id <новый uuid>")


if __name__ == "__main__":
    raise SystemExit(main())
