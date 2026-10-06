#!/usr/bin/env python3
"""Tests for the Duo consultation loop.

Everything here runs against ``fake_codex.py`` — no API key, no quota, no
thread left behind in anyone's Codex. What the fake emits was copied from the
schema the real binary generates, and since the 04.09.2026 design round it
keeps a durable journal, so "no duplicate turn" is asserted, not assumed.

Run: ``bash tests/run-tests.sh`` from the skill root.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

SKILL_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = SKILL_ROOT / "scripts" / "duo"
FAKE = Path(__file__).resolve().parent / "fake_codex.py"

sys.path.insert(0, str(PACKAGE))

import appserver  # noqa: E402
import config  # noqa: E402
import consult  # noqa: E402
import options as options_mod  # noqa: E402
import state as st  # noqa: E402
import verdict as verdict_mod  # noqa: E402
from verdict import (  # noqa: E402
    AGREED_UNHELD,
    CONVERGED,
    MAX_ROUNDS_REACHED,
    STABLE_DISAGREEMENT,
    UNPARSEABLE,
    decide,
    parse,
)

SESSION = "11111111-2222-4333-8444-555555555555"
HOST = "local_aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def verdict_json(**overrides: object) -> str:
    payload = {
        "stance": "agree",
        "stop": True,
        "confidence": "high",
        "summary": "Возражений нет.",
        "findings": [],
        "open_questions": [],
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def finding(**overrides: object) -> dict[str, object]:
    item = {
        "id": "f1",
        "severity": "p0",
        "claim": "Состояние пишется неатомарно.",
        "evidence": "state.py:120",
        "response": "new",
        "rationale": "",
    }
    item.update(overrides)
    return item


class EnvSandbox(unittest.TestCase):
    """Every test gets its own state directory, journal and fake Codex."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._saved = dict(os.environ)
        os.environ["DUO_STATE_DIR"] = str(self.tmp / "state")
        os.environ["DUO_CODEX_BIN"] = str(FAKE)
        os.environ["CLAUDE_CODE_SESSION_ID"] = SESSION
        os.environ["CLAUDE_CODE_HOST_SESSION_ID"] = HOST
        os.environ["FAKE_CODEX_MODE"] = "ok"
        os.environ["FAKE_CODEX_JOURNAL"] = str(self.tmp / "journal.json")
        for name in ("FAKE_CODEX_VERDICT", "FAKE_CODEX_THREAD_ID", "FAKE_CODEX_SEEN", "DUO_TEST_CRASH_AT"):
            os.environ.pop(name, None)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        os.environ.clear()
        os.environ.update(self._saved)
        self._tmp.cleanup()

    # -- fake journal helpers ---------------------------------------------

    def journal(self) -> dict:
        try:
            return json.loads((self.tmp / "journal.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"threads": {}}

    def turns_of(self, thread_id: str) -> list[dict]:
        return self.journal().get("threads", {}).get(thread_id, {}).get("turns", [])

    def seen(self) -> list[dict]:
        return json.loads((self.tmp / "seen.json").read_text(encoding="utf-8"))

    def watch(self) -> None:
        os.environ["FAKE_CODEX_SEEN"] = str(self.tmp / "seen.json")


# =========================================================================
# verdict parsing and the stopping rule (unchanged surface)
# =========================================================================


class TestVerdictParsing(EnvSandbox):
    def test_plain_json(self) -> None:
        v = parse(verdict_json())
        self.assertTrue(v.parsed)
        self.assertTrue(v.wants_stop)

    def test_json_in_a_code_fence(self) -> None:
        v = parse("```json\n" + verdict_json() + "\n```")
        self.assertTrue(v.parsed, v.parse_error)

    def test_prose_then_trailer(self) -> None:
        v = parse("Всё хорошо.\n\n```json\n" + verdict_json() + "\n```")
        self.assertTrue(v.parsed)
        self.assertEqual(v.prose, "Всё хорошо.")

    def test_prose_is_not_a_verdict(self) -> None:
        v = parse("Конечно! Вот мой анализ.")
        self.assertFalse(v.parsed)
        self.assertIn("no ```json verdict block", str(v.parse_error))

    def test_stop_next_to_an_open_blocker_does_not_stop(self) -> None:
        v = parse(verdict_json(stop=True, stance="agree", findings=[finding()]))
        self.assertFalse(v.wants_stop)

    def test_unknown_enum_values_fall_back_to_the_careful_side(self) -> None:
        """Every fallback errs towards "keep going", never towards "we are done"."""
        v = parse(verdict_json(stance="x", confidence="y", findings=[finding(severity="z", response="w")]))
        self.assertEqual((v.stance, v.confidence), ("needs_info", "low"))
        self.assertEqual((v.findings[0].severity, v.findings[0].response), ("p1", "new"))


class TestStoppingRule(EnvSandbox):
    def test_two_in_a_row_converge(self) -> None:
        d = decide(parse(verdict_json()), round_number=2, stop_streak=1, previous_unresolved=None)
        self.assertEqual((d.finished, d.outcome), (True, CONVERGED))

    def test_one_agreement_is_not_enough(self) -> None:
        d = decide(parse(verdict_json()), round_number=1, stop_streak=0, previous_unresolved=None)
        self.assertFalse(d.finished)

    def test_agreement_on_the_last_round_is_not_called_a_failure(self) -> None:
        d = decide(parse(verdict_json(findings=[finding(response="confirm")])),
                   round_number=3, stop_streak=0, previous_unresolved=frozenset({"f1"}))
        self.assertEqual(d.outcome, AGREED_UNHELD)

    def test_the_same_objections_twice_ends_it(self) -> None:
        d = decide(parse(verdict_json(stance="disagree", stop=False, findings=[finding()])),
                   round_number=2, stop_streak=0, previous_unresolved=frozenset({"f1"}))
        self.assertEqual(d.outcome, STABLE_DISAGREEMENT)

    def test_round_limit_without_objections(self) -> None:
        d = decide(parse(verdict_json(stance="disagree", stop=False)),
                   round_number=verdict_mod.MAX_ROUNDS, stop_streak=0, previous_unresolved=None)
        self.assertEqual(d.outcome, MAX_ROUNDS_REACHED)

    def test_unreadable_to_the_end(self) -> None:
        d = decide(parse("не json"), round_number=verdict_mod.MAX_ROUNDS, stop_streak=0, previous_unresolved=None)
        self.assertEqual(d.outcome, UNPARSEABLE)


# =========================================================================
# state v2: key, lock, tombstone, migration
# =========================================================================


def make_state(key: str = HOST, thread_id: str | None = "01a0-thread") -> st.SessionState:
    return st.SessionState(
        state_key=key,
        claude_session_id=SESSION,
        claude_host_session_id=HOST,
        thread_id=thread_id,
        thread_name="Имя чата",
        cwd="/tmp",
        profile="test",
        created_at=st.now_iso(),
        updated_at=st.now_iso(),
        rollout_materialized=thread_id is not None,
    )


class TestStateKey(EnvSandbox):
    def test_host_id_wins_when_present(self) -> None:
        self.assertEqual(st.state_key_for(HOST, SESSION), HOST)

    def test_cli_id_when_no_host(self) -> None:
        self.assertEqual(st.state_key_for(None, SESSION), SESSION)
        self.assertEqual(st.state_key_for("  ", SESSION), SESSION)

    def test_the_path_comes_from_the_key_not_the_payload(self) -> None:
        record = make_state()
        st.save(HOST, record)
        record.claude_session_id = "99999999-0000-4000-8000-000000000000"
        st.save(HOST, record)
        self.assertTrue(st.state_path(HOST).exists())
        self.assertFalse(st.state_path(record.claude_session_id).exists())

    def test_a_hostile_key_is_refused(self) -> None:
        for bad in ("../escape", "a/b", "", "with space"):
            with self.assertRaises(ValueError):
                st.state_path(bad)

    def test_v1_files_are_upgraded_in_memory(self) -> None:
        legacy = {
            "claude_session_id": SESSION, "thread_id": "01a0-old", "thread_name": "n", "cwd": "/tmp",
            "profile": "default", "created_at": "", "updated_at": "", "version": 1,
            "phases": {"design": {"rounds": 2, "outcome": "converged", "stop_streak": 2, "unresolved_ids": []}},
        }
        st.state_dir().mkdir(parents=True, exist_ok=True)
        st.state_path(SESSION).write_text(json.dumps(legacy), encoding="utf-8")
        loaded = st.load(SESSION)
        assert loaded is not None
        self.assertEqual(loaded.state_key, SESSION)
        self.assertTrue(loaded.rollout_materialized)
        self.assertEqual(loaded.phase("design").rounds, 2)


class TestStateLock(EnvSandbox):
    def test_a_second_holder_is_refused_not_blocked(self) -> None:
        """flock conflicts across descriptors, so this needs a second process."""
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import sys,time; sys.path.insert(0, sys.argv[1]); import state as st\n"
             "with st.lock(sys.argv[2]):\n"
             "    print('held', flush=True); time.sleep(5)",
             str(PACKAGE), HOST],
            stdout=subprocess.PIPE, text=True, env=os.environ.copy(),
        )
        try:
            assert holder.stdout is not None
            self.assertEqual(holder.stdout.readline().strip(), "held")
            with self.assertRaises(st.StateLocked):
                with st.lock(HOST):
                    pass
        finally:
            holder.kill()
            holder.wait()
            if holder.stdout is not None:
                holder.stdout.close()

    def test_the_lock_file_is_persistent(self) -> None:
        with st.lock(HOST):
            pass
        self.assertTrue(st.lock_path(HOST).exists())


class TestTombstone(EnvSandbox):
    def test_absent_is_only_enoent(self) -> None:
        self.assertIsNone(st.load(HOST))

    def test_corrupt_json_is_quarantined_and_blocks(self) -> None:
        st.state_dir().mkdir(parents=True, exist_ok=True)
        st.state_path(HOST).write_text("{ broken", encoding="utf-8")
        with self.assertRaises(st.StateCorrupt):
            st.load(HOST)
        self.assertTrue(st.blocked_path(HOST).exists())
        self.assertFalse(st.state_path(HOST).exists())
        self.assertTrue(list(st.state_dir().glob(f"{HOST}.json.corrupt-*")))
        # The next run is *still* blocked — no fail-open into a new thread.
        with self.assertRaises(st.StateBlocked):
            st.load(HOST)

    def test_bad_schema_is_quarantined_too(self) -> None:
        st.state_dir().mkdir(parents=True, exist_ok=True)
        st.state_path(HOST).write_text(json.dumps({"version": 7, "state_key": HOST}), encoding="utf-8")
        with self.assertRaises(st.StateCorrupt):
            st.load(HOST)

    def test_a_malformed_open_request_is_a_schema_error(self) -> None:
        payload = make_state().to_json()
        payload["open_request"] = {"status": "pending"}  # no request_id
        st.state_dir().mkdir(parents=True, exist_ok=True)
        st.state_path(HOST).write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(st.StateCorrupt):
            st.load(HOST)

    def test_an_unreadable_file_is_not_absent(self) -> None:
        st.save(HOST, make_state())
        st.state_path(HOST).chmod(0)
        try:
            with self.assertRaises(st.StateUnreadable):
                st.load(HOST)
        finally:
            st.state_path(HOST).chmod(0o600)

    def test_a_matching_replacement_releases_the_block(self) -> None:
        """Crash between save(new) and unlink(marker) must not re-block."""
        st.state_dir().mkdir(parents=True, exist_ok=True)
        st.state_path(HOST).write_text("nope", encoding="utf-8")
        with self.assertRaises(st.StateCorrupt):
            st.load(HOST)
        block_id = st.current_block_id(HOST)
        assert block_id is not None
        replacement = make_state()
        replacement.resolves_block_id = block_id
        body = json.dumps(replacement.to_json())
        # Simulate the crash: the new state lands, the marker was never removed.
        st.state_path(HOST).write_text(body, encoding="utf-8")
        loaded = st.load(HOST)
        assert loaded is not None
        self.assertEqual(loaded.thread_id, "01a0-thread")
        self.assertFalse(st.blocked_path(HOST).exists())

    def test_a_non_matching_replacement_stays_blocked(self) -> None:
        st.state_dir().mkdir(parents=True, exist_ok=True)
        st.state_path(HOST).write_text("nope", encoding="utf-8")
        with self.assertRaises(st.StateCorrupt):
            st.load(HOST)
        replacement = make_state()
        replacement.resolves_block_id = "someone-else"
        st.state_path(HOST).write_text(json.dumps(replacement.to_json()), encoding="utf-8")
        with self.assertRaises(st.StateBlocked):
            st.load(HOST)


class TestMigration(EnvSandbox):
    def test_a_legacy_file_moves_under_the_host_key(self) -> None:
        st.save(SESSION, make_state(key=SESSION))
        with st.lock_many([SESSION, HOST]):
            moved = st.migrate(SESSION, HOST)
        self.assertEqual(moved.state_key, HOST)
        self.assertTrue(st.state_path(HOST).exists())
        self.assertFalse(st.state_path(SESSION).exists())
        self.assertTrue(list(st.state_dir().glob(f"{SESSION}.json.migrated-*")))
        self.assertIsNotNone(moved.migrated_from)

    def test_a_half_done_move_is_finished_not_served(self) -> None:
        """Destination written, source not yet retired: exactly one binding."""
        src = make_state(key=SESSION)
        src.migrating = {"migration_id": "m-1", "destination_key": HOST}
        st.save(SESSION, src)
        dst = make_state(key=HOST)
        dst.migrated_from = "m-1"
        st.save(HOST, dst)
        self.assertEqual(st.pending_migration_target(SESSION), HOST)
        with st.lock_many([SESSION, HOST]):
            st.migrate(SESSION, HOST)
        self.assertFalse(st.state_path(SESSION).exists())
        self.assertTrue(st.state_path(HOST).exists())

    def test_an_existing_destination_for_the_same_thread_wins(self) -> None:
        """The destination was written after the source was marked: it is newer."""
        src = make_state(key=SESSION, thread_id="01a0-one")
        src.migrating = {"migration_id": "m-2", "destination_key": HOST}
        st.save(SESSION, src)
        dst = make_state(key=HOST, thread_id="01a0-one")
        dst.phase("design").rounds = 5  # newer than the source's 0
        st.save(HOST, dst)
        with st.lock_many([SESSION, HOST]):
            moved = st.migrate(SESSION, HOST)
        self.assertEqual(moved.phase("design").rounds, 5)
        self.assertFalse(st.state_path(SESSION).exists())

    def test_a_destination_with_another_thread_fails_closed(self) -> None:
        st.save(SESSION, make_state(key=SESSION, thread_id="01a0-one"))
        st.save(HOST, make_state(key=HOST, thread_id="01a0-two"))
        with st.lock_many([SESSION, HOST]):
            with self.assertRaises(st.StateError):
                st.migrate(SESSION, HOST)
        self.assertTrue(st.state_path(SESSION).exists())

    def test_the_round_migrates_automatically(self) -> None:
        st.save(SESSION, make_state(key=SESSION, thread_id="01a0-legacy"))
        # The fake must know the legacy thread has a rollout to resume it.
        journal = {"threads": {"01a0-legacy": {"id": "01a0-legacy", "turns": [
            {"id": "t0", "client_id": None, "input": "x", "status": "completed", "result": verdict_json()}]}}}
        (self.tmp / "journal.json").write_text(json.dumps(journal), encoding="utf-8")
        outcome = run(self)
        self.assertEqual(outcome.key, HOST)
        self.assertEqual(outcome.record.thread_id, "01a0-legacy")
        self.assertFalse(outcome.created_thread)


# =========================================================================
# the round, end to end against the fake
# =========================================================================


def run(case: EnvSandbox, **kwargs: object) -> consult.RoundOutcome:
    profile = consult.resolve_profile(str(kwargs.pop("profile", "test")))
    thread = options_mod.ThreadOptions(cwd=case.tmp, model=profile.model)
    turn = options_mod.TurnOptions(
        model=profile.model, effort=profile.effort, service_tier_for_turn=profile.service_tier,
    )
    params: dict[str, object] = {
        "claude_session_id": SESSION,
        "host_session_id": HOST,
        "phase": consult.DESIGN,
        "message": "Предлагаю такое решение.",
        "thread_options": thread,
        "turn_options": turn,
        "profile_name": profile.name,
        "name_override": "Имя чата",
        "reveal": False,
        "timeout": 30,
        "idle_timeout": 30,
    }
    params.update(kwargs)
    return consult.run_round(**params)  # type: ignore[arg-type]


class TestRound(EnvSandbox):
    def test_the_first_round_creates_names_and_never_resumes(self) -> None:
        self.watch()
        outcome = run(self)
        self.assertTrue(outcome.created_thread)
        methods = [c.get("method") for c in self.seen()]
        self.assertNotIn("thread/resume", methods)
        self.assertLess(methods.index("thread/start"), methods.index("turn/start"))
        names = [c for c in self.seen() if c.get("method") == "thread/name/set"]
        self.assertEqual(names[0]["params"]["name"], "Имя чата")
        start = [c for c in self.seen() if c.get("method") == "thread/start"][0]["params"]
        self.assertIs(start["ephemeral"], False)
        # Nothing forced: the thread inherits the user's Codex configuration,
        # which is where "Approve for me" lives. Flags still override.
        self.assertNotIn("sandbox", start)
        self.assertNotIn("approvalPolicy", start)

    def test_initialize_declares_experimental_api(self) -> None:
        self.watch()
        run(self)
        init = [c for c in self.seen() if c.get("method") == "initialize"][0]
        self.assertTrue(init["params"]["capabilities"]["experimentalApi"])

    def test_the_turn_carries_the_request_id(self) -> None:
        self.watch()
        outcome = run(self, request_id="req-1")
        turn = [c for c in self.seen() if c.get("method") == "turn/start"][0]["params"]
        self.assertEqual(turn["clientUserMessageId"], "req-1")
        self.assertEqual(outcome.request_id, "req-1")
        self.assertEqual(self.turns_of(outcome.record.thread_id)[0]["client_id"], "req-1")

    def test_a_request_id_is_generated_when_absent(self) -> None:
        outcome = run(self)
        self.assertEqual(len(outcome.request_id), 36)

    def test_the_second_round_needs_an_ack(self) -> None:
        first = run(self, request_id="req-1")
        with self.assertRaises(consult.OpenRequestConflict):
            run(self, request_id="req-2")
        second = run(self, request_id="req-2", ack_request_id=first.request_id)
        self.assertEqual(second.round_number, 2)
        self.assertEqual(len(self.turns_of(second.record.thread_id)), 2)

    def test_the_same_id_replays_without_a_new_turn(self) -> None:
        first = run(self, request_id="req-1")
        self.watch()
        again = run(self, request_id="req-1")
        self.assertTrue(again.replayed)
        self.assertEqual(again.verdict.stance, first.verdict.stance)
        # A replay answers from state: no app-server was even started.
        self.assertFalse((self.tmp / "seen.json").exists())
        self.assertEqual(len(self.turns_of(first.record.thread_id)), 1)

    def test_rounds_are_counted_once_per_request(self) -> None:
        first = run(self, request_id="req-1")
        again = run(self, request_id="req-1")
        self.assertEqual(again.record.phase("design").rounds, first.record.phase("design").rounds)

    def test_the_same_id_with_another_message_is_refused_before_replay(self) -> None:
        run(self, request_id="req-1")
        with self.assertRaises(consult.ConsultError) as caught:
            run(self, request_id="req-1", message="другое сообщение")
        self.assertIn("different message", str(caught.exception))

    def test_a_replay_reproduces_the_decision(self) -> None:
        os.environ["FAKE_CODEX_VERDICT"] = verdict_json()
        run(self, request_id="req-1")
        second = run(self, request_id="req-2", ack_request_id="req-1")
        assert second.decision is not None
        self.assertTrue(second.decision.finished)
        again = run(self, request_id="req-2")
        self.assertTrue(again.replayed)
        assert again.decision is not None
        self.assertEqual((again.decision.finished, again.decision.outcome), (True, CONVERGED))
        self.assertEqual(again.round_number, 2)

    def test_a_silent_turn_is_interrupted_and_resumable(self) -> None:
        os.environ["FAKE_CODEX_MODE"] = "hang"
        with self.assertRaises(appserver.TurnTimeout):
            run(self, request_id="req-1", timeout=None, idle_timeout=2)
        key = consult.resolve_key(SESSION, HOST).key
        rec = st.load(key)
        assert rec is not None and rec.open_request is not None
        self.assertEqual(rec.open_request.status, st.REQUEST_PENDING)
        # The fake journaled the turn as interrupted; the same id recovers it
        # from history instead of sending the message again.
        os.environ["FAKE_CODEX_MODE"] = "ok"
        self.watch()
        outcome = run(self, request_id="req-1")
        self.assertTrue(outcome.resumed)
        self.assertEqual(outcome.turn_status, "interrupted")
        self.assertEqual([c for c in self.seen() if c.get("method") == "turn/start"], [])
        self.assertEqual(len(self.turns_of(rec.thread_id)), 1)

    def test_a_terminal_failure_is_stored_and_not_retried(self) -> None:
        os.environ["FAKE_CODEX_MODE"] = "quota"
        outcome = run(self, request_id="req-1")
        self.assertEqual(outcome.turn_status, "failed")
        self.assertEqual(outcome.turn_error_kind, "usageLimitExceeded")
        self.assertIsNone(outcome.decision)
        self.assertEqual(outcome.record.phase("design").rounds, 0)

    def test_a_slow_but_chatty_turn_is_never_cut(self) -> None:
        os.environ["FAKE_CODEX_MODE"] = "slow"
        os.environ["FAKE_CODEX_SLOW_SEC"] = "3"
        outcome = run(self, timeout=None, idle_timeout=1.5)
        self.assertEqual(outcome.turn_status, "completed")

    def test_an_approval_request_is_answered(self) -> None:
        os.environ["FAKE_CODEX_MODE"] = "approval"
        self.assertEqual(run(self).turn_status, "completed")

    def test_new_archives_the_previous_thread(self) -> None:
        first = run(self, request_id="req-1")
        self.watch()
        fresh = run(self, request_id="req-2", ack_request_id="req-1", force_new=True)
        self.assertNotEqual(fresh.record.thread_id, first.record.thread_id)
        archived = [c for c in self.seen() if c.get("method") == "thread/archive"]
        self.assertEqual(archived[0]["params"]["threadId"], first.record.thread_id)
        self.assertTrue(self.journal()["threads"][first.record.thread_id]["archived"])

    def test_keep_old_leaves_it(self) -> None:
        run(self, request_id="req-1")
        self.watch()
        run(self, request_id="req-2", ack_request_id="req-1", force_new=True, keep_old=True)
        self.assertEqual([c for c in self.seen() if c.get("method") == "thread/archive"], [])

    def test_new_resolves_a_quarantine_block(self) -> None:
        st.state_dir().mkdir(parents=True, exist_ok=True)
        st.state_path(HOST).write_text("broken", encoding="utf-8")
        with self.assertRaises(st.StateCorrupt):
            run(self)
        with self.assertRaises(st.StateBlocked):
            run(self)
        outcome = run(self, force_new=True)
        self.assertTrue(outcome.created_thread)
        self.assertFalse(st.blocked_path(HOST).exists())

    def test_a_held_thread_is_forked_with_its_history(self) -> None:
        """The human opened the thread in Desktop between phases."""
        first = run(self, request_id="req-1")
        os.environ["FAKE_CODEX_MODE"] = "busy"
        os.environ["FAKE_CODEX_BUSY_ID"] = first.record.thread_id
        os.environ["FAKE_CODEX_FORK_ID"] = "01a0-forked"
        self.watch()
        second = run(self, request_id="req-2", ack_request_id="req-1", phase=consult.REVIEW)
        self.assertEqual(second.record.thread_id, "01a0-forked")
        self.assertEqual(second.record.forked_from, first.record.thread_id)
        # History travelled: the fork holds the design turn plus the new one.
        self.assertEqual(len(self.turns_of("01a0-forked")), 2)
        # The held original cannot be archived while held; it is owed.
        self.assertIn(first.record.thread_id, second.record.archive_debt)
        self.assertEqual(second.record.phase("design").rounds, 1)

    def test_the_creation_sandbox_follows_the_thread_into_resume_and_fork(self) -> None:
        """Codex does not restore an unnamed sandbox; duo sends it again."""
        first = run(self, request_id="req-1",
                    thread_options=options_mod.ThreadOptions(cwd=self.tmp, model="gpt-5.6-luna",
                                                             sandbox="read-only"))
        self.assertEqual(first.record.sandbox, "read-only")
        self.watch()
        run(self, request_id="req-2", ack_request_id="req-1")
        resume = next(c for c in self.seen() if c.get("method") == "thread/resume")
        self.assertEqual(resume["params"]["sandbox"], "read-only")
        os.environ["FAKE_CODEX_MODE"] = "busy"
        os.environ["FAKE_CODEX_BUSY_ID"] = first.record.thread_id
        run(self, request_id="req-3", ack_request_id="req-2")
        fork = next(c for c in self.seen() if c.get("method") == "thread/fork")
        self.assertEqual(fork["params"]["sandbox"], "read-only")

    def test_no_fork_fails_loudly(self) -> None:
        first = run(self, request_id="req-1")
        os.environ["FAKE_CODEX_MODE"] = "busy"
        os.environ["FAKE_CODEX_BUSY_ID"] = first.record.thread_id
        with self.assertRaises(consult.ThreadHeld) as caught:
            run(self, request_id="req-2", ack_request_id="req-1", fork_on_busy=False)
        self.assertIn("Codex Desktop", str(caught.exception))

    def test_resume_uses_the_threads_own_cwd(self) -> None:
        run(self, request_id="req-1")
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        self.watch()
        profile = consult.resolve_profile("test")
        consult.run_round(
            claude_session_id=SESSION, host_session_id=HOST, phase=consult.DESIGN, message="ещё",
            thread_options=options_mod.ThreadOptions(cwd=elsewhere, model=profile.model),
            turn_options=options_mod.TurnOptions(model=profile.model, effort=profile.effort),
            profile_name=profile.name, request_id="req-2", ack_request_id="req-1",
            reveal=False, timeout=30, idle_timeout=30,
        )
        resumes = [c for c in self.seen() if c.get("method") == "thread/resume"]
        self.assertEqual(resumes[-1]["params"]["cwd"], str(self.tmp))

    def test_reveal_works_on_a_later_round(self) -> None:
        calls: list[str] = []
        original = consult.reveal_in_desktop
        consult.reveal_in_desktop = lambda tid: calls.append(tid) or True  # type: ignore[assignment]
        try:
            run(self, request_id="req-1")
            outcome = run(self, request_id="req-2", ack_request_id="req-1", reveal=True)
        finally:
            consult.reveal_in_desktop = original  # type: ignore[assignment]
        self.assertTrue(outcome.revealed)
        self.assertEqual(calls, [outcome.record.thread_id])


# =========================================================================
# crash windows: kill the CLI at a named point, rerun, assert recovery
# =========================================================================


class TestCrashWindows(EnvSandbox):
    def cli(self, *args: str, crash_at: str | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if crash_at:
            env["DUO_TEST_CRASH_AT"] = crash_at
        else:
            env.pop("DUO_TEST_CRASH_AT", None)
        return subprocess.run(
            [sys.executable, str(PACKAGE), "ask", "--phase", "design", "--profile", "test",
             "--message", "x", "--json", *args],
            capture_output=True, text=True, cwd=str(self.tmp), env=env, timeout=120, check=False,
        )

    def visible_threads(self) -> list[str]:
        """What the sidebar would show: asked over the wire, not read off the journal."""
        with appserver.AppServer(Path(os.environ["DUO_CODEX_BIN"]), self.tmp) as server:
            server.initialize()
            rows = server.request("thread/list", {"limit": 50, "modelProviders": [], "sourceKinds": []})
        return [r["id"] for r in rows.get("data", [])]

    def all_turns(self) -> int:
        return sum(len(t.get("turns", [])) for t in self.journal().get("threads", {}).values())

    def test_crash_after_intent_before_thread_start(self) -> None:
        dead = self.cli("--request-id", "req-1", crash_at="after_creating_intent")
        self.assertEqual(dead.returncode, 137)
        self.assertEqual(self.visible_threads(), [])
        alive = self.cli("--request-id", "req-1")
        self.assertEqual(alive.returncode, 0, alive.stderr)
        self.assertEqual(len(self.visible_threads()), 1)
        self.assertEqual(self.all_turns(), 1)

    def test_crash_after_thread_start_leaves_an_invisible_row_and_no_duplicate_turn(self) -> None:
        """The weakened invariant, version-pinned against the fake's listing."""
        dead = self.cli("--request-id", "req-1", crash_at="after_thread_start_response")
        self.assertEqual(dead.returncode, 137)
        # A thread exists in the journal but has no turn: invisible in thread/list.
        self.assertEqual(self.visible_threads(), [])
        self.assertEqual(len(self.journal()["threads"]), 1)
        alive = self.cli("--request-id", "req-1")
        self.assertEqual(alive.returncode, 0, alive.stderr)
        self.assertEqual(len(self.visible_threads()), 1)
        self.assertEqual(self.all_turns(), 1)

    def test_crash_after_open_request_before_turn(self) -> None:
        dead = self.cli("--request-id", "req-1", crash_at="after_open_request")
        self.assertEqual(dead.returncode, 137)
        self.assertEqual(self.all_turns(), 0)
        alive = self.cli("--request-id", "req-1")
        self.assertEqual(alive.returncode, 0, alive.stderr)
        self.assertEqual(self.all_turns(), 1)

    def test_crash_after_save_before_stdout_replays_instead_of_resending(self) -> None:
        """The delivery window: result durable, caller never saw it."""
        dead = self.cli("--request-id", "req-1", crash_at="before_stdout")
        self.assertEqual(dead.returncode, 137)
        self.assertEqual(self.all_turns(), 1)
        alive = self.cli("--request-id", "req-1")
        self.assertEqual(alive.returncode, 0, alive.stderr)
        payload = json.loads(alive.stdout)
        self.assertTrue(payload["replayed"])
        self.assertEqual(self.all_turns(), 1)
        self.assertEqual(payload["round"], 1)

    def test_crash_after_save_replays_with_the_decision(self) -> None:
        """Result stored, phase counted, stdout never happened: one save did both."""
        os.environ["FAKE_CODEX_VERDICT"] = verdict_json()
        first = self.cli("--request-id", "req-1")
        self.assertEqual(first.returncode, 0, first.stderr)
        dead = self.cli("--request-id", "req-2", "--ack-request-id", "req-1", crash_at="after_save")
        self.assertEqual(dead.returncode, 137)
        alive = self.cli("--request-id", "req-2")
        self.assertEqual(alive.returncode, 0, alive.stderr)
        payload = json.loads(alive.stdout)
        self.assertTrue(payload["replayed"])
        self.assertTrue(payload["finished"])
        self.assertEqual(payload["outcome"], CONVERGED)
        self.assertEqual(payload["round"], 2)
        self.assertEqual(self.all_turns(), 2)

    def test_server_accepted_but_client_died_recovers_from_history(self) -> None:
        os.environ["FAKE_CODEX_MODE"] = "accept_then_die"
        dead = self.cli("--request-id", "req-1")
        self.assertNotEqual(dead.returncode, 0)
        self.assertEqual(self.all_turns(), 1)
        os.environ["FAKE_CODEX_MODE"] = "ok"
        alive = self.cli("--request-id", "req-1")
        self.assertEqual(alive.returncode, 0, alive.stderr)
        payload = json.loads(alive.stdout)
        self.assertTrue(payload["resumed"])
        self.assertEqual(self.all_turns(), 1)

    def _accepted_then_a_human_turn(self) -> str:
        """Our request accepted and lost; the human then took a turn of their own."""
        os.environ["FAKE_CODEX_MODE"] = "accept_then_die"
        dead = self.cli("--request-id", "req-1")
        self.assertNotEqual(dead.returncode, 0)
        os.environ["FAKE_CODEX_MODE"] = "ok"
        data = self.journal()
        thread_id = next(iter(data["threads"]))
        data["threads"][thread_id]["turns"].append(
            {"id": "t-human", "client_id": None, "input": "человек", "status": "completed",
             "result": verdict_json()})
        (self.tmp / "journal.json").write_text(json.dumps(data), encoding="utf-8")
        os.environ["FAKE_CODEX_PAGE_SIZE"] = "1"   # ours is on the second page
        return thread_id

    def test_an_accepted_turn_behind_a_newer_one_is_found_not_resent(self) -> None:
        self._accepted_then_a_human_turn()
        alive = self.cli("--request-id", "req-1")
        self.assertEqual(alive.returncode, 0, alive.stderr)
        self.assertTrue(json.loads(alive.stdout)["resumed"])
        self.assertEqual(self.all_turns(), 2, "запрос отправлен второй раз")

    def test_a_malformed_history_page_is_an_error_not_not_found(self) -> None:
        class Page(appserver.AppServer):
            def __init__(self, page: object) -> None:
                self.page = page

            def request(self, method: str, params: dict) -> object:
                return self.page

        for page in ({"data": [None], "nextCursor": None},
                     {"data": [{"id": "t", "status": "completed", "items": "broken"}], "nextCursor": None},
                     {"data": [], "nextCursor": 5},
                     {"nextCursor": None}):
            with self.subTest(page=page), self.assertRaises(appserver.AppServerError):
                Page(page).find_turn("01a0-thread", "req-1")

    def test_waiting_stays_on_the_turn_found_first(self) -> None:
        def turn(turn_id: str, status: str, text: str) -> dict:
            items = [{"type": "userMessage", "clientId": "req-1"}]
            if status == "completed":
                items.append({"type": "agentMessage", "text": text, "phase": "final_answer"})
            return {"id": turn_id, "status": status, "items": items}

        class Pages(appserver.AppServer):
            def __init__(self) -> None:
                self.pages = [
                    {"data": [turn("A", "inProgress", "")], "nextCursor": None},
                    # B shows up with the same clientId, newer than A.
                    {"data": [turn("B", "completed", "ответ B"), turn("A", "completed", "ответ A")],
                     "nextCursor": None},
                ]

            def request(self, method: str, params: dict) -> object:
                return self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]

        record = make_state()
        record.open_request = st.OpenRequest(request_id="req-1", phase="design", message_sha256="x",
                                             status="pending", started_at=st.now_iso())
        with unittest.mock.patch("time.sleep"):
            payload = consult._recover_from_history(Pages(), record, idle_timeout=60)
        assert payload is not None
        self.assertEqual(payload["turn_id"], "A")
        self.assertEqual(payload["final_text"], "ответ A")

    def test_a_failed_history_page_keeps_the_request_pending(self) -> None:
        self._accepted_then_a_human_turn()
        os.environ["FAKE_CODEX_TURNS_FAIL_PAST_FIRST"] = "1"
        broken = self.cli("--request-id", "req-1")
        self.assertNotEqual(broken.returncode, 0)
        self.assertEqual(self.all_turns(), 2, "ошибка чтения истории сочтена за «не принят»")
        record = st.load(HOST)
        assert record is not None and record.open_request is not None
        self.assertEqual(record.open_request.request_id, "req-1")

    def test_a_creation_resumed_after_a_crash_keeps_its_sandbox(self) -> None:
        dead = self.cli("--request-id", "req-1", "--sandbox", "read-only", crash_at="after_creating_intent")
        self.assertEqual(dead.returncode, 137)
        self.watch()
        alive = self.cli("--request-id", "req-1")   # the same request, without the flag
        self.assertEqual(alive.returncode, 0, alive.stderr)
        start = next(c["params"] for c in self.seen() if c.get("method") == "thread/start")
        self.assertEqual(start.get("sandbox"), "read-only", "повтор создания потерял песочницу")
        record = st.load(HOST)
        assert record is not None
        self.assertEqual(record.sandbox, "read-only")

    def test_new_crash_after_intent_keeps_the_old_thread_owed(self) -> None:
        """previous_binding is read back: T1 is archived once T2 is confirmed."""
        first = self.cli("--request-id", "req-1")
        self.assertEqual(first.returncode, 0, first.stderr)
        t1 = json.loads(first.stdout)["thread_id"]
        dead = self.cli("--request-id", "req-2", "--ack-request-id", "req-1", "--new", crash_at="after_creating_intent")
        self.assertEqual(dead.returncode, 137)
        alive = self.cli("--request-id", "req-2", "--new")
        self.assertEqual(alive.returncode, 0, alive.stderr)
        t2 = json.loads(alive.stdout)["thread_id"]
        self.assertNotEqual(t1, t2)
        self.assertTrue(self.journal()["threads"][t1]["archived"])
        self.assertEqual(self.visible_threads(), [t2])

    def test_fork_crash_after_response_leaves_nothing_visible(self) -> None:
        first = self.cli("--request-id", "req-1")
        t1 = json.loads(first.stdout)["thread_id"]
        os.environ["FAKE_CODEX_MODE"] = "busy"
        os.environ["FAKE_CODEX_BUSY_ID"] = t1
        os.environ["FAKE_CODEX_FORK_ID"] = "01a0-stray-fork"
        dead = self.cli("--request-id", "req-2", "--ack-request-id", "req-1", crash_at="after_fork_response")
        self.assertEqual(dead.returncode, 137)
        # The fork holds the history and takes no listing entry until its own
        # turn, so this window leaves nothing for anyone to find.
        self.assertNotIn("01a0-stray-fork", self.visible_threads())
        os.environ["FAKE_CODEX_FORK_ID"] = "01a0-real-fork"
        alive = self.cli("--request-id", "req-2")
        self.assertEqual(alive.returncode, 0, alive.stderr)
        self.assertEqual(json.loads(alive.stdout)["thread_id"], "01a0-real-fork")
        self.assertNotIn("01a0-stray-fork", self.visible_threads())

    def test_migration_crash_after_marking_source_finishes_next_run(self) -> None:
        st.save(SESSION, make_state(key=SESSION, thread_id="01a0-legacy"))
        journal = {"threads": {"01a0-legacy": {"id": "01a0-legacy", "turns": [
            {"id": "t0", "client_id": None, "input": "x", "status": "completed", "result": verdict_json()}]}}}
        (self.tmp / "journal.json").write_text(json.dumps(journal), encoding="utf-8")
        dead = self.cli("--request-id", "req-1", crash_at="after_migration_mark")
        self.assertEqual(dead.returncode, 137)
        alive = self.cli("--request-id", "req-1")
        self.assertEqual(alive.returncode, 0, alive.stderr)
        self.assertFalse(st.state_path(SESSION).exists())
        loaded = st.load(HOST)
        assert loaded is not None
        self.assertEqual(loaded.thread_id, "01a0-legacy")

    def test_migration_crash_after_destination_keeps_one_binding(self) -> None:
        st.save(SESSION, make_state(key=SESSION, thread_id="01a0-legacy"))
        journal = {"threads": {"01a0-legacy": {"id": "01a0-legacy", "turns": [
            {"id": "t0", "client_id": None, "input": "x", "status": "completed", "result": verdict_json()}]}}}
        (self.tmp / "journal.json").write_text(json.dumps(journal), encoding="utf-8")
        dead = self.cli("--request-id", "req-1", crash_at="after_migration_dst")
        self.assertEqual(dead.returncode, 137)
        self.assertTrue(st.state_path(HOST).exists())
        alive = self.cli("--request-id", "req-1")
        self.assertEqual(alive.returncode, 0, alive.stderr)
        self.assertFalse(st.state_path(SESSION).exists())

    def test_the_fake_is_pinned_to_the_verified_codex_version(self) -> None:
        self.assertIn(os.environ.get("FAKE_CODEX_VERSION", "0.153.0-alpha.5"), config.VERIFIED_CODEX_VERSIONS)

    def test_accepted_turn_then_desktop_holds_the_thread_does_not_duplicate(self) -> None:
        """Codex's composite scenario from the review: accepted turn, lost
        result, then Desktop opens the original before the retry. The retry
        must fork and find the turn in the fork's history, not resend it."""
        os.environ["FAKE_CODEX_MODE"] = "accept_then_die"
        dead = self.cli("--request-id", "req-1")
        self.assertNotEqual(dead.returncode, 0)
        self.assertEqual(self.all_turns(), 1)
        os.environ["FAKE_CODEX_MODE"] = "busy_after_accept"
        os.environ["FAKE_CODEX_FORK_ID"] = "01a0-fork-after-accept"
        alive = self.cli("--request-id", "req-1")
        self.assertEqual(alive.returncode, 0, alive.stderr)
        payload = json.loads(alive.stdout)
        self.assertTrue(payload["resumed"])
        self.assertEqual(payload["thread_id"], "01a0-fork-after-accept")
        # One accepted turn, copied into the fork — never a second one.
        self.assertEqual(len(self.turns_of("01a0-fork-after-accept")), 1)
        self.assertEqual(sum(1 for t in self.journal()["threads"].values() for x in t["turns"] if x.get("client_id") == "req-1"), 2)

    def test_a_lost_request_id_cannot_start_a_second_turn(self) -> None:
        """The caller restarted and forgot the id: the state still refuses."""
        dead = self.cli("--request-id", "req-1", crash_at="before_stdout")
        self.assertEqual(dead.returncode, 137)
        other = self.cli("--request-id", "req-2")
        self.assertEqual(other.returncode, 7)
        self.assertIn("req-1", other.stderr)
        self.assertEqual(self.all_turns(), 1)


# =========================================================================
# CLI surface
# =========================================================================


class TestCli(EnvSandbox):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PACKAGE), *args], capture_output=True, text=True,
            cwd=str(self.tmp), env=os.environ.copy(), timeout=120, check=False,
        )

    def dry(self, *args: str) -> dict:
        done = self.run_cli("ask", "--phase", "design", "--message", "тест", "--dry-run", *args)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def call(self, payload: dict, method: str) -> dict:
        for entry in payload["calls"]:
            if entry.get("method") == method:
                return entry
        raise AssertionError(f"{method} not in {payload}")

    def test_schema_is_strict(self) -> None:
        payload = json.loads(self.run_cli("schema").stdout)

        def strict(node: object) -> None:
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertIs(node.get("additionalProperties"), False)
                    self.assertEqual(sorted(node.get("properties") or {}), sorted(node.get("required") or []))
                for v in node.values():
                    strict(v)
            elif isinstance(node, list):
                for v in node:
                    strict(v)

        strict(payload)

    def test_verify_shows_the_key(self) -> None:
        done = self.run_cli("verify")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(f"state key         {HOST}", done.stdout)

    def test_dry_run_spends_nothing_and_shows_the_id(self) -> None:
        payload = self.dry("--request-id", "r1")
        self.assertEqual(payload["request_id"], "r1")
        self.assertEqual(self.call(payload, "turn/start")["params"]["clientUserMessageId"], "r1")
        self.assertIsNone(st.load(HOST))

    def test_spoken_model_and_effort_precedence(self) -> None:
        turn = self.call(self.dry("--model", "5.6 luna light", "--effort", "high"), "turn/start")["params"]
        self.assertEqual((turn["model"], turn["effort"]), ("gpt-5.6-luna", "high"))

    def test_context_window_is_a_config_override(self) -> None:
        start = self.call(self.dry("--context-window", "200000"), "thread/start")["params"]
        self.assertEqual(start["config"]["model_context_window"], 200000)

    def test_trailer_is_the_default_and_json_is_available(self) -> None:
        self.assertNotIn("outputSchema", self.call(self.dry(), "turn/start")["params"])
        self.assertEqual(self.call(self.dry("--verdict-format", "json"), "turn/start")["params"]["outputSchema"]["type"], "object")

    def test_two_rounds_via_cli_with_ack(self) -> None:
        os.environ["FAKE_CODEX_VERDICT"] = verdict_json()
        first = self.run_cli("ask", "--phase", "design", "--message", "x", "--profile", "test", "--json", "--request-id", "a")
        self.assertEqual(first.returncode, 0, first.stderr)
        blocked = self.run_cli("ask", "--phase", "design", "--message", "x", "--profile", "test", "--json", "--request-id", "b")
        self.assertEqual(blocked.returncode, 7)
        second = self.run_cli("ask", "--phase", "design", "--message", "x", "--profile", "test", "--json",
                              "--request-id", "b", "--ack-request-id", "a")
        self.assertEqual(second.returncode, 0, second.stderr)
        payload = json.loads(second.stdout)
        self.assertTrue(payload["finished"])
        self.assertEqual(payload["outcome"], CONVERGED)

    def test_ack_command(self) -> None:
        self.run_cli("ask", "--phase", "design", "--message", "x", "--profile", "test", "--request-id", "a")
        done = self.run_cli("ack", "a")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.run_cli("ack", "a").returncode, 2)

    def test_status_shows_the_open_request(self) -> None:
        self.run_cli("ask", "--phase", "design", "--message", "x", "--profile", "test", "--request-id", "a")
        payload = json.loads(self.run_cli("status", "--json").stdout)
        self.assertEqual(payload["open_request"]["request_id"], "a")
        self.assertEqual(payload["state_key"], HOST)

    def test_recover_pulls_an_old_key(self) -> None:
        old = "33333333-4444-4555-8666-777777777777"
        st.save(old, make_state(key=old, thread_id="01a0-old"))
        journal = {"threads": {"01a0-old": {"id": "01a0-old", "turns": [
            {"id": "t0", "client_id": None, "input": "x", "status": "completed", "result": verdict_json()}]}}}
        (self.tmp / "journal.json").write_text(json.dumps(journal), encoding="utf-8")
        done = self.run_cli("recover", "--from", old)
        self.assertEqual(done.returncode, 0, done.stderr)
        loaded = st.load(HOST)
        assert loaded is not None
        self.assertEqual(loaded.thread_id, "01a0-old")

    def test_close_forgets_and_can_archive(self) -> None:
        self.run_cli("ask", "--phase", "design", "--message", "x", "--profile", "test", "--request-id", "a")
        done = self.run_cli("close", "--archive")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIsNone(st.load(HOST))

    def test_close_keeps_the_tombstone(self) -> None:
        st.state_dir().mkdir(parents=True, exist_ok=True)
        st.state_path(HOST).write_text("broken", encoding="utf-8")
        self.assertEqual(self.run_cli("status").returncode, 9)
        self.assertEqual(self.run_cli("close").returncode, 0)
        self.assertTrue(st.blocked_path(HOST).exists())
        self.assertEqual(self.run_cli("close", "--force").returncode, 0)
        self.assertFalse(st.blocked_path(HOST).exists())

    def test_recover_into_a_blocked_key(self) -> None:
        old = "33333333-4444-4555-8666-777777777777"
        st.save(old, make_state(key=old, thread_id="01a0-old"))
        st.state_dir().mkdir(parents=True, exist_ok=True)
        st.state_path(HOST).write_text("broken", encoding="utf-8")
        self.assertEqual(self.run_cli("status").returncode, 9)
        done = self.run_cli("recover", "--from", old)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(st.blocked_path(HOST).exists())
        loaded = st.load(HOST)
        assert loaded is not None
        self.assertEqual(loaded.thread_id, "01a0-old")

    def test_the_lock_is_reported_not_ignored(self) -> None:
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import sys,time; sys.path.insert(0, sys.argv[1]); import state as st\n"
             "with st.lock_many([sys.argv[2], sys.argv[3]]):\n"
             "    print('held', flush=True); time.sleep(5)",
             str(PACKAGE), HOST, SESSION],
            stdout=subprocess.PIPE, text=True, env=os.environ.copy(),
        )
        try:
            assert holder.stdout is not None
            self.assertEqual(holder.stdout.readline().strip(), "held")
            done = self.run_cli("ask", "--phase", "design", "--message", "x", "--profile", "test")
            self.assertEqual(done.returncode, 8)
            self.assertIn("in use", done.stderr)
        finally:
            holder.kill()
            holder.wait()
            if holder.stdout is not None:
                holder.stdout.close()


# =========================================================================
# review round 2: proven fork ownership, refused redirects, honest failures
# =========================================================================


class TestPermissionsInherit(EnvSandbox):
    """The thread runs with the user's own rights unless a flag says otherwise."""

    def dry(self, *args: str) -> dict:
        done = subprocess.run(
            [sys.executable, str(PACKAGE), "ask", "--phase", "design", "--profile", "test",
             "--message", "x", "--dry-run", *args],
            capture_output=True, text=True, cwd=str(self.tmp), env=os.environ.copy(),
            timeout=60, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def start_params(self, payload: dict) -> dict:
        return next(c["params"] for c in payload["calls"] if c.get("method") == "thread/start")

    def test_by_default_no_sandbox_or_policy_is_sent(self) -> None:
        params = self.start_params(self.dry())
        for key in ("sandbox", "approvalPolicy", "approvalsReviewer", "permissions"):
            self.assertNotIn(key, params, f"{key} навязан вопреки конфигу пользователя")

    def test_the_sandbox_flag_still_pins_the_box(self) -> None:
        params = self.start_params(self.dry("--sandbox", "read-only"))
        self.assertEqual(params["sandbox"], "read-only")

    def test_flags_nobody_passed_are_gone(self) -> None:
        for flag in ("--turn-sandbox", "--approval-policy", "--no-output-schema", "-c"):
            done = subprocess.run(
                [sys.executable, str(PACKAGE), "ask", "--phase", "design", "--profile", "test",
                 "--message", "x", "--dry-run", flag, "x"],
                capture_output=True, text=True, cwd=str(self.tmp), env=os.environ.copy(),
                timeout=60, check=False)
            self.assertEqual(done.returncode, 2, flag)


class TestForkOwnership(EnvSandbox):
    """What a fork actually costs, measured on 0.153.4 and mirrored by the fake."""

    def cli(self, *args: str, crash_at: str | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if crash_at:
            env["DUO_TEST_CRASH_AT"] = crash_at
        else:
            env.pop("DUO_TEST_CRASH_AT", None)
        return subprocess.run(
            [sys.executable, str(PACKAGE), "ask", "--phase", "design", "--profile", "test",
             "--message", "x", "--json", *args],
            capture_output=True, text=True, cwd=str(self.tmp), env=env, timeout=120, check=False,
        )

    def visible_threads(self) -> list[str]:
        """Every page of what the sidebar would show, asked over the wire."""
        found: list[str] = []
        cursor = None
        with appserver.AppServer(Path(os.environ["DUO_CODEX_BIN"]), self.tmp) as server:
            server.initialize()
            for _ in range(20):
                params: dict = {"limit": 100, "modelProviders": [], "sourceKinds": []}
                if cursor:
                    params["cursor"] = cursor
                page = server.request("thread/list", params)
                found += [r["id"] for r in page.get("data") or []]
                cursor = page.get("nextCursor")
                if not cursor:
                    break
        return found

    def plant_foreign_fork(self, source: str, fork_id: str = "01a0-user-fork") -> str:
        """A fork the human made in Desktop and has been working in."""
        data = self.journal()
        data["threads"][fork_id] = {
            "id": fork_id, "name": "Имя чата", "archived": False, "ephemeral": False,
            "cwd": str(self.tmp), "forked_from": source, "created_at": 1, "listed": True,
            "turns": [dict(t) for t in data["threads"][source]["turns"]],
        }
        (self.tmp / "journal.json").write_text(json.dumps(data), encoding="utf-8")
        return fork_id

    def hold(self, thread_id: str) -> None:
        os.environ["FAKE_CODEX_MODE"] = "busy"
        os.environ["FAKE_CODEX_BUSY_ID"] = thread_id

    def archived(self, thread_id: str) -> bool:
        return bool(self.journal()["threads"][thread_id].get("archived"))

    def setUp(self) -> None:
        super().setUp()
        for name in ("FAKE_CODEX_LIST_FAILS", "FAKE_CODEX_PAGE_SIZE", "FAKE_CODEX_FORK_ID",
                     "FAKE_CODEX_BUSY_ID"):
            os.environ.pop(name, None)

    def test_a_fork_is_invisible_until_it_takes_a_turn(self) -> None:
        """The measured property the whole crash story rests on."""
        first = self.cli("--request-id", "req-1")
        self.assertEqual(first.returncode, 0, first.stderr)
        source = json.loads(first.stdout)["thread_id"]
        self.hold(source)
        os.environ["FAKE_CODEX_FORK_ID"] = "01a0-stray"

        dead = self.cli("--request-id", "req-2", "--ack-request-id", "req-1",
                        crash_at="after_fork_response")
        self.assertEqual(dead.returncode, 137)
        # The fork exists and holds the history, and nobody can see it.
        self.assertEqual(len(self.turns_of("01a0-stray")), 1)
        self.assertNotIn("01a0-stray", self.visible_threads())

    def test_a_stray_fork_is_left_invisible_not_archived(self) -> None:
        first = self.cli("--request-id", "req-1")
        source = json.loads(first.stdout)["thread_id"]
        self.hold(source)
        os.environ["FAKE_CODEX_FORK_ID"] = "01a0-stray"
        self.assertEqual(
            self.cli("--request-id", "req-2", "--ack-request-id", "req-1",
                     crash_at="after_fork_response").returncode, 137)

        os.environ["FAKE_CODEX_FORK_ID"] = "01a0-second"
        alive = self.cli("--request-id", "req-2", "--ack-request-id", "req-1")
        self.assertEqual(alive.returncode, 0, alive.stderr)
        self.assertEqual(json.loads(alive.stdout)["thread_id"], "01a0-second")
        self.assertFalse(self.archived("01a0-stray"), "невидимый форк зачем-то архивируется")
        self.assertNotIn("01a0-stray", self.visible_threads())
        # The held original stays in the sidebar until Desktop lets go; the
        # second fork is the only thread this round added.
        self.assertEqual(sorted(self.visible_threads()), sorted(["01a0-second", source]))
        self.assertEqual(len(self.turns_of("01a0-second")), 2)

    def test_a_fork_the_human_made_is_never_touched(self) -> None:
        first = self.cli("--request-id", "req-1")
        source = json.loads(first.stdout)["thread_id"]
        foreign = self.plant_foreign_fork(source)
        self.hold(source)
        os.environ["FAKE_CODEX_FORK_ID"] = "01a0-ours"

        alive = self.cli("--request-id", "req-2", "--ack-request-id", "req-1")
        self.assertEqual(alive.returncode, 0, alive.stderr)
        self.assertFalse(self.archived(foreign), "чужой форк заархивирован")
        self.assertIn(foreign, self.visible_threads())

    def test_the_held_original_is_owed_an_archive_across_pages(self) -> None:
        """Debt survives, and the listing the helpers read is paginated."""
        first = self.cli("--request-id", "req-1")
        source = json.loads(first.stdout)["thread_id"]
        for i in range(4):
            self.plant_foreign_fork(source, f"01a0-noise-{i}")
        os.environ["FAKE_CODEX_PAGE_SIZE"] = "2"
        self.hold(source)
        os.environ["FAKE_CODEX_FORK_ID"] = "01a0-ours"

        alive = self.cli("--request-id", "req-2", "--ack-request-id", "req-1")
        self.assertEqual(alive.returncode, 0, alive.stderr)
        record = st.load(HOST)
        assert record is not None
        self.assertIn(source, record.archive_debt)
        self.assertFalse(self.archived(source), "удерживаемый тред нельзя архивировать")
        listing = self.visible_threads()
        self.assertEqual(len(listing), 6, listing)


class TestHonestFailures(EnvSandbox):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PACKAGE), *args], capture_output=True, text=True,
            cwd=str(self.tmp), env=os.environ.copy(), timeout=120, check=False,
        )

    def test_a_still_running_turn_exits_ten_and_keeps_the_request(self) -> None:
        """Waiting is not interrupting: nothing is resent, nothing is stopped."""
        record = make_state(thread_id="01a0-thread")
        record.cwd = str(self.tmp)
        record.open_request = st.OpenRequest(
            request_id="req-1", phase="design",
            message_sha256=consult.sha256(consult.frame_message("design", "x")),
            status=st.REQUEST_PENDING, started_at=st.now_iso(),
        )
        st.save(HOST, record)
        self.journal_write({"threads": {"01a0-thread": {
            "id": "01a0-thread", "name": "Имя чата", "archived": False, "ephemeral": False,
            "cwd": str(self.tmp), "created_at": 1,
            "turns": [{"id": "t1", "client_id": "req-1", "input": "x", "status": "inProgress"}]}}})
        os.environ["FAKE_CODEX_SEEN"] = str(self.tmp / "seen.json")

        done = self.run_cli("ask", "--phase", "design", "--profile", "test", "--message", "x",
                            "--idle-timeout", "1", "--request-id", "req-1")
        self.assertEqual(done.returncode, 10, done.stderr + done.stdout)
        methods = [c["method"] for c in json.loads((self.tmp / "seen.json").read_text())]
        self.assertNotIn("turn/start", methods)
        self.assertNotIn("turn/interrupt", methods)
        kept = st.load(HOST)
        assert kept is not None and kept.open_request is not None
        self.assertEqual(kept.open_request.status, st.REQUEST_PENDING)

    def journal_write(self, data: dict) -> None:
        (self.tmp / "journal.json").write_text(json.dumps(data), encoding="utf-8")

    def test_close_archive_keeps_the_binding_when_the_server_refuses(self) -> None:
        first = subprocess.run(
            [sys.executable, str(PACKAGE), "ask", "--phase", "design", "--profile", "test",
             "--message", "x", "--json"],
            capture_output=True, text=True, cwd=str(self.tmp), env=os.environ.copy(),
            timeout=120, check=False)
        self.assertEqual(first.returncode, 0, first.stderr)
        thread_id = json.loads(first.stdout)["thread_id"]
        os.environ["FAKE_CODEX_MODE"] = "busy"
        os.environ["FAKE_CODEX_BUSY_ID"] = thread_id

        done = self.run_cli("close", "--archive")
        self.assertNotEqual(done.returncode, 0, "отказ архивации сообщён как успех")
        self.assertTrue(st.state_path(HOST).exists(), "привязка потеряна после отказа")
        self.assertFalse(self.journal()["threads"][thread_id].get("archived"))

    def test_new_over_an_unconfirmed_creation_owes_both_threads(self) -> None:
        """--new twice across a crash: neither old thread is forgotten."""
        first = subprocess.run(
            [sys.executable, str(PACKAGE), "ask", "--phase", "design", "--profile", "test",
             "--message", "x", "--json"],
            capture_output=True, text=True, cwd=str(self.tmp), env=os.environ.copy(),
            timeout=120, check=False)
        original = json.loads(first.stdout)["thread_id"]
        first_id = json.loads(first.stdout)["request_id"]

        env = os.environ.copy()
        env["DUO_TEST_CRASH_AT"] = "after_thread_start_response"
        env["FAKE_CODEX_THREAD_ID"] = "01a0-half-made"
        dead = subprocess.run(
            [sys.executable, str(PACKAGE), "ask", "--phase", "design", "--profile", "test",
             "--message", "x", "--json", "--new", "--request-id", "req-2",
             "--ack-request-id", first_id],
            capture_output=True, text=True, cwd=str(self.tmp), env=env, timeout=120, check=False)
        self.assertEqual(dead.returncode, 137)

        env = os.environ.copy()
        env["FAKE_CODEX_THREAD_ID"] = "01a0-third"
        alive = subprocess.run(
            [sys.executable, str(PACKAGE), "ask", "--phase", "design", "--profile", "test",
             "--message", "x", "--json", "--new", "--request-id", "req-3"],
            capture_output=True, text=True, cwd=str(self.tmp), env=env, timeout=120, check=False)
        self.assertEqual(alive.returncode, 0, alive.stderr)
        threads = self.journal()["threads"]
        self.assertTrue(threads[original].get("archived"), "первый тред остался в sidebar")
        self.assertEqual(json.loads(alive.stdout)["thread_id"], "01a0-third")
        live = [t for t in threads.values() if t.get("turns") and not t.get("archived")]
        self.assertEqual([t["id"] for t in live], ["01a0-third"])


class TestMigrationRedirect(EnvSandbox):
    def test_a_started_move_cannot_be_pointed_somewhere_else(self) -> None:
        source = make_state(key=SESSION, thread_id="01a0-one")
        source.migrating = {"migration_id": "m1", "destination_key": HOST}
        st.save(SESSION, source)
        with st.lock_many([SESSION, "local_other"]):
            with self.assertRaises(st.StateError) as caught:
                st.migrate(SESSION, "local_other")
        self.assertIn("mid-move", str(caught.exception))

    def test_a_malformed_migrating_record_is_quarantined(self) -> None:
        record = make_state()
        payload = record.to_json()
        payload["migrating"] = {"destination_key": HOST}
        st.state_dir().mkdir(parents=True, exist_ok=True)
        st.state_path(HOST).write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(st.StateCorrupt):
            st.load(HOST)

    def test_a_string_archive_debt_is_quarantined(self) -> None:
        record = make_state()
        payload = record.to_json()
        payload["archive_debt"] = "01a0-one"
        st.state_dir().mkdir(parents=True, exist_ok=True)
        st.state_path(HOST).write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(st.StateCorrupt):
            st.load(HOST)



# =========================================================================
# review round 3: creation intent, migration chains, strict v2, close debt
# =========================================================================


class TestRoundThreeFixes(EnvSandbox):
    def run_cli(self, *args: str, crash_at: str | None = None,
                thread_id: str | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if crash_at:
            env["DUO_TEST_CRASH_AT"] = crash_at
        else:
            env.pop("DUO_TEST_CRASH_AT", None)
        if thread_id:
            env["FAKE_CODEX_THREAD_ID"] = thread_id
        else:
            env.pop("FAKE_CODEX_THREAD_ID", None)
        return subprocess.run(
            [sys.executable, str(PACKAGE), *args], capture_output=True, text=True,
            cwd=str(self.tmp), env=env, timeout=120, check=False,
        )

    def ask(self, *args: str, **kw) -> subprocess.CompletedProcess[str]:
        return self.run_cli("ask", "--phase", "design", "--profile", "test",
                            "--message", "x", "--json", *args, **kw)

    def test_a_plain_round_after_a_failed_new_still_owes_the_old_thread(self) -> None:
        """Codex round 3: the next round need not carry --new to inherit the debt."""
        first = self.ask("--request-id", "req-1")
        self.assertEqual(first.returncode, 0, first.stderr)
        t1 = json.loads(first.stdout)["thread_id"]

        dead = self.ask("--request-id", "req-2", "--ack-request-id", "req-1", "--new",
                        crash_at="after_creating_intent")
        self.assertEqual(dead.returncode, 137)

        plain = self.ask("--request-id", "req-3", thread_id="01a0-third")
        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertEqual(json.loads(plain.stdout)["thread_id"], "01a0-third")
        self.assertTrue(self.journal()["threads"][t1]["archived"],
                        "тред, который просили оставить, потерян")

    def test_a_retired_key_redirects_instead_of_being_recreated(self) -> None:
        """A→B, then B→C, then a stale retry of A→B must not resurrect B."""
        st.save(SESSION, make_state(key=SESSION, thread_id="01a0-one"))
        with st.lock_many([SESSION, HOST]):
            st.migrate(SESSION, HOST)
        with st.lock_many([HOST, "local_third"]):
            st.migrate(HOST, "local_third")
        self.assertEqual(st.moved_target(SESSION), HOST)
        self.assertEqual(st.moved_target(HOST), "local_third")
        self.assertEqual(st.migration_chain(SESSION), [HOST, "local_third"])

        # An older process still holds the A→B move and runs it again.
        stale = make_state(key=SESSION, thread_id="01a0-one")
        stale.migrating = {"migration_id": "m-old", "destination_key": HOST}
        st.save(SESSION, stale)
        with st.lock_many([SESSION, HOST, "local_third"]):
            landed = st.migrate(SESSION, HOST)
        self.assertEqual(landed.state_key, "local_third")
        self.assertIsNone(st.load(HOST), "перенесённый ключ воскрес второй привязкой")
        self.assertIsNone(st.load(SESSION))

    def test_a_numeric_thread_id_is_quarantined_not_read_as_absent(self) -> None:
        payload = make_state().to_json()
        payload["thread_id"] = 0
        st.state_dir().mkdir(parents=True, exist_ok=True)
        st.state_path(HOST).write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(st.StateCorrupt):
            st.load(HOST)

    def test_a_numeric_request_id_is_quarantined(self) -> None:
        record = make_state()
        record.open_request = st.OpenRequest(
            request_id="req-1", phase="design", message_sha256="deadbeef",
            status=st.REQUEST_PENDING, started_at=st.now_iso(),
        )
        payload = record.to_json()
        payload["open_request"]["request_id"] = 17
        st.state_dir().mkdir(parents=True, exist_ok=True)
        st.state_path(HOST).write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(st.StateCorrupt):
            st.load(HOST)

    def test_a_null_archive_debt_is_quarantined_but_an_absent_one_is_not(self) -> None:
        payload = make_state().to_json()
        payload["archive_debt"] = None
        st.state_dir().mkdir(parents=True, exist_ok=True)
        st.state_path(HOST).write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(st.StateCorrupt):
            st.load(HOST)

        st.blocked_path(HOST).unlink()
        payload.pop("archive_debt")
        st.state_path(HOST).write_text(json.dumps(payload), encoding="utf-8")
        loaded = st.load(HOST)
        assert loaded is not None
        self.assertEqual(loaded.archive_debt, [])

    def test_a_v1_file_is_still_read_leniently(self) -> None:
        """Strictness is for v2. A legacy file was written by looser code."""
        payload = {
            "version": 1, "claude_session_id": SESSION, "thread_id": "01a0-legacy",
            "thread_name": "Имя", "cwd": "/tmp", "profile": "test",
            "phases": {"design": {"rounds": 1, "stop_streak": 0, "unresolved_ids": []}},
        }
        st.state_dir().mkdir(parents=True, exist_ok=True)
        st.state_path(SESSION).write_text(json.dumps(payload), encoding="utf-8")
        loaded = st.load(SESSION)
        assert loaded is not None
        self.assertEqual(loaded.thread_id, "01a0-legacy")

    def test_close_archive_settles_the_debt_before_forgetting(self) -> None:
        first = self.ask("--request-id", "req-1", thread_id="01a0-current")
        self.assertEqual(first.returncode, 0, first.stderr)
        record = st.load(HOST)
        assert record is not None
        # A thread an earlier round could not archive, still owed.
        self.journal_plant("01a0-owed")
        record.archive_debt = ["01a0-owed"]
        st.save(HOST, record)

        done = self.run_cli("close", "--archive")
        self.assertEqual(done.returncode, 0, done.stderr)
        threads = self.journal()["threads"]
        self.assertTrue(threads["01a0-current"]["archived"])
        self.assertTrue(threads["01a0-owed"]["archived"], "долг забыт вместе с привязкой")
        self.assertFalse(st.state_path(HOST).exists())

    def test_close_archive_keeps_everything_when_a_debt_cannot_be_paid(self) -> None:
        first = self.ask("--request-id", "req-1", thread_id="01a0-current")
        self.assertEqual(first.returncode, 0, first.stderr)
        record = st.load(HOST)
        assert record is not None
        self.journal_plant("01a0-held")
        record.archive_debt = ["01a0-held"]
        st.save(HOST, record)
        os.environ["FAKE_CODEX_MODE"] = "busy"
        os.environ["FAKE_CODEX_BUSY_ID"] = "01a0-held"

        done = self.run_cli("close", "--archive")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("01a0-held", done.stderr)
        kept = st.load(HOST)
        assert kept is not None
        self.assertEqual(kept.archive_debt, ["01a0-held"])
        self.assertFalse(self.journal()["threads"]["01a0-current"]["archived"],
                         "текущий тред заархивирован, хотя close не завершился")

    def journal_plant(self, thread_id: str) -> None:
        data = self.journal()
        data["threads"][thread_id] = {
            "id": thread_id, "name": "старый", "archived": False, "ephemeral": False,
            "cwd": str(self.tmp), "created_at": 1, "listed": True,
            "turns": [{"id": "t0", "client_id": None, "input": "x", "status": "completed",
                       "result": verdict_json()}],
        }
        (self.tmp / "journal.json").write_text(json.dumps(data), encoding="utf-8")



# =========================================================================
# review round 4: one canonical key, closing pays every debt, fail closed
# =========================================================================


class TestRoundFourFixes(EnvSandbox):
    THIRD = "local_cccccccc-dddd-4eee-8fff-999999999999"

    def run_cli(self, *args: str, host: str | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if host is not None:
            env["CLAUDE_CODE_HOST_SESSION_ID"] = host
        return subprocess.run(
            [sys.executable, str(PACKAGE), *args], capture_output=True, text=True,
            cwd=str(self.tmp), env=env, timeout=120, check=False,
        )

    def ask(self, *args: str, host: str | None = None) -> subprocess.CompletedProcess[str]:
        return self.run_cli("ask", "--phase", "design", "--profile", "test",
                            "--message", "x", "--json", *args, host=host)

    def test_recover_lands_the_binding_where_the_move_ended(self) -> None:
        """Codex round 4: A→B, B→C, then recover A→B must not resurrect B."""
        st.save(SESSION, make_state(key=SESSION, thread_id="01a0-one"))
        with st.lock_many([SESSION, HOST]):
            st.migrate(SESSION, HOST)
        with st.lock_many([HOST, self.THIRD]):
            st.migrate(HOST, self.THIRD)
        # An old CLI id shows up again, still pointing at the first key.
        st.save(SESSION, make_state(key=SESSION, thread_id="01a0-one"))

        landed = consult.recover(SESSION, HOST, SESSION)
        self.assertEqual(landed.state_key, self.THIRD)
        self.assertIsNone(st.load(HOST), "промежуточный ключ воскрес второй привязкой")
        loaded = st.load(self.THIRD)
        assert loaded is not None
        self.assertEqual(loaded.thread_id, "01a0-one")

    def test_a_plain_round_follows_the_redirect_instead_of_starting_over(self) -> None:
        """A round under a migrated-away key must not open a second thread."""
        first = self.ask("--request-id", "req-1")
        self.assertEqual(first.returncode, 0, first.stderr)
        thread_id = json.loads(first.stdout)["thread_id"]
        with st.lock_many([HOST, self.THIRD]):
            st.migrate(HOST, self.THIRD)

        again = self.ask("--request-id", "req-2", "--ack-request-id", "req-1")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(json.loads(again.stdout)["thread_id"], thread_id,
                         "раунд под перенесённым ключом открыл новый тред")
        self.assertIsNone(st.load(HOST))
        landed = st.load(self.THIRD)
        assert landed is not None
        self.assertEqual(landed.phase("design").rounds, 2)

    def test_close_pays_the_thread_hiding_in_an_unconfirmed_creation(self) -> None:
        first = self.ask("--request-id", "req-1")
        t1 = json.loads(first.stdout)["thread_id"]
        env = os.environ.copy()
        env["DUO_TEST_CRASH_AT"] = "after_creating_intent"
        dead = subprocess.run(
            [sys.executable, str(PACKAGE), "ask", "--phase", "design", "--profile", "test",
             "--message", "x", "--json", "--new", "--request-id", "req-2",
             "--ack-request-id", "req-1"],
            capture_output=True, text=True, cwd=str(self.tmp), env=env, timeout=120, check=False)
        self.assertEqual(dead.returncode, 137)
        record = st.load(HOST)
        assert record is not None and record.creating is not None
        self.assertEqual((record.creating.previous_binding or {}).get("thread_id"), t1)

        done = self.run_cli("close", "--archive")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(self.journal()["threads"][t1]["archived"],
                        "тред из неподтверждённого создания забыт при закрытии")

    def test_an_empty_thread_id_is_quarantined(self) -> None:
        payload = make_state().to_json()
        payload["thread_id"] = ""
        st.state_dir().mkdir(parents=True, exist_ok=True)
        st.state_path(HOST).write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(st.StateCorrupt):
            st.load(HOST)

    def test_an_unreadable_redirect_is_a_fault_not_an_absence(self) -> None:
        st.save(SESSION, make_state(key=SESSION, thread_id="01a0-one"))
        with st.lock_many([SESSION, HOST]):
            st.migrate(SESSION, HOST)
        st.moved_path(SESSION).write_text("{ not json", encoding="utf-8")
        with self.assertRaises(st.StateUnreadable):
            st.moved_target(SESSION)
        with self.assertRaises(st.StateUnreadable):
            st.migration_chain(SESSION)

    def test_a_redirect_naming_nothing_is_a_fault(self) -> None:
        st.state_dir().mkdir(parents=True, exist_ok=True)
        st.moved_path(SESSION).write_text('{"at": "now"}', encoding="utf-8")
        with self.assertRaises(st.StateUnreadable):
            st.moved_target(SESSION)



# =========================================================================
# review round 5: the route is finished under lock, and repairable
# =========================================================================


class TestRoundFiveFixes(EnvSandbox):
    THIRD = "local_cccccccc-dddd-4eee-8fff-999999999999"

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PACKAGE), *args], capture_output=True, text=True,
            cwd=str(self.tmp), env=os.environ.copy(), timeout=120, check=False,
        )

    def ask(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self.run_cli("ask", "--phase", "design", "--profile", "test",
                            "--message", "x", "--json", *args)

    def test_a_move_marked_but_never_written_is_finished_not_ignored(self) -> None:
        """Codex round 5: crash after the source is marked, before the write."""
        first = self.ask("--request-id", "req-1")
        self.assertEqual(first.returncode, 0, first.stderr)
        thread_id = json.loads(first.stdout)["thread_id"]
        # The state is marked for a move whose destination was never written.
        record = st.load(HOST)
        assert record is not None
        record.migrating = {"migration_id": "m1", "destination_key": self.THIRD}
        st.save(HOST, record)
        self.assertFalse(st.state_path(self.THIRD).exists())

        again = self.ask("--request-id", "req-2", "--ack-request-id", "req-1")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(json.loads(again.stdout)["thread_id"], thread_id,
                         "недописанный перенос прочитан как отсутствие сессии")
        landed = st.load(self.THIRD)
        assert landed is not None
        self.assertEqual(landed.phase("design").rounds, 2)
        self.assertEqual(len(self.journal()["threads"]), 1)

    def test_a_loop_of_redirects_is_an_error_not_a_truncated_answer(self) -> None:
        st.state_dir().mkdir(parents=True, exist_ok=True)
        st.repair_redirect(HOST, SESSION)
        st.repair_redirect(SESSION, HOST)
        with self.assertRaises(st.StateError) as caught:
            st.migration_chain(HOST)
        self.assertIn("loop", str(caught.exception))

    def test_a_chain_past_the_limit_is_an_error(self) -> None:
        st.state_dir().mkdir(parents=True, exist_ok=True)
        keys = [f"local_chain-{i:02d}" for i in range(st.MAX_MIGRATION_CHAIN + 3)]
        for here, nxt in zip(keys, keys[1:]):
            st.repair_redirect(here, nxt)
        with self.assertRaises(st.StateError) as caught:
            st.migration_chain(keys[0])
        self.assertIn("longer than", str(caught.exception))

    def test_a_broken_redirect_can_be_set_aside_and_work_resumes(self) -> None:
        first = self.ask("--request-id", "req-1")
        self.assertEqual(first.returncode, 0, first.stderr)
        thread_id = json.loads(first.stdout)["thread_id"]
        st.moved_path(HOST).write_text("{ not json", encoding="utf-8")

        blocked = self.ask("--request-id", "req-2", "--ack-request-id", "req-1")
        self.assertEqual(blocked.returncode, 9, blocked.stderr)

        fixed = self.run_cli("repair-redirect", HOST)
        self.assertEqual(fixed.returncode, 0, fixed.stderr)
        again = self.ask("--request-id", "req-2", "--ack-request-id", "req-1")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(json.loads(again.stdout)["thread_id"], thread_id)

    def test_a_redirect_can_be_pointed_at_the_key_that_holds_the_state(self) -> None:
        st.save(self.THIRD, make_state(key=self.THIRD, thread_id="01a0-moved"))
        st.moved_path(HOST).write_text("{ broken", encoding="utf-8")
        done = self.run_cli("repair-redirect", HOST, "--to", self.THIRD)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(st.moved_target(HOST), self.THIRD)

        status = self.run_cli("status", "--json")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)["thread_id"], "01a0-moved")

    def test_a_redirect_to_nowhere_is_refused(self) -> None:
        done = self.run_cli("repair-redirect", HOST, "--to", self.THIRD)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("holds no state", done.stderr)



# =========================================================================
# review round 6: strict reads under lock, and a repair that cannot lose a thread
# =========================================================================


class TestRoundSixFixes(EnvSandbox):
    THIRD = "local_cccccccc-dddd-4eee-8fff-999999999999"

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PACKAGE), *args], capture_output=True, text=True,
            cwd=str(self.tmp), env=os.environ.copy(), timeout=120, check=False,
        )

    def ask(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self.run_cli("ask", "--phase", "design", "--profile", "test",
                            "--message", "x", "--json", *args)

    def test_an_unreadable_source_stops_the_command_instead_of_starting_over(self) -> None:
        """Codex round 6: under lock, a read error must not mean 'no move pending'."""
        first = self.ask("--request-id", "req-1")
        self.assertEqual(first.returncode, 0, first.stderr)
        record = st.load(HOST)
        assert record is not None
        record.migrating = {"migration_id": "m1", "destination_key": self.THIRD}
        st.save(HOST, record)
        # The marked source is now unreadable, and its destination absent.
        st.state_path(HOST).write_text("{ broken", encoding="utf-8")

        again = self.ask("--request-id", "req-2", "--ack-request-id", "req-1")
        self.assertEqual(again.returncode, 9, again.stderr)
        self.assertEqual(len(self.journal()["threads"]), 1,
                         "нечитаемый источник переноса привёл ко второму треду")

    def test_a_redirect_pointing_at_its_own_key_is_a_fault(self) -> None:
        st.state_dir().mkdir(parents=True, exist_ok=True)
        st.moved_path(HOST).write_text(
            json.dumps({"destination_key": HOST, "at": "now"}), encoding="utf-8")
        with self.assertRaises(st.StateError):
            st.moved_target(HOST)

    def test_close_finishes_a_half_done_move_before_forgetting(self) -> None:
        first = self.ask("--request-id", "req-1")
        thread_id = json.loads(first.stdout)["thread_id"]
        record = st.load(HOST)
        assert record is not None
        record.migrating = {"migration_id": "m1", "destination_key": self.THIRD}
        st.save(HOST, record)

        done = self.run_cli("close")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIsNone(st.load(self.THIRD), "перенос не был доведён до конца перед закрытием")
        self.assertIsNone(st.load(HOST))

        after = self.ask("--request-id", "req-2")
        self.assertEqual(after.returncode, 0, after.stderr)
        self.assertNotEqual(json.loads(after.stdout)["thread_id"], thread_id,
                            "закрытая привязка воскресла")

    def test_recover_accepts_a_move_that_already_landed_here(self) -> None:
        st.save(SESSION, make_state(key=SESSION, thread_id="01a0-one"))
        with st.lock_many([SESSION, HOST]):
            st.migrate(SESSION, HOST)
        # An old CLI-id file reappears, marked for a move that is already done.
        stale = make_state(key=SESSION, thread_id="01a0-one")
        stale.migrating = {"migration_id": "m-old", "destination_key": HOST}
        st.save(SESSION, stale)

        landed = consult.recover(SESSION, HOST, SESSION)
        self.assertEqual(landed.state_key, HOST)
        self.assertEqual(landed.thread_id, "01a0-one")

    def test_dropping_a_redirect_with_no_local_binding_blocks_instead(self) -> None:
        """The duplicate Codex reproduced through the repair command itself."""
        first = self.ask("--request-id", "req-1")
        thread_id = json.loads(first.stdout)["thread_id"]
        with st.lock_many([HOST, self.THIRD]):
            st.migrate(HOST, self.THIRD)
        st.moved_path(HOST).write_text("{ broken", encoding="utf-8")

        dropped = self.run_cli("repair-redirect", HOST)
        self.assertNotEqual(dropped.returncode, 0)
        self.assertIn("--to", dropped.stderr)
        blocked = self.ask("--request-id", "req-2", "--ack-request-id", "req-1")
        self.assertEqual(blocked.returncode, 9, blocked.stderr)
        self.assertEqual(len(self.journal()["threads"]), 1, "починка открыла второй тред")

        fixed = self.run_cli("repair-redirect", HOST, "--to", self.THIRD)
        self.assertEqual(fixed.returncode, 0, fixed.stderr)
        again = self.ask("--request-id", "req-2", "--ack-request-id", "req-1", "--new")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(len(self.journal()["threads"]), 2)

    def test_a_redirect_cannot_hide_a_live_binding(self) -> None:
        st.save(HOST, make_state(key=HOST, thread_id="01a0-live"))
        st.save(self.THIRD, make_state(key=self.THIRD, thread_id="01a0-other"))
        done = self.run_cli("repair-redirect", HOST, "--to", self.THIRD)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("01a0-live", done.stderr)

    def test_a_repair_cannot_build_a_loop(self) -> None:
        st.save(self.THIRD, make_state(key=self.THIRD, thread_id="01a0-one"))
        st.repair_redirect(self.THIRD, HOST)
        st.save(HOST, make_state(key=HOST, thread_id="01a0-one"))
        done = self.run_cli("repair-redirect", HOST, "--to", self.THIRD)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("loop", done.stderr)



# =========================================================================
# review round 7: a quarantined route refuses, a repair hides nothing
# =========================================================================


class TestRoundSevenFixes(EnvSandbox):
    THIRD = "local_cccccccc-dddd-4eee-8fff-999999999999"

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PACKAGE), *args], capture_output=True, text=True,
            cwd=str(self.tmp), env=os.environ.copy(), timeout=120, check=False,
        )

    def ask(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self.run_cli("ask", "--phase", "design", "--profile", "test",
                            "--message", "x", "--json", *args)

    def test_two_runs_after_a_quarantined_source_still_refuse(self) -> None:
        """Codex round 7: the second run must not walk past the block."""
        first = self.ask("--request-id", "req-1")
        self.assertEqual(first.returncode, 0, first.stderr)
        record = st.load(HOST)
        assert record is not None
        record.migrating = {"migration_id": "m1", "destination_key": self.THIRD}
        st.save(HOST, record)
        st.state_path(HOST).write_text("{ broken", encoding="utf-8")

        one = self.ask("--request-id", "req-2", "--ack-request-id", "req-1")
        self.assertEqual(one.returncode, 9, one.stderr)
        two = self.ask("--request-id", "req-2", "--ack-request-id", "req-1")
        self.assertEqual(two.returncode, 9, two.stderr + two.stdout)
        self.assertEqual(len(self.journal()["threads"]), 1,
                         "второй запуск после карантина создал второй тред")

    def test_new_gets_past_a_quarantined_route_on_its_own(self) -> None:
        first = self.ask("--request-id", "req-1")
        self.assertEqual(first.returncode, 0, first.stderr)
        st.state_path(HOST).write_text("{ broken", encoding="utf-8")
        self.assertEqual(self.ask("--request-id", "req-2").returncode, 9)

        fresh = self.ask("--request-id", "req-3", "--new")
        self.assertEqual(fresh.returncode, 0, fresh.stderr)
        self.assertEqual(len(self.journal()["threads"]), 2)

    def test_a_repair_cannot_hide_an_unconfirmed_creations_debt(self) -> None:
        first = self.ask("--request-id", "req-1")
        t1 = json.loads(first.stdout)["thread_id"]
        env = os.environ.copy()
        env["DUO_TEST_CRASH_AT"] = "after_creating_intent"
        dead = subprocess.run(
            [sys.executable, str(PACKAGE), "ask", "--phase", "design", "--profile", "test",
             "--message", "x", "--json", "--new", "--request-id", "req-2",
             "--ack-request-id", "req-1"],
            capture_output=True, text=True, cwd=str(self.tmp), env=env, timeout=120, check=False)
        self.assertEqual(dead.returncode, 137)
        st.save(self.THIRD, make_state(key=self.THIRD, thread_id="01a0-other"))

        done = self.run_cli("repair-redirect", HOST, "--to", self.THIRD)
        self.assertNotEqual(done.returncode, 0, "редирект скрыл тред из неподтверждённого создания")
        self.assertIn(t1, done.stderr)

    def test_a_repair_cannot_hide_an_archive_debt(self) -> None:
        self.ask("--request-id", "req-1")
        record = st.load(HOST)
        assert record is not None
        record.archive_debt = ["01a0-owed"]
        st.save(HOST, record)
        st.save(self.THIRD, make_state(key=self.THIRD, thread_id=record.thread_id))

        done = self.run_cli("repair-redirect", HOST, "--to", self.THIRD)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("01a0-owed", done.stderr)

    def test_an_unreadable_local_state_blocks_the_repair(self) -> None:
        st.save(self.THIRD, make_state(key=self.THIRD, thread_id="01a0-one"))
        st.state_dir().mkdir(parents=True, exist_ok=True)
        st.state_path(HOST).write_text("{ broken", encoding="utf-8")
        done = self.run_cli("repair-redirect", HOST, "--to", self.THIRD)
        self.assertNotEqual(done.returncode, 0, "испорченное состояние спрятано под редирект")



# =========================================================================
# review round 8: no write lands on a key this process does not hold
# =========================================================================


class TestHeldLocks(EnvSandbox):
    THIRD = "local_cccccccc-dddd-4eee-8fff-999999999999"

    def test_migrate_refuses_a_key_it_does_not_hold(self) -> None:
        st.save(SESSION, make_state(key=SESSION, thread_id="01a0-one"))
        with self.assertRaises(st.StateError) as caught:
            st.migrate(SESSION, HOST)
        self.assertIn("without holding the lock", str(caught.exception))

    def test_migrate_refuses_a_forward_target_it_does_not_hold(self) -> None:
        """Codex round 8: the race that put a binding on an unlocked key."""
        st.save(SESSION, make_state(key=SESSION, thread_id="01a0-one"))
        with st.lock_many([SESSION, HOST]):
            st.migrate(SESSION, HOST)
        with st.lock_many([HOST, self.THIRD]):
            st.migrate(HOST, self.THIRD)
        # A stale process still holds only the first two keys and tries again.
        st.save(SESSION, make_state(key=SESSION, thread_id="01a0-one"))
        with st.lock_many([SESSION, HOST]):
            with self.assertRaises(st.StateError) as caught:
                st.migrate(SESSION, HOST)
        self.assertIn(self.THIRD, str(caught.exception))
        landed = st.load(self.THIRD)
        assert landed is not None
        self.assertEqual(landed.thread_id, "01a0-one")

    def test_the_registry_empties_when_the_lock_is_released(self) -> None:
        with st.lock_many([HOST, SESSION]):
            self.assertEqual(st.held_keys(), frozenset({HOST, SESSION}))
        self.assertEqual(st.held_keys(), frozenset())

    def test_recover_holds_the_destination_it_ends_up_writing(self) -> None:
        """Codex round 8, step by step: the key moved on and was then closed."""
        st.save(HOST, make_state(key=HOST, thread_id="01a0-two"))
        with st.lock_many([HOST, self.THIRD]):
            st.migrate(HOST, self.THIRD)
        st.discard(self.THIRD)          # `close` under the key it moved to
        st.save(SESSION, make_state(key=SESSION, thread_id="01a0-one"))

        landed = consult.recover(SESSION, HOST, SESSION)
        self.assertEqual(landed.state_key, self.THIRD,
                         "восстановление записало не туда, куда ведёт маршрут")
        self.assertEqual(landed.thread_id, "01a0-one")
        self.assertIsNone(st.load(HOST))



# =========================================================================
# review round 9: the settlement sees both sides, the guard runs first
# =========================================================================


class TestRoundNineFixes(EnvSandbox):
    OLD = "local_cccccccc-dddd-4eee-8fff-999999999999"

    def test_recover_finishes_a_move_marked_on_a_key_it_was_given(self) -> None:
        """Codex round 9: --from names a key outside the session's own route."""
        marked = make_state(key=self.OLD, thread_id="01a0-one")
        marked.migrating = {"migration_id": "m1", "destination_key": HOST}
        st.save(self.OLD, marked)
        self.assertIsNone(st.load(HOST))

        landed = consult.recover(SESSION, HOST, self.OLD)
        self.assertEqual(landed.state_key, HOST)
        self.assertEqual(landed.thread_id, "01a0-one")
        self.assertIsNone(st.load(self.OLD))

        # And again: the second run finds it already home, not an error.
        again = consult.recover(SESSION, HOST, self.OLD)
        self.assertEqual(again.thread_id, "01a0-one")

    def test_migrate_checks_the_lock_before_it_can_quarantine(self) -> None:
        st.state_dir().mkdir(parents=True, exist_ok=True)
        st.state_path(SESSION).write_text("{ broken", encoding="utf-8")
        with self.assertRaises(st.StateError) as caught:
            st.migrate(SESSION, HOST)
        self.assertIn("without holding the lock", str(caught.exception))
        self.assertFalse(st.blocked_path(SESSION).exists(),
                         "карантин записан вызывающим без блокировки")
        self.assertTrue(st.state_path(SESSION).exists(), "файл источника переименован без блокировки")



# =========================================================================
# the verdict surface: a value outside the contract must not read as milder
# =========================================================================


class TestVerdictEdges(EnvSandbox):
    def verdict_with(self, **finding) -> verdict_mod.Verdict:
        base = {"id": "a", "severity": "p1", "claim": "c", "evidence": "e",
                "response": "new", "rationale": "r"}
        base.update(finding)
        payload = {"stance": "agree", "stop": True, "confidence": "high", "summary": "s",
                   "findings": [base], "open_questions": []}
        return verdict_mod.parse("Проза.\n\n```json\n" + json.dumps(payload) + "\n```")

    def test_a_severity_outside_the_scale_still_blocks(self) -> None:
        parsed = self.verdict_with(severity="critical")
        self.assertEqual(len(parsed.blocking_findings), 1,
                         "severity вне шкалы понижена до необязательной")
        self.assertFalse(parsed.wants_stop)

    def test_a_response_outside_the_contract_stays_unresolved(self) -> None:
        parsed = self.verdict_with(response="maybe")
        self.assertTrue(parsed.findings[0].is_unresolved)
        self.assertFalse(parsed.wants_stop)

    def test_stop_is_refused_while_a_blocking_finding_stands(self) -> None:
        parsed = self.verdict_with()
        self.assertFalse(parsed.wants_stop, "stop принят вместе с блокирующим возражением")
        decision = verdict_mod.decide(parsed, round_number=1, stop_streak=0,
                                      previous_unresolved=[], max_rounds=3, stop_hold=2)
        self.assertFalse(decision.finished)

    def test_a_repeated_id_is_counted_once_for_the_stopping_rule(self) -> None:
        payload = {"stance": "disagree", "stop": False, "confidence": "high", "summary": "s",
                   "findings": [
                       {"id": "a", "severity": "p1", "claim": "c", "evidence": "e",
                        "response": "new", "rationale": "r"},
                       {"id": "a", "severity": "p1", "claim": "c2", "evidence": "e",
                        "response": "dispute", "rationale": "r"}],
                   "open_questions": []}
        parsed = verdict_mod.parse("Проза.\n\n```json\n" + json.dumps(payload) + "\n```")
        self.assertEqual(parsed.unresolved_ids(), frozenset({"a"}),
                         "повторённый id посчитан дважды")
        decision = verdict_mod.decide(parsed, round_number=2, stop_streak=0,
                                      previous_unresolved=["a"], max_rounds=3, stop_hold=2)
        self.assertFalse(decision.finished)



# =========================================================================
# the brief on the way in: encoding, size, and a thread that lives elsewhere
# =========================================================================


class TestBriefHandling(EnvSandbox):
    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PACKAGE), "ask", "--phase", "design", "--profile", "test",
             "--json", *args],
            capture_output=True, text=True, cwd=str(self.tmp), env=os.environ.copy(),
            timeout=120, check=False,
        )

    def test_a_brief_in_another_encoding_is_explained_not_a_traceback(self) -> None:
        path = self.tmp / "cp1251.md"
        path.write_bytes("## Задача\n\nБриф.\n".encode("cp1251"))
        done = self.cli("--message-file", str(path), "--request-id", "req-1")
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("not UTF-8", done.stderr)
        self.assertNotIn("Traceback", done.stderr)

    def test_a_byte_order_mark_does_not_travel_into_the_message(self) -> None:
        path = self.tmp / "bom.md"
        path.write_bytes(b"\xef\xbb\xbf" + "## Задача\n\nБриф.\n".encode("utf-8"))
        done = self.cli("--message-file", str(path), "--request-id", "req-1")
        self.assertEqual(done.returncode, 0, done.stderr)
        sent = next(iter(self.journal()["threads"].values()))["turns"][0]["input"]
        self.assertNotIn("\ufeff", sent)

    def test_a_brief_past_the_limit_is_refused_with_the_numbers(self) -> None:
        path = self.tmp / "big.md"
        path.write_text("x" * 130_000, encoding="utf-8")
        done = self.cli("--message-file", str(path), "--request-id", "req-1")
        self.assertEqual(done.returncode, 2)
        self.assertIn("130000", done.stderr)
        self.assertIn("120000", done.stderr)
        self.assertEqual(self.journal().get("threads", {}), {})

    def test_a_round_from_another_directory_stays_in_the_same_thread(self) -> None:
        first = self.cli("--message", "x", "--request-id", "req-1")
        self.assertEqual(first.returncode, 0, first.stderr)
        thread_id = json.loads(first.stdout)["thread_id"]
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        done = subprocess.run(
            [sys.executable, str(PACKAGE), "ask", "--phase", "design", "--profile", "test",
             "--json", "--message", "y", "--request-id", "req-2", "--ack-request-id", "req-1"],
            capture_output=True, text=True, cwd=str(elsewhere), env=os.environ.copy(),
            timeout=120, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["thread_id"], thread_id)
        # A subdirectory of the thread's own root is the everyday case once
        # threads are filed under a project: nothing to warn about.
        self.assertNotIn("resuming there", done.stderr)

    def test_a_round_from_outside_the_root_says_where_the_thread_lives(self) -> None:
        first = self.cli("--message", "x", "--request-id", "req-1")
        self.assertEqual(first.returncode, 0, first.stderr)
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside, True)
        done = subprocess.run(
            [sys.executable, str(PACKAGE), "ask", "--phase", "design", "--profile", "test",
             "--json", "--message", "y", "--request-id", "req-2", "--ack-request-id", "req-1"],
            capture_output=True, text=True, cwd=str(outside), env=os.environ.copy(),
            timeout=120, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("resuming there", done.stderr)



# =========================================================================
# the holding round: a malformed verdict is never a clean stop
# =========================================================================


class TestVerdictFailClosed(EnvSandbox):
    def parse_payload(self, payload: dict) -> verdict_mod.Verdict:
        return verdict_mod.parse("Проза.\n\n```json\n" + json.dumps(payload) + "\n```")

    def base(self, **over) -> dict:
        payload = {"stance": "agree", "stop": True, "confidence": "high", "summary": "s",
                   "findings": [], "open_questions": []}
        payload.update(over)
        return payload

    def test_a_string_stop_is_not_a_stop(self) -> None:
        parsed = self.parse_payload(self.base(stop="false"))
        self.assertFalse(parsed.parsed, "строковый stop принят как булев")
        self.assertIn("stop", str(parsed.parse_error))

    def test_findings_as_an_object_is_a_parse_error_not_an_empty_list(self) -> None:
        payload = self.base(findings={"id": "a", "severity": "p1", "claim": "c",
                                      "evidence": "e", "response": "new", "rationale": "r"})
        parsed = self.parse_payload(payload)
        self.assertFalse(parsed.parsed, "возражения молча потеряны")
        decision = verdict_mod.decide(parsed, round_number=2, stop_streak=1,
                                      previous_unresolved=[], max_rounds=3, stop_hold=2)
        self.assertNotEqual(decision.outcome, verdict_mod.CONVERGED)

    def test_a_finding_that_is_not_an_object_is_a_parse_error(self) -> None:
        parsed = self.parse_payload(self.base(findings=["p1: сломается"]))
        self.assertFalse(parsed.parsed)

    def test_a_malformed_round_breaks_the_agreement_streak(self) -> None:
        """One clean round, then a broken one: the phase must not converge."""
        clean = self.parse_payload(self.base())
        first = verdict_mod.decide(clean, round_number=1, stop_streak=0,
                                   previous_unresolved=[], max_rounds=3, stop_hold=2)
        self.assertEqual(first.stop_streak, 1)
        broken = self.parse_payload(self.base(stop="true"))
        second = verdict_mod.decide(broken, round_number=2, stop_streak=first.stop_streak,
                                    previous_unresolved=[], max_rounds=3, stop_hold=2)
        self.assertEqual(second.stop_streak, 0, "серия согласий пережила нечитаемый вердикт")
        self.assertNotEqual(second.outcome, verdict_mod.CONVERGED)


class TestMissingDirectory(EnvSandbox):
    def ask(self, cwd, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PACKAGE), "ask", "--phase", "design", "--profile", "test",
             "--json", *args],
            capture_output=True, text=True, cwd=str(cwd), env=os.environ.copy(),
            timeout=120, check=False)

    def test_a_thread_whose_directory_vanished_continues_where_it_is(self) -> None:
        repo = self.tmp / "repo"
        repo.mkdir()
        first = self.ask(repo, "--message", "x", "--request-id", "req-1")
        self.assertEqual(first.returncode, 0, first.stderr)
        thread_id = json.loads(first.stdout)["thread_id"]

        shutil.rmtree(repo)
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        done = self.ask(elsewhere, "--message", "y", "--request-id", "req-2",
                        "--ack-request-id", "req-1")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("no longer exists", done.stderr)
        self.assertEqual(json.loads(done.stdout)["thread_id"], thread_id)
        record = st.load(HOST)
        assert record is not None
        self.assertEqual(Path(record.cwd).resolve(), elsewhere.resolve())


class TestForkKeepsTheDirectory(EnvSandbox):
    def test_a_fork_uses_the_threads_directory_not_the_current_one(self) -> None:
        """Codex's holding round: busy plus a changed directory."""
        self.watch()
        first = subprocess.run(
            [sys.executable, str(PACKAGE), "ask", "--phase", "design", "--profile", "test",
             "--message", "x", "--json", "--request-id", "req-1"],
            capture_output=True, text=True, cwd=str(self.tmp), env=os.environ.copy(),
            timeout=120, check=False)
        self.assertEqual(first.returncode, 0, first.stderr)
        thread_id = json.loads(first.stdout)["thread_id"]

        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        os.environ["FAKE_CODEX_MODE"] = "busy"
        os.environ["FAKE_CODEX_BUSY_ID"] = thread_id
        os.environ["FAKE_CODEX_FORK_ID"] = "01a0-forked-elsewhere"
        done = subprocess.run(
            [sys.executable, str(PACKAGE), "ask", "--phase", "design", "--profile", "test",
             "--message", "y", "--json", "--request-id", "req-2", "--ack-request-id", "req-1"],
            capture_output=True, text=True, cwd=str(elsewhere), env=os.environ.copy(),
            timeout=120, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        forks = [c for c in self.seen() if c.get("method") == "thread/fork"]
        self.assertEqual(len(forks), 1, forks)
        self.assertEqual(Path(forks[0]["params"]["cwd"]).resolve(), self.tmp.resolve(),
                         "форк ушёл в каталог запуска, а не треда")



# =========================================================================
# review round 12: the verdict surface, where a fault must never read as agreement
# =========================================================================


class TestVerdictContract(EnvSandbox):
    def full(self, **over) -> dict:
        payload = {"stance": "agree", "stop": True, "confidence": "high", "summary": "s",
                   "findings": [], "open_questions": []}
        payload.update(over)
        return payload

    def parsed(self, payload: dict) -> verdict_mod.Verdict:
        return verdict_mod.parse("Проза.\n\n```json\n" + json.dumps(payload) + "\n```")

    def test_null_findings_is_a_parse_error_not_an_empty_list(self) -> None:
        v = self.parsed(self.full(findings=None))
        self.assertFalse(v.parsed, "findings: null принят за отсутствие возражений")

    def test_a_missing_decision_field_is_a_parse_error(self) -> None:
        """The three fields whose absence could pass for "no objections"."""
        for name in ("stance", "stop", "findings"):
            for payload in (self.full(), self.full(**{name: None})):
                payload.pop(name, None) if payload is not None else None
                with self.subTest(missing=name, form="absent" if name not in payload else "null"):
                    v = self.parsed(payload)
                    self.assertFalse(v.parsed, f"вердикт без {name} принят")
                    self.assertIn(name, str(v.parse_error))

    def test_a_missing_prose_field_is_not_a_parse_error(self) -> None:
        """Measured against every real answer in the live thread: Codex omits
        `summary` when it agrees, and that verdict is still a verdict."""
        for name in ("confidence", "summary", "open_questions"):
            payload = self.full()
            payload.pop(name)
            with self.subTest(missing=name):
                v = self.parsed(payload)
                self.assertTrue(v.parsed, f"вердикт без {name} отвергнут: {v.parse_error}")

    def test_a_prose_field_of_the_wrong_type_is_still_a_parse_error(self) -> None:
        v = self.parsed(self.full(summary=42))
        self.assertFalse(v.parsed)

    def test_a_json_block_in_the_middle_is_not_the_verdict(self) -> None:
        """Codex round 12: an example, then prose about an objection."""
        text = ("Вот прошлый вердикт:\n\n```json\n"
                + json.dumps(self.full()) + "\n```\n\nНо остаётся незакрытый p1.")
        v = verdict_mod.parse(text)
        self.assertFalse(v.parsed, "пример в середине ответа принят за вердикт")
        self.assertIn("остаётся незакрытый p1", v.prose)

    def test_backticks_inside_a_string_do_not_break_the_trailer(self) -> None:
        v = self.parsed(self.full(summary="напиши ``` для кода"))
        self.assertTrue(v.parsed, v.parse_error)
        self.assertTrue(v.wants_stop)

    def test_an_example_before_the_verdict_is_ignored(self) -> None:
        text = ('Было так:\n\n```json\n{"старое": true}\n```\n\nА вот вердикт:\n\n```json\n'
                + json.dumps(self.full()) + "\n```")
        v = verdict_mod.parse(text)
        self.assertTrue(v.parsed, v.parse_error)
        self.assertTrue(v.wants_stop)

    def test_a_marked_example_before_the_verdict_is_ignored(self) -> None:
        """Codex rounds 14 and 15: fences whose label is not the verdict's."""
        for info in ("python", "diff", "bash", "c#", "objective-c++"):
            text = (f"Смотри:\n\n```{info}\nx = 1\n```\n\nВердикт:\n\n```json\n"
                    + json.dumps(self.full()) + "\n```")
            with self.subTest(fence=info):
                v = verdict_mod.parse(text)
                self.assertTrue(v.parsed, f"{info}: {v.parse_error}")
                self.assertTrue(v.wants_stop)

    def test_two_blocks_with_windows_line_endings(self) -> None:
        body = json.dumps(self.full()).replace("\n", "\r\n")
        text = ('Было:\r\n\r\n```json\r\n{"старое": true}\r\n```\r\n\r\n'
                "Стало:\r\n\r\n```json\r\n" + body + "\r\n```\r\n")
        v = verdict_mod.parse(text)
        self.assertTrue(v.parsed, v.parse_error)
        self.assertTrue(v.wants_stop)

    def test_an_answer_opening_with_an_object_still_finds_the_trailer(self) -> None:
        text = "{} — пример.\n\nА вердикт:\n\n```json\n" + json.dumps(self.full()) + "\n```"
        self.assertTrue(verdict_mod.parse(text).parsed)

    def test_a_longer_fence_is_honoured(self) -> None:
        text = "Проза.\n\n````json\n" + json.dumps(self.full()) + "\n````"
        self.assertTrue(verdict_mod.parse(text).parsed)

    def test_an_indented_example_is_not_the_verdict(self) -> None:
        """Codex round 15: four spaces make the backticks literal text."""
        block = "```json\n" + json.dumps(self.full()) + "\n```"
        text = ("Остаётся p1, ниже только пример прежнего вердикта:\n\n"
                + "\n".join("    " + line for line in block.splitlines()) + "\n")
        v = verdict_mod.parse(text)
        self.assertFalse(v.parsed, "пример с отступом принят за вердикт")
        self.assertFalse(v.wants_stop)

    def test_anything_after_the_verdict_refuses_including_an_empty_fence(self) -> None:
        """The contract is that the verdict closes the answer. Refusing costs a
        round; guessing that trailing content is decoration costs a defect."""
        text = "```json\n" + json.dumps(self.full()) + "\n```\n\n```\n\n```"
        self.assertFalse(verdict_mod.parse(text).parsed)

    def test_an_indent_on_the_opening_line_alone_is_still_a_literal(self) -> None:
        """Codex round 16: `parse` stripped the text before the split saw it."""
        # No prose in front: the defect ate the indent of the answer's *first*
        # line, so a test with a prefix stayed green through it.
        text = "    ```json\n    " + json.dumps(self.full()) + "\n```"
        v = verdict_mod.parse(text)
        self.assertFalse(v.parsed, "отступ первой строки потерян до разбора")
        self.assertFalse(v.wants_stop)

    def test_an_unmarked_verdict_after_a_json_example_is_found(self) -> None:
        """The last candidate wins, labelled or not."""
        text = ('```json\n{"пример": 1}\n```\n\nВердикт:\n\n```\n'
                + json.dumps(self.full()) + "\n```")
        v = verdict_mod.parse(text)
        self.assertTrue(v.parsed, v.parse_error)
        self.assertTrue(v.wants_stop)

    def test_a_repeated_key_cannot_erase_a_finding(self) -> None:
        """Codex round 18. Raw strings: a Python dict cannot hold duplicates."""
        body = ('{"stance":"agree","stop":true,"confidence":"high","summary":"s",'
                '"findings":[{"id":"a","severity":"p1","claim":"c","evidence":"e",'
                '"response":"new","rationale":"r"}],'
                '"findings":[],"open_questions":[]}')
        for form in (body, "Проза.\n\n```json\n" + body + "\n```"):
            with self.subTest(bare=form is body):
                v = verdict_mod.parse(form)
                self.assertFalse(v.parsed, "повторный ключ стёр возражение")
                self.assertFalse(v.wants_stop)
                self.assertIn("twice", str(v.parse_error))

    def test_a_repeated_key_inside_a_finding_is_refused(self) -> None:
        body = ('{"stance":"agree","stop":true,"confidence":"high","summary":"s","findings":'
                '[{"id":"a","severity":"p1","severity":"p3","claim":"c","evidence":"e",'
                '"response":"new","rationale":"r"}],"open_questions":[]}')
        v = verdict_mod.parse(body)
        self.assertFalse(v.parsed, "повторный ключ понизил severity")

    def test_a_verdict_nested_too_deeply_is_a_parse_error_not_a_crash(self) -> None:
        """Codex round 19: the process used to die with the turn still pending."""
        body = ('{"stance":"agree","stop":true,"confidence":"high","summary":"s",'
                '"findings":[],"open_questions":[],"x":' + "[" * 12000 + "]" * 12000 + "}")
        v = verdict_mod.parse(body)
        self.assertFalse(v.parsed)
        self.assertIn("deeply", str(v.parse_error))

    def test_an_unpaired_surrogate_is_refused_before_it_is_stored(self) -> None:
        for where, body in (
            ("summary", '{"stance":"agree","stop":true,"confidence":"high",'
                        '"summary":"\ud800","findings":[],"open_questions":[]}'),
            ("id", '{"stance":"agree","stop":true,"confidence":"high","summary":"s",'
                   '"findings":[{"id":"\ud800","severity":"p1","claim":"c","evidence":"e",'
                   '"response":"new","rationale":"r"}],"open_questions":[]}'),
        ):
            with self.subTest(where=where):
                v = verdict_mod.parse(body)
                self.assertFalse(v.parsed, "непарный суррогат дошёл до сохранения")

    def test_a_real_surrogate_pair_is_still_fine(self) -> None:
        body = ('{"stance":"agree","stop":true,"confidence":"high","summary":"😀",'
                '"findings":[],"open_questions":[]}')
        v = verdict_mod.parse(body)
        self.assertTrue(v.parsed, v.parse_error)
        self.assertEqual(v.summary, "\U0001f600")

    def test_a_numeric_finding_id_is_refused(self) -> None:
        """1e309 and 1e310 both spell "inf": two objections would merge."""
        body = ('{"stance":"agree","stop":true,"confidence":"high","summary":"s","findings":'
                '[{"id":1e309,"severity":"p1","claim":"c","evidence":"e",'
                '"response":"new","rationale":"r"}],"open_questions":[]}')
        v = verdict_mod.parse(body)
        self.assertFalse(v.parsed)
        self.assertIn("non-string id", str(v.parse_error))

    def test_a_moderately_deep_verdict_does_not_escape_as_a_crash(self) -> None:
        """Codex round 20: the string walk blew the stack one level past the
        decoder, where the handler could no longer see it."""
        body = ('{"stance":"agree","stop":true,"confidence":"high","summary":"s",'
                '"findings":[],"open_questions":[],"x":' + "[" * 1100 + "]" * 1100 + "}")
        v = verdict_mod.parse(body)   # must not raise
        self.assertTrue(v.parsed, v.parse_error)

    def test_a_pathological_fence_line_is_cheap(self) -> None:
        """Three backticks, a wall of spaces and one more backtick: the old
        pattern took ten seconds on this, and eight times that when doubled."""
        import time

        body = ('{"stance":"agree","stop":true,"confidence":"high","summary":"s",'
                '"findings":[],"open_questions":[]}')
        text = "```" + " " * 8192 + "`\n\n```json\n" + body + "\n```"
        started = time.monotonic()
        v = verdict_mod.parse(text)
        elapsed = time.monotonic() - started
        self.assertTrue(v.parsed, v.parse_error)
        self.assertLess(elapsed, 1.0, f"разбор занял {elapsed:.2f} с")

    def test_the_string_walk_does_not_amplify_memory(self) -> None:
        """Codex round 21: a 37 KB input peaked at 64 MiB, because every array
        element carried its own copy of a 32 KB parent path."""
        import tracemalloc

        body = ('{"stance":"agree","stop":true,"confidence":"high","summary":"s","findings":[],'
                '"open_questions":[],"' + "k" * 32768 + '":' + json.dumps([0] * 2048) + "}")
        tracemalloc.start()
        try:
            v = verdict_mod.parse(body)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertTrue(v.parsed, v.parse_error)
        self.assertLess(peak, 4 * 2**20, f"пик {peak / 2**20:.1f} МиБ на входе в {len(body)} байт")

    def test_the_error_path_still_names_where_the_bad_string_was(self) -> None:
        body = ('{"stance":"agree","stop":true,"confidence":"high","summary":"s","findings":[],'
                '"open_questions":[],"extra":[[{"x":"\ud800"}]]}')
        v = verdict_mod.parse(body)
        self.assertFalse(v.parsed)
        self.assertIn("extra[0][0].x", str(v.parse_error))

    def test_a_verdict_without_repeats_is_still_read(self) -> None:
        body = ('{"stance":"disagree","stop":false,"confidence":"high","summary":"s","findings":'
                '[{"id":"a","severity":"p1","claim":"c","evidence":"e",'
                '"response":"new","rationale":"r"}],"open_questions":[]}')
        v = verdict_mod.parse(body)
        self.assertTrue(v.parsed, v.parse_error)
        self.assertEqual(len(v.blocking_findings), 1)

    def test_an_unmarked_block_can_still_be_the_verdict(self) -> None:
        text = "Проза.\n\n```\n" + json.dumps(self.full()) + "\n```"
        self.assertTrue(verdict_mod.parse(text).parsed)

    def test_an_example_after_the_verdict_still_refuses(self) -> None:
        text = "```json\n" + json.dumps(self.full()) + "\n```\n\n```python\nx = 1\n```"
        self.assertFalse(verdict_mod.parse(text).parsed)

    def test_a_short_indent_still_opens_a_fence(self) -> None:
        text = "Проза.\n\n  ```json\n  " + json.dumps(self.full()) + "\n  ```"
        self.assertTrue(verdict_mod.parse(text).parsed)

    def test_a_message_that_is_only_json_is_still_a_verdict(self) -> None:
        v = verdict_mod.parse(json.dumps(self.full()))
        self.assertTrue(v.parsed, v.parse_error)

    def test_a_trailing_verdict_is_still_accepted(self) -> None:
        v = self.parsed(self.full())
        self.assertTrue(v.parsed, v.parse_error)
        self.assertTrue(v.wants_stop)

    def test_stop_hold_below_one_is_refused_by_the_cli(self) -> None:
        for flag in ("--stop-hold", "--max-rounds"):
            with self.subTest(flag=flag):
                done = subprocess.run(
                    [sys.executable, str(PACKAGE), "ask", "--phase", "design", "--profile",
                     "test", "--message", "x", flag, "0", "--dry-run"],
                    capture_output=True, text=True, cwd=str(self.tmp), env=os.environ.copy(),
                    timeout=60, check=False)
                self.assertNotEqual(done.returncode, 0)
                self.assertIn("less than 1", done.stderr)

    def test_a_fork_refuses_rather_than_change_repository(self) -> None:
        """The branch itself, with a server that would fail if it were used.

        Codex's round-13 objection was that the previous test passed
        `--no-fork` and stopped before this branch. End to end the branch is
        hard to reach on purpose: a round whose recorded directory is already
        gone moves the binding to the current one, loudly, before any fork can
        happen. What is left is the race — the directory disappearing between
        that check and the fork — and this is it.
        """
        class ExplodingServer:
            def request(self, *_a: object, **_k: object) -> None:
                raise AssertionError("форк дошёл до RPC, хотя каталог исчез")

        record = make_state(thread_id="01a0-held")
        record.cwd = str(self.tmp / "gone")
        options = options_mod.ThreadOptions(cwd=self.tmp, model="gpt-5.6-luna")
        with self.assertRaises(consult.ConsultError) as caught:
            consult._fork_held_thread(ExplodingServer(), HOST, record, "req-1", options)
        self.assertIn("disappeared", str(caught.exception))
        self.assertEqual(record.thread_id, "01a0-held", "привязка изменилась при отказе")

    def test_a_round_whose_directory_vanished_moves_the_binding_and_says_so(self) -> None:
        repo = self.tmp / "repo"
        repo.mkdir()
        first = subprocess.run(
            [sys.executable, str(PACKAGE), "ask", "--phase", "design", "--profile", "test",
             "--message", "x", "--json", "--request-id", "req-1"],
            capture_output=True, text=True, cwd=str(repo), env=os.environ.copy(),
            timeout=120, check=False)
        self.assertEqual(first.returncode, 0, first.stderr)
        thread_id = json.loads(first.stdout)["thread_id"]
        shutil.rmtree(repo)
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()

        os.environ["FAKE_CODEX_MODE"] = "busy"
        os.environ["FAKE_CODEX_BUSY_ID"] = thread_id
        os.environ["FAKE_CODEX_FORK_ID"] = "01a0-forked-here"
        done = subprocess.run(
            [sys.executable, str(PACKAGE), "ask", "--phase", "design", "--profile", "test",
             "--message", "y", "--json", "--request-id", "req-2", "--ack-request-id", "req-1"],
            capture_output=True, text=True, cwd=str(elsewhere), env=os.environ.copy(),
            timeout=120, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("no longer exists", done.stderr)
        self.assertEqual(json.loads(done.stdout)["thread_id"], "01a0-forked-here")
        record = st.load(HOST)
        assert record is not None
        self.assertEqual(Path(record.cwd).resolve(), elsewhere.resolve())


# =========================================================================
# review round 22: nothing Codex says can drive the terminal
# =========================================================================


class TestTerminalBoundary(EnvSandbox):
    ESC = "\x1b"

    def verdict_with_controls(self) -> str:
        payload = {"stance": "disagree", "stop": False, "confidence": "high",
                   "summary": "сводка " + self.ESC + "[8m скрыто",
                   "findings": [{"id": "a", "severity": "p1", "claim": "строка\tс табом\nи переводом",
                                 "evidence": self.ESC + "[31m красное", "response": "new", "rationale": "r"}],
                   "open_questions": ["вопрос " + self.ESC + "[2J"]}
        return ("Проза " + self.ESC + "[8m конец прозы\n\n```json\n"
                + json.dumps(payload, ensure_ascii=False) + "\n```")

    def cli(self, *args: str, verdict: str | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if verdict is not None:
            path = self.tmp / "verdict.txt"
            path.write_text(verdict, encoding="utf-8")
            env["FAKE_CODEX_VERDICT_FILE"] = str(path)
        return subprocess.run(
            [sys.executable, str(PACKAGE), *args], capture_output=True, text=True,
            cwd=str(self.tmp), env=env, timeout=120, check=False,
        )

    def test_control_characters_are_spelled_out_in_human_output(self) -> None:
        done = self.cli("ask", "--phase", "design", "--profile", "test", "--message", "x",
                        "--request-id", "req-1", verdict=self.verdict_with_controls())
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn(self.ESC, done.stdout, "ESC дошёл до терминала")
        self.assertIn("\\x1b[8m", done.stdout)
        self.assertIn("строка\tс табом", done.stdout, "таб потерян")
        self.assertIn("с табом\nи переводом", done.stdout, "перевод строки потерян")

    def test_the_stored_answer_on_conflict_is_sanitised_or_json(self) -> None:
        first = self.cli("ask", "--phase", "design", "--profile", "test", "--message", "x",
                         "--request-id", "req-1", verdict=self.verdict_with_controls())
        self.assertEqual(first.returncode, 0, first.stderr)
        # A different request without an ack: the conflict path, human form.
        human = self.cli("ask", "--phase", "design", "--profile", "test", "--message", "y",
                         "--request-id", "req-2")
        self.assertEqual(human.returncode, 7, human.stderr)
        self.assertIn("сохранённый ответ", human.stdout)
        self.assertNotIn(self.ESC, human.stdout + human.stderr)
        # And with --json it is JSON, not the raw answer.
        machine = self.cli("ask", "--phase", "design", "--profile", "test", "--message", "y",
                           "--request-id", "req-2", "--json")
        self.assertEqual(machine.returncode, 7, machine.stderr)
        payload = json.loads(machine.stdout)
        self.assertEqual(payload["request_id"], "req-1")
        self.assertIn("[8m", payload["stored_answer"])
        self.assertNotIn(self.ESC, machine.stdout, "JSON вывел ESC сырым")

    def test_the_thread_name_is_sanitised_too(self) -> None:
        done = self.cli("ask", "--phase", "design", "--profile", "test", "--message", "x",
                        "--request-id", "req-1", "--name", "чат " + self.ESC + "[8m")
        self.assertEqual(done.returncode, 0, done.stderr)
        status = self.cli("status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertNotIn(self.ESC, status.stdout)
        self.assertIn("\\x1b[8m", status.stdout)

    def test_a_parse_error_message_is_sanitised_on_first_run_and_replay(self) -> None:
        """Codex round 23: the diagnostic path carried the bad key into
        `decision.reason`, and the second print of it was raw."""
        body = ('{"stance":"agree","stop":true,"confidence":"high","summary":"s","findings":[],'
                '"open_questions":[],"' + self.ESC + '[8m":[{"x":"\\ud800"}]}')
        first = self.cli("ask", "--phase", "design", "--profile", "test", "--message", "x",
                         "--request-id", "req-1", verdict=body)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("ОШИБКА разбора", first.stdout)
        self.assertNotIn(self.ESC, first.stdout + first.stderr, "ESC в первом выводе")
        replay = self.cli("ask", "--phase", "design", "--profile", "test", "--message", "x",
                          "--request-id", "req-1", verdict=body)
        self.assertEqual(replay.returncode, 0, replay.stderr)
        self.assertNotIn(self.ESC, replay.stdout + replay.stderr, "ESC при повторе")

    def test_verify_sanitises_the_thread_name(self) -> None:
        done = self.cli("ask", "--phase", "design", "--profile", "test", "--message", "x",
                        "--request-id", "req-1", "--name", "чат " + self.ESC + "[8m")
        self.assertEqual(done.returncode, 0, done.stderr)
        verify = self.cli("verify")
        self.assertEqual(verify.returncode, 0, verify.stderr)
        self.assertNotIn(self.ESC, verify.stdout + verify.stderr)
        self.assertIn("\\x1b[8m", verify.stdout)

    def test_the_directory_warning_is_sanitised(self) -> None:
        done = self.cli("ask", "--phase", "design", "--profile", "test", "--message", "x",
                        "--request-id", "req-1")
        self.assertEqual(done.returncode, 0, done.stderr)
        weird = self.tmp / ("repo " + self.ESC + "[8m")
        weird.mkdir()
        record = st.load(HOST)
        assert record is not None
        record.cwd = str(weird)
        st.save(HOST, record)
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        again = subprocess.run(
            [sys.executable, str(PACKAGE), "ask", "--phase", "design", "--profile", "test",
             "--message", "y", "--request-id", "req-2", "--ack-request-id", "req-1"],
            capture_output=True, text=True, cwd=str(elsewhere), env=os.environ.copy(),
            timeout=120, check=False)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("resuming there", again.stderr)
        self.assertNotIn(self.ESC, again.stderr, "ESC в предупреждении о каталоге")

    def test_argparse_diagnostics_are_sanitised(self) -> None:
        """Codex round 24: argv is the caller's, and still a way onto the screen."""
        done = self.cli("status", "--bogus" + self.ESC + "[8m")
        self.assertEqual(done.returncode, 2)
        self.assertNotIn(self.ESC, done.stderr)
        self.assertIn("\\x1b[8m", done.stderr)

    def test_the_stored_state_keeps_the_answer_verbatim(self) -> None:
        """Sanitising is for the screen; the record is the record."""
        done = self.cli("ask", "--phase", "design", "--profile", "test", "--message", "x",
                        "--request-id", "req-1", verdict=self.verdict_with_controls())
        self.assertEqual(done.returncode, 0, done.stderr)
        record = st.load(HOST)
        assert record is not None and record.open_request is not None
        self.assertIn(self.ESC, record.open_request.result["final_text"])


# =========================================================================
# where the thread lands in Codex Desktop: the project whose root holds $PWD
# =========================================================================


class ChatFixture(EnvSandbox):
    """A Claude transcript to anchor on: the chat's directory and its title."""

    def setUp(self) -> None:
        super().setUp()
        self.projects = self.tmp / "projects"
        os.environ["DUO_PROJECTS_ROOT"] = str(self.projects)
        self.chat_dir = self.tmp / "chat"
        self.chat_dir.mkdir()
        self.sub = self.chat_dir / "repo" / "sub"
        self.sub.mkdir(parents=True)
        for name in ("FAKE_CODEX_NAME_SET_FAILS",):
            os.environ.pop(name, None)
        self.watch()

    def plant_transcript(self, chat_dir: Path | None = None, title: str | None = "Имя А",
                         with_cwd: bool = True) -> Path:
        chat_dir = chat_dir or self.chat_dir
        folder = self.projects / config.project_slug(chat_dir)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{SESSION}.jsonl"
        records = [{"type": "queue-operation", "operation": "enqueue"}]
        user = {"type": "user", "message": {"role": "user", "content": "привет"}}
        if with_cwd:
            user["cwd"] = str(chat_dir)
        records.append(user)
        if title is not None:
            records.append({"type": "custom-title", "customTitle": title})
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
        return path

    def ask(self, cwd: Path, *args: str, crash_at: str | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if crash_at:
            env["DUO_TEST_CRASH_AT"] = crash_at
        return subprocess.run(
            [sys.executable, str(PACKAGE), "ask", "--phase", "design", "--profile", "test",
             "--message", "x", "--json", *args],
            capture_output=True, text=True, cwd=str(cwd), env=env, timeout=120, check=False)

    def start_cwd(self) -> str:
        return next(c["params"]["cwd"] for c in self.seen() if c.get("method") == "thread/start")

    def names_sent(self) -> list[str]:
        return [c["params"]["name"] for c in self.seen() if c.get("method") == "thread/name/set"]

    def journal_name(self, thread_id: str) -> str | None:
        return self.journal()["threads"][thread_id].get("name")


class TestChatDirectoryAnchor(ChatFixture):
    def test_the_thread_is_anchored_on_the_chats_directory_not_the_shell(self) -> None:
        self.plant_transcript()
        done = self.ask(self.sub, "--request-id", "req-1")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(Path(self.start_cwd()).resolve(), self.chat_dir.resolve())
        self.assertIn("anchored on the chat's directory", done.stderr)
        record = st.load(HOST)
        assert record is not None
        self.assertEqual(Path(record.cwd).resolve(), self.chat_dir.resolve())

    def test_without_a_transcript_the_shell_directory_is_used_and_said(self) -> None:
        done = self.ask(self.sub, "--request-id", "req-1")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(Path(self.start_cwd()).resolve(), self.sub.resolve())
        self.assertIn("no Claude transcript", done.stderr)

    def test_a_transcript_without_a_directory_refuses_and_names_the_flag(self) -> None:
        self.plant_transcript(with_cwd=False)
        done = self.ask(self.sub, "--request-id", "req-1")
        self.assertEqual(done.returncode, 2, done.stdout)
        self.assertIn("--cwd", done.stderr)
        self.assertEqual(self.journal().get("threads", {}), {})

    def test_an_explicit_cwd_wins_over_the_transcript(self) -> None:
        self.plant_transcript()
        done = self.ask(self.sub, "--request-id", "req-1", "--cwd", str(self.sub))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(Path(self.start_cwd()).resolve(), self.sub.resolve())
        self.assertNotIn("anchored", done.stderr)

    def test_a_vanished_chat_directory_refuses(self) -> None:
        gone = self.tmp / "gone"
        gone.mkdir()
        self.plant_transcript(chat_dir=gone)
        shutil.rmtree(gone)
        done = self.ask(self.sub, "--request-id", "req-1")
        self.assertEqual(done.returncode, 2, done.stdout)
        self.assertIn("--cwd", done.stderr)

    def test_the_directory_is_fixed_in_the_creation_intent(self) -> None:
        """Codex round 1: crash after the intent in A, retry with --cwd B."""
        self.plant_transcript()
        dead = self.ask(self.sub, "--request-id", "req-1", crash_at="after_creating_intent")
        self.assertEqual(dead.returncode, 137)
        other = self.tmp / "other"
        other.mkdir()
        refused = self.ask(self.sub, "--request-id", "req-1", "--cwd", str(other))
        self.assertEqual(refused.returncode, 2, refused.stdout)
        self.assertIn("--new", refused.stderr)
        self.assertIn("--request-id <new id>", refused.stderr)
        self.assertEqual(self.journal().get("threads", {}), {}, "тред создан в B")
        alive = self.ask(self.sub, "--request-id", "req-1")
        self.assertEqual(alive.returncode, 0, alive.stderr)
        self.assertEqual(Path(self.start_cwd()).resolve(), self.chat_dir.resolve())

    def test_a_retried_creation_keeps_its_directory_even_with_new(self) -> None:
        """Codex review 2: --new on a retry used to move the thread to B."""
        self.plant_transcript()
        other = self.tmp / "other"
        other.mkdir()
        dead = self.ask(self.sub, "--request-id", "req-1", crash_at="after_creating_intent")
        self.assertEqual(dead.returncode, 137)
        again = self.ask(self.sub, "--request-id", "req-1", "--new", "--cwd", str(other))
        self.assertEqual(again.returncode, 2, again.stdout)
        self.assertIn("--request-id <new id>", again.stderr)
        self.assertEqual(self.journal().get("threads", {}), {}, "тред создан в B")

    def test_a_retry_after_the_open_request_stays_in_the_chats_directory(self) -> None:
        """The second bypass: no --new, no --cwd, just a different shell."""
        self.plant_transcript()
        dead = self.ask(self.sub, "--request-id", "req-1", crash_at="after_open_request")
        self.assertEqual(dead.returncode, 137)
        record = st.load(HOST)
        assert record is not None
        self.assertEqual(Path(record.cwd).resolve(), self.chat_dir.resolve())
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        again = self.ask(elsewhere, "--request-id", "req-1")
        self.assertEqual(again.returncode, 0, again.stderr)
        after = st.load(HOST)
        assert after is not None
        self.assertEqual(Path(after.cwd).resolve(), self.chat_dir.resolve())
        resumes = [c for c in self.seen() if c.get("method") == "thread/resume"]
        self.assertEqual(Path(resumes[-1]["params"]["cwd"]).resolve(), self.chat_dir.resolve())

    def test_an_unreadable_transcript_refuses_instead_of_wandering(self) -> None:
        path = self.plant_transcript()
        path.chmod(0o000)
        self.addCleanup(path.chmod, 0o600)
        done = self.ask(self.sub, "--request-id", "req-1")
        self.assertEqual(done.returncode, 2, done.stdout)
        self.assertIn("cannot read the Claude transcript", done.stderr)
        self.assertEqual(self.journal().get("threads", {}), {})

    def test_a_relative_first_directory_refuses(self) -> None:
        folder = self.projects / config.project_slug(self.chat_dir)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{SESSION}.jsonl").write_text(
            json.dumps({"type": "user", "cwd": "relative/path"}) + "\n"
            + json.dumps({"type": "user", "cwd": str(self.chat_dir)}) + "\n",
            encoding="utf-8")
        done = self.ask(self.sub, "--request-id", "req-1")
        self.assertEqual(done.returncode, 2, done.stdout)
        self.assertIn("--cwd", done.stderr)

    def test_a_later_round_from_a_subdirectory_is_quiet(self) -> None:
        self.plant_transcript()
        self.ask(self.sub, "--request-id", "req-1")
        again = self.ask(self.sub, "--request-id", "req-2", "--ack-request-id", "req-1")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertNotIn("resuming there", again.stderr)
        self.assertNotIn("anchored", again.stderr)


class TestThreadNameFollowsChat(ChatFixture):
    def test_a_rename_in_desktop_is_overridden_by_the_chat_title(self) -> None:
        """Codex round 1: state == chat, Desktop differs; the chat must win."""
        self.plant_transcript(title="Имя А")
        first = self.ask(self.sub, "--request-id", "req-1")
        self.assertEqual(first.returncode, 0, first.stderr)
        tid = json.loads(first.stdout)["thread_id"]
        data = self.journal(); data["threads"][tid]["name"] = "Имя из Desktop"
        (self.tmp / "journal.json").write_text(json.dumps(data), encoding="utf-8")
        again = self.ask(self.sub, "--request-id", "req-2", "--ack-request-id", "req-1")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(self.journal_name(tid), "Имя А")

    def test_a_renamed_chat_renames_the_thread(self) -> None:
        self.plant_transcript(title="Имя А")
        first = self.ask(self.sub, "--request-id", "req-1")
        tid = json.loads(first.stdout)["thread_id"]
        self.plant_transcript(title="Имя Б")
        again = self.ask(self.sub, "--request-id", "req-2", "--ack-request-id", "req-1")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(self.journal_name(tid), "Имя Б")
        record = st.load(HOST)
        assert record is not None
        self.assertEqual(record.thread_name, "Имя Б")
        self.assertIs(record.name_pinned, False)

    def test_a_pinned_name_survives_a_refused_rename(self) -> None:
        """Codex round 2: the wanted name is state before the RPC."""
        self.plant_transcript(title="Имя А")
        first = self.ask(self.sub, "--request-id", "req-1")
        tid = json.loads(first.stdout)["thread_id"]
        os.environ["FAKE_CODEX_NAME_SET_FAILS"] = "1"
        pinned = self.ask(self.sub, "--request-id", "req-2", "--ack-request-id", "req-1", "--name", "Закреплено")
        self.assertEqual(pinned.returncode, 0, pinned.stderr)
        self.assertIn("will try again", pinned.stderr)
        record = st.load(HOST)
        assert record is not None
        self.assertEqual(record.thread_name, "Закреплено")
        self.assertIs(record.name_pinned, True)
        os.environ.pop("FAKE_CODEX_NAME_SET_FAILS")
        again = self.ask(self.sub, "--request-id", "req-3", "--ack-request-id", "req-2")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(self.journal_name(tid), "Закреплено")
        # The chat title keeps changing; the pin holds.
        self.plant_transcript(title="Имя В")
        more = self.ask(self.sub, "--request-id", "req-4", "--ack-request-id", "req-3")
        self.assertEqual(more.returncode, 0, more.stderr)
        self.assertEqual(self.journal_name(tid), "Закреплено")

    def test_name_from_chat_unpins(self) -> None:
        self.plant_transcript(title="Имя А")
        first = self.ask(self.sub, "--request-id", "req-1", "--name", "Закреплено")
        tid = json.loads(first.stdout)["thread_id"]
        self.plant_transcript(title="Имя Б")
        back = self.ask(self.sub, "--request-id", "req-2", "--ack-request-id", "req-1", "--name-from-chat")
        self.assertEqual(back.returncode, 0, back.stderr)
        self.assertEqual(self.journal_name(tid), "Имя Б")
        record = st.load(HOST)
        assert record is not None
        self.assertIs(record.name_pinned, False)

    def test_name_and_name_from_chat_exclude_each_other(self) -> None:
        done = self.ask(self.sub, "--name", "x", "--name-from-chat", "--dry-run")
        self.assertEqual(done.returncode, 2)
        self.assertIn("not allowed with", done.stderr)

    def test_an_old_state_is_unpinned_and_told_once(self) -> None:
        self.plant_transcript(title="Имя А")
        first = self.ask(self.sub, "--request-id", "req-1")
        self.assertEqual(first.returncode, 0, first.stderr)
        payload = json.loads(st.state_path(HOST).read_text(encoding="utf-8"))
        payload.pop("name_pinned", None)
        st.state_path(HOST).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        again = self.ask(self.sub, "--request-id", "req-2", "--ack-request-id", "req-1")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("predates name pinning", again.stderr)
        record = st.load(HOST)
        assert record is not None
        self.assertIs(record.name_pinned, False)
        third = self.ask(self.sub, "--request-id", "req-3", "--ack-request-id", "req-2")
        self.assertNotIn("predates", third.stderr)

    def test_a_retried_creation_sends_the_new_pinned_name(self) -> None:
        """Codex review 2: the created-shortcut hid a rename the server never got."""
        self.plant_transcript(title="Имя А")
        dead = self.ask(self.sub, "--request-id", "req-1", "--name", "Имя А",
                        crash_at="after_thread_start_response")
        self.assertEqual(dead.returncode, 137)
        again = self.ask(self.sub, "--request-id", "req-1", "--name", "Имя Б")
        self.assertEqual(again.returncode, 0, again.stderr)
        tid = json.loads(again.stdout)["thread_id"]
        self.assertEqual(self.journal_name(tid), "Имя Б", "серверу ушло старое имя")

    def test_an_explicit_null_pin_is_a_schema_error(self) -> None:
        self.plant_transcript()
        first = self.ask(self.sub, "--request-id", "req-1", "--name", "Закреплено")
        self.assertEqual(first.returncode, 0, first.stderr)
        payload = json.loads(st.state_path(HOST).read_text(encoding="utf-8"))
        payload["name_pinned"] = None
        st.state_path(HOST).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(st.StateCorrupt):
            st.load(HOST)

    def test_a_legacy_state_is_written_without_the_field(self) -> None:
        record = make_state()
        record.name_pinned = None
        self.assertNotIn("name_pinned", record.to_json())
        record.name_pinned = False
        self.assertIs(record.to_json()["name_pinned"], False)

    def test_a_replay_sends_no_rename(self) -> None:
        self.plant_transcript(title="Имя А")
        first = self.ask(self.sub, "--request-id", "req-1")
        self.assertEqual(first.returncode, 0, first.stderr)
        before = len(self.names_sent())
        replay = self.ask(self.sub, "--request-id", "req-1")
        self.assertEqual(replay.returncode, 0, replay.stderr)
        self.assertTrue(json.loads(replay.stdout)["replayed"])
        self.assertEqual(len(self.names_sent()), before)

import jsonschema

SCHEMA = Path(__file__).resolve().parent / "schema" / "0.160.1"


class _Recorder:
    """Stands in for an AppServer: records requests, answers a fork with an id."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def request(self, method: str, params: dict) -> dict:
        self.calls.append((method, params))
        return {"thread": {"id": "01a0-fork"}}


class TestProtocolContract(EnvSandbox):
    """What duo sends and answers, checked against the schema of codex-cli 0.160.1.

    The fixture is the generator's own output, copied file for file; refresh it
    with the command in references/protocol.md.
    """

    ANSWERS = {
        "item/commandExecution/requestApproval": "CommandExecutionRequestApprovalResponse.json",
        "item/fileChange/requestApproval": "FileChangeRequestApprovalResponse.json",
        "item/permissions/requestApproval": "PermissionsRequestApprovalResponse.json",
        "execCommandApproval": "ExecCommandApprovalResponse.json",
        "applyPatchApproval": "ApplyPatchApprovalResponse.json",
        "mcpServer/elicitation/request": "McpServerElicitationRequestResponse.json",
        "item/tool/requestUserInput": "ToolRequestUserInputResponse.json",
    }

    def check(self, name: str, value: object) -> None:
        jsonschema.validate(value, json.loads((SCHEMA / name).read_text(encoding="utf-8")))

    def test_every_answer_to_a_server_request_fits_its_schema(self) -> None:
        self.assertEqual(set(appserver._DENIALS), set(self.ANSWERS))
        for method, name in self.ANSWERS.items():
            with self.subTest(method=method):
                self.check(name, appserver._DENIALS[method])
        self.check("CurrentTimeReadResponse.json", {"currentTimeAt": 1_700_000_000})

    def test_the_answers_sent_before_are_rejected(self) -> None:
        for name, old in (
            ("ToolRequestUserInputResponse.json", {"answers": []}),
            ("ExecCommandApprovalResponse.json", {"decision": "denied"}),
            ("ApplyPatchApprovalResponse.json", {"decision": "denied"}),
        ):
            with self.subTest(name=name), self.assertRaises(jsonschema.ValidationError):
                self.check(name, old)

    def test_the_requests_duo_sends_fit_their_schema(self) -> None:
        done = subprocess.run(
            [sys.executable, str(PACKAGE), "ask", "--phase", "design", "--profile", "test",
             "--message", "x", "--dry-run", "--sandbox", "read-only", "--verdict-format", "json"],
            capture_output=True, text=True, cwd=str(self.tmp), env=os.environ.copy(),
            timeout=60, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        calls = {c.get("method"): c.get("params") for c in json.loads(done.stdout)["calls"]}
        self.check("v2/ThreadStartParams.json", calls["thread/start"])
        self.check("v2/TurnStartParams.json", calls["turn/start"])

        server = _Recorder()
        consult.resume_thread(server, "01a0-thread", self.tmp, "read-only")
        consult.fork_thread(server, "01a0-thread",
                            options_mod.ThreadOptions(cwd=self.tmp, model="gpt-5.6-luna",
                                                      sandbox="read-only"))
        (_, resume), (_, fork) = server.calls
        self.check("v2/ThreadResumeParams.json", resume)
        self.check("v2/ThreadForkParams.json", fork)
        self.assertIs(fork["excludeTurns"], True)

    def test_a_write_stdin_approval_is_declined_and_the_turn_finishes(self) -> None:
        os.environ["FAKE_CODEX_MODE"] = "approval"
        os.environ["FAKE_CODEX_APPROVAL_KIND"] = "writeStdin"
        self.check("CommandExecutionRequestApprovalParams.json", {
            "threadId": "t", "turnId": "u", "itemId": "item-approval", "startedAtMs": 0,
            "command": "rm -rf /", "kind": "writeStdin"})
        self.assertEqual(run(self).turn_status, "completed")
        answers = self.journal()["approval_answers"]
        self.assertEqual(answers, [{"decision": "decline"}])
        self.check("CommandExecutionRequestApprovalResponse.json", answers[0])


class TestSupersededOriginal(EnvSandbox):
    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PACKAGE), "ask", "--phase", "design", "--profile", "test",
             "--message", "x", "--json", *args],
            capture_output=True, text=True, cwd=str(self.tmp), env=os.environ.copy(),
            timeout=120, check=False)

    def setUp(self) -> None:
        super().setUp()
        self.watch()
        first = self.cli("--request-id", "req-1")
        assert first.returncode == 0, first.stderr
        self.source = json.loads(first.stdout)["thread_id"]
        os.environ["FAKE_CODEX_MODE"] = "busy"
        os.environ["FAKE_CODEX_BUSY_ID"] = self.source
        os.environ["FAKE_CODEX_FORK_ID"] = "01a0-fork"

    def names_set(self) -> list[tuple[str, str]]:
        return [(c["params"]["threadId"], c["params"]["name"])
                for c in self.seen() if c.get("method") == "thread/name/set"]

    def test_the_original_is_marked_old_the_moment_it_is_forked(self) -> None:
        done = self.cli("--request-id", "req-2", "--ack-request-id", "req-1")
        self.assertEqual(done.returncode, 0, done.stderr)
        record = st.load(HOST)
        assert record is not None
        self.assertIn((self.source, consult.SUPERSEDED_PREFIX + record.thread_name), self.names_set())
        self.assertEqual(record.archive_debt, [self.source])
        self.assertFalse(self.journal()["threads"][self.source].get("archived"),
                         "заархивирован, пока Desktop держит")

    def test_while_held_it_stays_marked_and_owed(self) -> None:
        self.cli("--request-id", "req-2", "--ack-request-id", "req-1")
        again = self.cli("--request-id", "req-3", "--ack-request-id", "req-2")
        self.assertEqual(again.returncode, 0, again.stderr)
        record = st.load(HOST)
        assert record is not None
        self.assertEqual(record.archive_debt, [self.source])
        self.assertEqual(self.journal()["threads"][self.source]["name"],
                         consult.SUPERSEDED_PREFIX + record.thread_name)

    def test_once_desktop_lets_go_the_original_is_archived_not_deleted(self) -> None:
        self.cli("--request-id", "req-2", "--ack-request-id", "req-1")
        # The human goes on writing into the original after the fork.
        data = self.journal()
        data["threads"][self.source]["turns"].append(
            {"id": "t-after-fork", "client_id": None, "input": "после форка", "status": "completed",
             "result": verdict_json()})
        (self.tmp / "journal.json").write_text(json.dumps(data), encoding="utf-8")
        os.environ["FAKE_CODEX_MODE"] = "ok"
        os.environ.pop("FAKE_CODEX_BUSY_ID", None)
        again = self.cli("--request-id", "req-3", "--ack-request-id", "req-2")
        self.assertEqual(again.returncode, 0, again.stderr)
        # The human may have written into it after the fork: archived keeps that.
        original = self.journal()["threads"][self.source]
        self.assertTrue(original.get("archived"))
        self.assertIn("t-after-fork", [t["id"] for t in original["turns"]])
        self.assertNotIn("thread/delete", [c.get("method") for c in self.seen()])
        self.assertIn(f"archived thread {self.source}", again.stderr)
        record = st.load(HOST)
        assert record is not None
        self.assertEqual(record.archive_debt, [])
        # One live thread under the chat's name.
        live = [t for t in self.journal()["threads"].values() if not t.get("archived")]
        self.assertEqual([t["id"] for t in live], ["01a0-fork"])

    def test_a_state_with_the_old_delete_list_owes_an_archive_instead(self) -> None:
        self.cli("--request-id", "req-2", "--ack-request-id", "req-1")
        path = st.state_path(HOST)
        raw = json.loads(path.read_text())
        raw["supersede_debt"] = ["01a0-legacy", self.source, "01a0-legacy"]
        raw["archive_debt"] = [self.source]
        path.write_text(json.dumps(raw))
        record = st.load(HOST)
        assert record is not None
        self.assertEqual(record.archive_debt, [self.source, "01a0-legacy"])
        self.assertNotIn("supersede_debt", record.to_json())

    def test_a_mark_that_failed_at_fork_time_is_set_on_a_later_round(self) -> None:
        os.environ["FAKE_CODEX_NAME_SET_FAILS"] = "1"
        self.cli("--request-id", "req-2", "--ack-request-id", "req-1")
        record = st.load(HOST)
        assert record is not None
        self.assertNotEqual(self.journal()["threads"][self.source].get("name"),
                            consult.SUPERSEDED_PREFIX + record.thread_name)
        os.environ.pop("FAKE_CODEX_NAME_SET_FAILS")
        again = self.cli("--request-id", "req-3", "--ack-request-id", "req-2")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(self.journal()["threads"][self.source]["name"],
                         consult.SUPERSEDED_PREFIX + record.thread_name)

    def test_close_refuses_while_the_original_is_still_held(self) -> None:
        self.cli("--request-id", "req-2", "--ack-request-id", "req-1")
        os.environ["FAKE_CODEX_BUSY_ID"] = self.source   # still held; the fork is free
        done = subprocess.run(
            [sys.executable, str(PACKAGE), "close", "--archive"], capture_output=True, text=True,
            cwd=str(self.tmp), env=os.environ.copy(), timeout=120, check=False)
        self.assertNotEqual(done.returncode, 0)
        self.assertTrue(st.state_path(HOST).exists())

    def test_a_new_thread_still_archives_rather_than_deletes(self) -> None:
        os.environ["FAKE_CODEX_MODE"] = "ok"
        os.environ.pop("FAKE_CODEX_BUSY_ID", None)
        done = self.cli("--request-id", "req-2", "--ack-request-id", "req-1", "--new")
        self.assertEqual(done.returncode, 0, done.stderr)
        threads = self.journal()["threads"]
        self.assertIn(self.source, threads, "--new удалил тред, чью историю никто не хранит")
        self.assertTrue(threads[self.source]["archived"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
