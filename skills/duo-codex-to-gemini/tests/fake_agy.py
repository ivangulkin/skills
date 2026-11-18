#!/usr/bin/env python3
"""Fake `agy` 1.5.11 print-mode binary for offline end-to-end tests of duo-codex-to-gemini.

It reproduces what the real CLI does, measured 18.11.2026, including the
parts that broke the 1.4 adapter: slash prompts answered by the CLI with an
empty conversation id, bare `-p` rejected, prompts read from stdin only as
stream-json `user` events, and output that carries the answer in the
terminal `result` event rather than in text chunks.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid


def _load_journal(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"conversations": {}, "calls": []}


def _save_journal(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _emit(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def _result(cid: str, status: str, response: str = "", error: str | None = None) -> dict:
    body: dict = {"conversation_id": cid, "status": status, "response": response,
                  "duration_seconds": 0.1, "num_turns": 1 if status == "SUCCESS" else 0,
                  "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}
    if error is not None:
        body["error"] = error
        body["finished_reason"] = "error"
    return {"event": "result", "result": body}


def _materialize(cid: str) -> None:
    """Brain directory and summaries row, as the Antigravity daemon creates them."""
    if "DUO_BRAIN_DIR" in os.environ:
        (Path(os.environ["DUO_BRAIN_DIR"]) / cid / "scratch").mkdir(parents=True, exist_ok=True)
    db_path = Path(os.environ["DUO_SUMMARIES_DB"]) if "DUO_SUMMARIES_DB" in os.environ else None
    if db_path is not None and db_path.is_file():
        conn = sqlite3.connect(str(db_path))
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO conversation_summaries (conversation_id, title) VALUES (?, '')",
                (cid,),
            )
        conn.close()


def _append_transcript(cid: str, prompt: str, reply_text: str) -> None:
    if "DUO_BRAIN_DIR" not in os.environ:
        return
    logs_dir = Path(os.environ["DUO_BRAIN_DIR"]) / cid / ".system_generated" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    transcript = logs_dir / "transcript_full.jsonl"
    step_idx = len(transcript.read_text(encoding="utf-8").splitlines()) if transcript.is_file() else 0
    steps = [
        {"step_index": step_idx, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE",
         "content": f"<USER_REQUEST>\n{prompt}\n</USER_REQUEST>"},
        {"step_index": step_idx + 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE",
         "content": reply_text},
    ]
    with transcript.open("a", encoding="utf-8") as handle:
        for step in steps:
            handle.write(json.dumps(step, ensure_ascii=False) + "\n")


def _flag(argv: list[str], name: str) -> str | None:
    for i, arg in enumerate(argv):
        if arg == name and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return None


def main(argv: list[str]) -> int:
    if "--version" in argv:
        print("1.5.11")
        return 0

    # Go's flag package: `-p` is a string flag and needs a value.
    if argv and argv[-1] in ("-p", "--print"):
        sys.stderr.write("flag needs an argument: -p\n")
        return 2
    prompt_flag = next((a.split("=", 1)[1] for a in argv if a.startswith(("-p=", "--print="))), None)
    output_format = _flag(argv, "--output-format") or "text"
    input_format = _flag(argv, "--input-format") or "text"
    if input_format == "stream-json" and output_format != "stream-json":
        sys.stderr.write("error: --input-format stream-json requires --output-format stream-json\n")
        return 2
    if "--mode" in argv and "--disable-slash-commands" in argv:
        sys.stderr.write("warning: --mode is ignored when --disable-slash-commands is set\n")

    journal_path = Path(os.environ.get("FAKE_AGY_JOURNAL", "/tmp/fake_agy_journal.json"))
    journal = _load_journal(journal_path)
    cid = _flag(argv, "--conversation") or ""
    model = _flag(argv, "--model")
    effort = _flag(argv, "--effort")

    # A slash prompt is answered by the CLI itself: no conversation is created.
    if input_format != "stream-json" and prompt_flag and prompt_flag.startswith("/"):
        journal["calls"].append({"argv": argv, "kind": "slash_command"})
        _save_journal(journal_path, journal)
        _emit({"event": "command_result", "command_result": {"command": prompt_flag, "output": "Current model: Gemini 4 Carbon (High)"}})
        _emit({"event": "result", "result": {"conversation_id": "", "status": "SUCCESS", "response": ""}})
        return 0

    # Real agy refuses --model <slug>-<effort> together with --effort.
    if model and effort and any(model.endswith(f"-{s}") for s in ("low", "medium", "high")):
        error = (f'invalid model selection (--model "{model}" --effort "{effort}"): '
                 f"--model {model} conflicts with --effort={effort}")
        sys.stderr.write(f"error: {error}\n")
        _emit(_result("", "ERROR", error=error))
        return 1

    if not cid:
        cid = os.environ.get("FAKE_AGY_CONVERSATION_ID") or str(uuid.uuid4())
    _materialize(cid)
    journal["conversations"].setdefault(cid, {"turns": []})

    if input_format == "stream-json":
        _emit({"event": "init", "conversation_id": cid, "init": {"model": model, "cwd": os.getcwd(), "tools": [], "permission_mode": "always-proceed"}})
        prompts: list[str] = []
        for line in sys.stdin:
            if not line.strip():
                continue
            message = json.loads(line)
            if "event" not in message:
                error = 'stream input message is missing the "event" field'
            elif message["event"] != "user":
                sys.stderr.write(f'warning: ignoring unsupported stream input message event "{message["event"]}"\n')
                continue
            elif not isinstance(message.get("message"), dict):
                error = 'stream input "user" message is missing the "message" field'
            elif not isinstance(message["message"].get("content"), str) or not message["message"]["content"]:
                error = 'stream input "user" message has no content'
            else:
                prompts.append(message["message"]["content"])
                continue
            sys.stderr.write(f"error: {error}\n")
            _emit(_result(cid, "ERROR", error=error))
            return 0
        if not prompts:
            # Empty input: the conversation exists, no turn ran.
            journal["calls"].append({"argv": argv, "kind": "init_only", "conversation_id": cid})
            _save_journal(journal_path, journal)
            return 0
    else:
        prompts = [prompt_flag or ""]
        _emit({"event": "init", "conversation_id": cid, "init": {"model": model, "cwd": os.getcwd()}})

    for prompt in prompts:
        failure = os.environ.get("FAKE_AGY_FAIL")
        verdict = os.environ.get(
            "FAKE_AGY_VERDICT",
            json.dumps(
                {
                    "stance": "agree",
                    "stop": True,
                    "confidence": "high",
                    "summary": "Возражений нет.",
                    "findings": [],
                    "open_questions": [],
                },
                ensure_ascii=False,
            ),
        )
        reply_text = f"Разбор Gemini завершён.\n\n```json\n{verdict}\n```"
        conv = journal["conversations"][cid]
        conv["turns"].append({"model": model, "effort": effort, "prompt": prompt})
        journal["calls"].append({"argv": argv, "kind": "turn", "conversation_id": cid, "model": model, "effort": effort})
        _save_journal(journal_path, journal)

        _emit({"event": "step_update", "step_update": {"conversation_id": cid, "step_index": 0, "state": "DONE", "step_type": "user_input"}})
        if failure:
            _emit(_result(cid, "ERROR", error=failure))
            continue
        _append_transcript(cid, prompt, reply_text)
        half = len(reply_text) // 2
        _emit({"event": "step_update", "step_update": {"conversation_id": cid, "step_index": 1, "state": "ACTIVE", "step_type": "agent_response", "text_delta": reply_text[:half]}})
        _emit({"event": "step_update", "step_update": {"conversation_id": cid, "step_index": 1, "state": "DONE", "step_type": "agent_response", "text_delta": reply_text[half:]}})
        _emit(_result(cid, "SUCCESS", response=reply_text + "\n"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
