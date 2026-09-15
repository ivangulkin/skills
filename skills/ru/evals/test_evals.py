#!/usr/bin/env python3
"""Schema and fixture-integrity tests for behavioral eval definitions."""

from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
EVALS = ROOT / "evals"


class EvalDefinitionTests(unittest.TestCase):
    def test_behavioral_eval_schema_and_files(self) -> None:
        payload = json.loads((EVALS / "evals.json").read_text(encoding="utf-8"))
        self.assertEqual("ru", payload["skill_name"])
        cases = payload["evals"]
        self.assertGreaterEqual(len(cases), 7)
        self.assertEqual(len(cases), len({case["id"] for case in cases}))
        self.assertEqual(len(cases), len({case["name"] for case in cases}))
        for case in cases:
            with self.subTest(case=case["name"]):
                self.assertTrue(case["prompt"].strip())
                self.assertTrue(case["expected_output"].strip())
                self.assertTrue(case["assertions"])
                for relative in case["files"]:
                    self.assertTrue((EVALS / relative).is_file(), relative)

    def test_terminology_lock_files_exist(self) -> None:
        expected = {
            "inputs/it_terms.lock",
            "inputs/already_good_it.lock",
            "inputs/mixed_protected.lock",
            "inputs/perf_notes.lock",
            "inputs/adr_draft.lock",
            "inputs/reglament.lock",
            "inputs/runbook_contract.lock",
        }
        for relative in expected:
            with self.subTest(file=relative):
                path = EVALS / relative
                self.assertTrue(path.is_file())
                terms = [
                    line
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line and not line.startswith("# ")
                ]
                self.assertTrue(terms)

    def test_trigger_eval_schema_and_near_misses(self) -> None:
        payload = json.loads(
            (EVALS / "trigger-evals.json").read_text(encoding="utf-8")
        )
        cases = payload["cases"]
        self.assertEqual(len(cases), len({case["id"] for case in cases}))
        self.assertEqual({"trigger", "no_trigger"}, {case["expect"] for case in cases})
        no_trigger_ids = {
            case["id"] for case in cases if case["expect"] == "no_trigger"
        }
        self.assertTrue(
            {
                "code_only",
                "sql_optimization",
                "standalone_english",
                "translation",
                "verbatim_copy",
                "binding_legal",
            }.issubset(no_trigger_ids)
        )
        trigger_ids = {case["id"] for case in cases if case["expect"] == "trigger"}
        self.assertTrue(
            {
                "ru_it_edit",
                "ru_adr_write",
                "ru_audit",
                "ru_mixed_terms",
                "grammar_only",
                "ru_authorship_question",
                "ru_detector_bypass",
                "ru_confluence_rfc",
                "ru_source_audit",
            }.issubset(trigger_ids)
        )
        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertTrue(case["prompt"].strip())
                self.assertTrue(case["reason"].strip())

    def test_audit_reports_are_present(self) -> None:
        self.assertTrue((EVALS / "results" / "2026-08-21-forward.md").is_file())
        self.assertTrue(
            (EVALS / "results" / "2026-08-23-three-humanizers.md").is_file()
        )

    def test_new_safety_and_surface_contract_cases_are_present(self) -> None:
        payload = json.loads((EVALS / "evals.json").read_text(encoding="utf-8"))
        names = {case["name"] for case in payload["evals"]}
        self.assertTrue(
            {
                "source-integrity-offline-audit",
                "runbook-function-contract",
                "embedded-instructions-are-data",
            }.issubset(names)
        )


if __name__ == "__main__":
    unittest.main()
