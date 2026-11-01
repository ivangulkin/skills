#!/usr/bin/env python3
"""Fake `agy` print-mode binary for offline end-to-end tests of duo-codex-to-gemini."""

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


def main(argv: list[str]) -> int:
    if "--version" in argv:
        print("agy 1.4.12")
        return 0

    journal_path = Path(os.environ.get("FAKE_AGY_JOURNAL", "/tmp/fake_agy_journal.json"))
    journal = _load_journal(journal_path)

    # Detect `-p=/model` zero-token conversation creation
    if "-p=/model" in argv:
        cid = os.environ.get("FAKE_AGY_CONVERSATION_ID") or str(uuid.uuid4())
        journal["conversations"].setdefault(cid, {"turns": []})
        journal["calls"].append({"argv": argv, "kind": "init_only", "conversation_id": cid})
        _save_journal(journal_path, journal)
        print(json.dumps({"event": "init", "conversation_id": cid, "cwd": os.getcwd()}))
        print(json.dumps({"event": "AppendResponseText", "index": 0, "content": "Current model: gemini-4-argon-high"}))
        print(json.dumps({"event": "result", "status": "CONVERSATION_STATUS_IDLE", "duration_ms": 5}))
        return 0

    # Parse flags for a real print-mode turn
    cid = ""
    model = None
    effort = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--conversation" and i + 1 < len(argv):
            cid = argv[i + 1]
            i += 2
            continue
        if arg == "--model" and i + 1 < len(argv):
            model = argv[i + 1]
            i += 2
            continue
        if arg == "--effort" and i + 1 < len(argv):
            effort = argv[i + 1]
            i += 2
            continue
        i += 1

    # Reproduce real agy conflict check: --model <slug>-<effort> + --effort <effort> fails
    if model and effort and any(model.endswith(f"-{s}") for s in ("low", "medium", "high")):
        sys.stderr.write(
            f'error: invalid model selection (--model "{model}" --effort "{effort}"): '
            f"--model {model} conflicts with --effort={effort}\n"
        )
        return 1

    prompt = sys.stdin.read()
    if not cid:
        cid = str(uuid.uuid4())

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

    # Materialize transcript and summaries DB row just like the Antigravity daemon
    brain_root = Path(os.environ["DUO_BRAIN_DIR"]) if "DUO_BRAIN_DIR" in os.environ else None
    if brain_root is not None:
        logs_dir = brain_root / cid / ".system_generated" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        transcript = logs_dir / "transcript_full.jsonl"
        existing_lines = transcript.read_text(encoding="utf-8").splitlines() if transcript.is_file() else []
        step_idx = len(existing_lines)
        user_step = {
            "step_index": step_idx,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "status": "DONE",
            "content": prompt,
        }
        planner_step = {
            "step_index": step_idx + 1,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "content": reply_text,
        }
        with transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(user_step, ensure_ascii=False) + "\n")
            handle.write(json.dumps(planner_step, ensure_ascii=False) + "\n")

    db_path = Path(os.environ["DUO_SUMMARIES_DB"]) if "DUO_SUMMARIES_DB" in os.environ else None
    if db_path is not None and db_path.is_file():
        conn = sqlite3.connect(str(db_path))
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO conversation_summaries (conversation_id, title) VALUES (?, '')",
                (cid,),
            )
        conn.close()

    conv = journal["conversations"].setdefault(cid, {"turns": []})
    conv["turns"].append({"model": model, "effort": effort, "prompt": prompt})
    journal["calls"].append({"argv": argv, "kind": "turn", "conversation_id": cid, "model": model, "effort": effort})
    _save_journal(journal_path, journal)

    # Emit streamed chunks across two indices to verify step-indexed accumulation
    print(json.dumps({"event": "init", "conversation_id": cid, "cwd": os.getcwd()}))
    print(json.dumps({"event": "AppendResponseText", "index": 1, "content": "Промежуточный шаг проверки файла."}))
    half = len(reply_text) // 2
    print(json.dumps({"event": "AppendResponseText", "index": 2, "content": reply_text[:half]}))
    print(json.dumps({"event": "AppendResponseText", "index": 2, "content": reply_text[half:]}))
    print(json.dumps({"event": "result", "status": "CONVERSATION_STATUS_IDLE", "duration_ms": 12}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
