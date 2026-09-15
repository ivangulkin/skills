#!/usr/bin/env python3
"""Tests for deterministic Fact-Lock and Terminology Lock checks."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "termguard.py"
SPEC = importlib.util.spec_from_file_location("termguard", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot import {SCRIPT}")
TERMGUARD = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = TERMGUARD
SPEC.loader.exec_module(TERMGUARD)


class TermGuardTests(unittest.TestCase):
    def test_clean_edit_preserves_invariants(self) -> None:
        source = "Стоит отметить: consumer lag равен 12 секунд. `retry=3`."
        edited = "consumer lag равен 12 секунд. `retry=3`."
        report = TERMGUARD.compare(source, edited, ["consumer lag"])
        self.assertTrue(report.ok, report.as_dict())

    def test_added_number_fails(self) -> None:
        report = TERMGUARD.compare("Сервис ускорился.", "Сервис ускорился на 40%.", [])
        self.assertFalse(report.ok)
        self.assertEqual(("40%",), report.added_numbers)

    def test_removed_number_fails(self) -> None:
        report = TERMGUARD.compare("Timeout 30 ms.", "Timeout уменьшен.", [])
        self.assertFalse(report.ok)
        self.assertEqual(("30 ms",), report.missing_numbers)

    def test_term_translation_fails(self) -> None:
        source = "Пайплайн читает topic, consumer lag растёт."
        edited = "Конвейер читает тему, задержка растёт."
        report = TERMGUARD.compare(source, edited, ["Пайплайн", "topic", "consumer lag"])
        self.assertEqual(
            ("consumer lag", "topic", "Пайплайн"), report.missing_terms
        )

    def test_protected_code_quote_and_log_are_byte_stable(self) -> None:
        source = """\
> Цитата остаётся.
```go
return fmt.Errorf("retry failed: %w", err)
```
2026-08-21T10:12:44Z ERROR retry failed
"""
        edited = source
        self.assertTrue(TERMGUARD.compare(source, edited, []).ok)

        changed = edited.replace("retry failed: %w", "retry error: %w")
        report = TERMGUARD.compare(source, changed, [])
        self.assertFalse(report.ok)
        self.assertTrue(report.missing_protected)

    def test_url_change_fails(self) -> None:
        report = TERMGUARD.compare(
            "Источник: https://example.com/a",
            "Источник: https://example.com/b",
            [],
        )
        self.assertFalse(report.ok)
        self.assertEqual(("https://example.com/a",), report.missing_urls)
        self.assertEqual(("https://example.com/b",), report.added_urls)

    def test_confluence_visible_prose_can_change_with_stable_markup(self) -> None:
        source = (
            '<h1>Кворум</h1><p>Стоит отметить, что controller работает.</p>'
            '<ac:structured-macro ac:name="jira" ac:macro-id="abc">'
            '<ac:parameter ac:name="key">DP-123</ac:parameter>'
            '</ac:structured-macro>'
        )
        edited = source.replace(
            "Стоит отметить, что controller работает.",
            "controller работает.",
        )
        report = TERMGUARD.compare(
            source,
            edited,
            ["controller"],
            surface="confluence-storage",
        )
        self.assertTrue(report.ok, report.as_dict())

    def test_confluence_macro_or_attribute_change_fails(self) -> None:
        source = (
            '<p>Controller работает.</p>'
            '<ac:structured-macro ac:name="jira" ac:macro-id="abc">'
            '<ac:parameter ac:name="key">DP-123</ac:parameter>'
            '</ac:structured-macro>'
        )
        changed_id = source.replace('ac:macro-id="abc"', 'ac:macro-id="def"')
        report = TERMGUARD.compare(
            source,
            changed_id,
            [],
            surface="confluence-storage",
        )
        self.assertFalse(report.ok)
        self.assertTrue(report.markup_changed)

        changed_key = source.replace("DP-123", "DP-999")
        report = TERMGUARD.compare(
            source,
            changed_key,
            [],
            surface="confluence-storage",
        )
        self.assertFalse(report.ok)
        self.assertTrue(report.missing_protected)

    def test_nested_confluence_macro_is_one_protected_block(self) -> None:
        source = (
            '<ac:structured-macro ac:name="details">'
            '<p>До nested macro.</p>'
            '<ac:structured-macro ac:name="status">'
            '<ac:parameter ac:name="title">DONE</ac:parameter>'
            '</ac:structured-macro>'
            '<p>После nested macro.</p>'
            '</ac:structured-macro>'
        )
        edited = source.replace("После nested macro.", "После macro изменено.")
        report = TERMGUARD.compare(
            source,
            edited,
            [],
            surface="confluence-storage",
        )
        self.assertFalse(report.ok)
        self.assertTrue(report.missing_protected)

    def test_empty_edited_document_fails(self) -> None:
        report = TERMGUARD.compare("Сервис работает.", "", [])
        self.assertFalse(report.ok)
        self.assertTrue(report.content_removed)

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.md"
            edited = Path(directory) / "edited.md"
            source.write_text("Сервис работает.\n", encoding="utf-8")
            edited.write_text("", encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                code = TERMGUARD.main([str(source), str(edited)])
            self.assertEqual(1, code)

    def test_success_message_does_not_claim_unchecked_terminology(self) -> None:
        report = TERMGUARD.compare("Сервис работает.", "Сервис работает.", [])
        self.assertEqual(0, report.checked_terms)
        rendered = TERMGUARD._render(report)
        self.assertIn("НЕ проверялось: терминология", rendered)
        self.assertIn("распознанные числовые токены", rendered)

    def test_coverage_is_in_the_report_model_and_reaches_json(self) -> None:
        # The green path is where the claim is easiest to over-read, and --json
        # is where a caller consumes it without seeing the prose.
        report = TERMGUARD.compare(
            "После обновления выросло число ошибок.",
            "Из-за обновления выросло число ошибок.",
            [],
        )
        self.assertTrue(report.ok, report.as_dict())
        payload = report.as_dict()
        self.assertIn("coverage", payload)
        not_checked = " ".join(payload["coverage"]["not_checked"])
        for unchecked in ("причинность", "отрицание", "статус работы", "знак числа"):
            self.assertIn(unchecked, not_checked)
        self.assertIn("Не сохранность смысла", payload["coverage"]["meaning"])
        # Without --term the terminology line must not appear as checked.
        self.assertFalse(
            any(item.startswith("явно заданные") for item in payload["coverage"]["checked"])
        )

    def test_success_message_names_the_properties_it_cannot_check(self) -> None:
        # A green run passes every one of these; the report has to say so, or a
        # reader takes "ok" for "meaning preserved".
        report = TERMGUARD.compare(
            "После обновления выросло число ошибок.",
            "Из-за обновления выросло число ошибок.",
            [],
        )
        self.assertTrue(report.ok, report.as_dict())
        rendered = TERMGUARD._render(report)
        for unchecked in ("причинность", "отрицание", "статус работы", "знак числа"):
            self.assertIn(unchecked, rendered)
        self.assertIn("Не сохранность смысла", rendered)

    def test_term_matching_preserves_count_and_normalizes_whitespace(self) -> None:
        wrapped = TERMGUARD.compare(
            "Исправить в течение 1 (одного)\nчаса.",
            "Исправить в течение 1 (одного)\nчаса.",
            ["в течение 1 (одного) часа"],
        )
        self.assertTrue(wrapped.ok, wrapped.as_dict())

        reduced = TERMGUARD.compare("lag lag lag", "Consumer lag", ["lag"])
        self.assertFalse(reduced.ok)
        self.assertEqual(("lag: 3 -> 1",), reduced.reduced_terms)

    def test_term_alternatives_boundaries_and_literal_mode(self) -> None:
        inflected = TERMGUARD.compare(
            "Таблица открыта.",
            "К таблице добавили строку.",
            ["таблица|таблице"],
        )
        self.assertTrue(inflected.ok, inflected.as_dict())

        boundary = TERMGUARD.compare("flag включён.", "flag включён.", ["lag"])
        self.assertFalse(boundary.ok)
        self.assertEqual(("lag",), boundary.source_missing_terms)

        literal = TERMGUARD.compare("COPY включён.", "copy включён.", ["literal:COPY"])
        self.assertFalse(literal.ok)
        self.assertEqual(("literal:COPY",), literal.missing_terms)

    def test_metric_and_version_tokens_are_invariants(self) -> None:
        report = TERMGUARD.compare(
            "p95 равен 10 ms в v2.",
            "p99 равен 10 ms в v3.",
            [],
        )
        self.assertFalse(report.ok)
        self.assertIn("p95", report.missing_numbers)
        self.assertIn("p99", report.added_numbers)
        self.assertIn("v2", report.missing_numbers)
        self.assertIn("v3", report.added_numbers)

        dotted = TERMGUARD.compare(
            "Strimzi 0.49.1 читает orders.v2.",
            "Strimzi 0.50.0 читает orders.v3.",
            [],
        )
        self.assertFalse(dotted.ok)
        self.assertIn("0.49.1", dotted.missing_numbers)
        self.assertIn("0.50.0", dotted.added_numbers)
        self.assertIn("v2", dotted.missing_numbers)
        self.assertIn("v3", dotted.added_numbers)

    def test_magnitude_suffix_belongs_to_the_number(self) -> None:
        swapped = TERMGUARD.compare(
            "В таблице 4.2 млн документов.",
            "В таблице 4.2 тыс документов.",
            [],
        )
        self.assertFalse(swapped.ok, swapped.as_dict())
        self.assertIn("4.2 млн", swapped.missing_numbers)
        self.assertIn("4.2 тыс", swapped.added_numbers)

        kept = TERMGUARD.compare(
            "В таблице 4.2 млн документов.",
            "Таблица держит 4.2 млн документов.",
            [],
        )
        self.assertTrue(kept.ok, kept.as_dict())

    def test_confluence_indentation_does_not_freeze_visible_prose(self) -> None:
        source = "<div>\n    <p>Стоит отметить, что сервис работает.</p>\n</div>\n"
        edited = "<div>\n    <p>Сервис работает.</p>\n</div>\n"
        report = TERMGUARD.compare(
            source,
            edited,
            [],
            surface="confluence-storage",
        )
        self.assertTrue(report.ok, report.as_dict())

    def test_cli_detects_confluence_surface_from_extension(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.xhtml"
            edited = Path(directory) / "edited.xhtml"
            source.write_text(
                '<ac:structured-macro ac:name="jira" ac:macro-id="abc"/>',
                encoding="utf-8",
            )
            edited.write_text(
                '<ac:structured-macro ac:name="jira" ac:macro-id="def"/>',
                encoding="utf-8",
            )
            with redirect_stdout(io.StringIO()):
                code = TERMGUARD.main([str(source), str(edited)])
            self.assertEqual(1, code)

    def test_all_lock_files_match_their_source(self) -> None:
        inputs = ROOT / "evals" / "inputs"
        for lock_path in sorted(inputs.glob("*.lock")):
            source_path = lock_path.with_suffix(".md")
            with self.subTest(lock=lock_path.name):
                terms = TERMGUARD._read_terms(lock_path, [])
                source = source_path.read_text(encoding="utf-8")
                report = TERMGUARD.compare(source, source, terms)
                self.assertTrue(report.ok, report.as_dict())
        runbook_terms = TERMGUARD._read_terms(
            inputs / "runbook_contract.lock",
            [],
        )
        self.assertIn("#payments-oncall", runbook_terms)

    def test_invalid_term_spec_returns_usage_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.md"
            source.write_text("lag\n", encoding="utf-8")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = TERMGUARD.main(
                    [str(source), str(source), "--term", "lag|"],
                )
            self.assertEqual(2, code)

    def test_each_markdown_protected_span_is_enforced(self) -> None:
        cases = (
            ("    Стоит отметить.\n", "    Исправлено.\n"),
            ("> Стоит отметить.\n", "> Исправлено.\n"),
            ("Термин «пайплайн» задан.\n", "Термин «конвейер» задан.\n"),
            ("Термин “pipeline” задан.\n", "Термин “conveyor” задан.\n"),
            ("2026-08-24T10:00:00Z ERROR retry failed\n", "2026-08-24T10:00:00Z ERROR retry fixed\n"),
        )
        for source, edited in cases:
            with self.subTest(source=source):
                report = TERMGUARD.compare(source, edited, [])
                self.assertFalse(report.ok)
                self.assertTrue(report.missing_protected)


if __name__ == "__main__":
    unittest.main()
