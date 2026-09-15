#!/usr/bin/env python3
"""Regression tests for the deterministic no-ai-slop scanner."""

from __future__ import annotations

from contextlib import redirect_stdout
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "slopspans.py"
SPEC = importlib.util.spec_from_file_location("slopspans", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot import {SCRIPT}")
SLOPSPANS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SLOPSPANS
SPEC.loader.exec_module(SLOPSPANS)


class SlopSpansTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = SLOPSPANS.load_rules()

    def ids(
        self,
        text: str,
        *,
        register: str = "general",
        surface: str = "markdown",
    ) -> list[str]:
        findings = SLOPSPANS.scan_text(
            "fixture.md",
            text,
            self.rules,
            language="ru",
            register=register,
            surface=surface,
        )
        return [finding.rule.id for finding in findings]

    def test_every_rule_has_working_examples(self) -> None:
        for rule in self.rules:
            with self.subTest(rule=rule.id, case="positive"):
                self.assertIn(rule.id, self.ids(rule.examples["positive"]))
            with self.subTest(rule=rule.id, case="negative"):
                self.assertNotIn(rule.id, self.ids(rule.examples["negative"]))
            with self.subTest(rule=rule.id, case="boundary"):
                self.assertNotIn(rule.id, self.ids(rule.examples["boundary"]))

    def test_rule_registry_ids_are_unique(self) -> None:
        ids = [rule.id for rule in self.rules]
        self.assertEqual(len(ids), len(set(ids)))

    def test_rules_have_sources_and_three_tiers(self) -> None:
        self.assertEqual(
            {rule.tier for rule in self.rules},
            {"artifact-strict", "hygiene-contextual", "soft-candidate"},
        )
        self.assertTrue(all(rule.source.strip() for rule in self.rules))

    def test_evidence_registry_is_dated_and_complete(self) -> None:
        payload = json.loads(
            (ROOT / "scripts" / "rules.json").read_text(encoding="utf-8")
        )
        self.assertEqual(2, payload["schema_version"])
        self.assertEqual("2026-08-24", payload["registry_updated"])
        registry = payload["source_registry"]
        self.assertTrue(registry)
        for source_id, record in registry.items():
            with self.subTest(source=source_id):
                self.assertTrue(record["accessed"])
                self.assertIsInstance(record["immutable"], bool)
                self.assertTrue(record["claim"])

    def test_protected_fixture_is_clean(self) -> None:
        text = (ROOT / "evals" / "fixtures" / "slopspans-negative.md").read_text(
            encoding="utf-8"
        )
        findings = SLOPSPANS.scan_text(
            "negative.md",
            text,
            self.rules,
            language="ru",
            register="technical",
        )
        self.assertEqual([], findings)

    def test_multiline_paragraph_matching(self) -> None:
        text = "Используя этот метод,\nэффективность повышается."
        findings = SLOPSPANS.scan_text(
            "wrapped.md", text, self.rules, register="general"
        )
        gerunds = [
            finding
            for finding in findings
            if finding.rule.id == "grammar.dangling-gerund"
        ]
        self.assertEqual(1, len(gerunds))
        self.assertEqual(1, gerunds[0].line)

    def test_sentence_anchor_reports_the_actionable_line(self) -> None:
        text = "Кэш прогрет и работает.\nОднако, latency не упал."
        findings = SLOPSPANS.scan_text(
            "anchored.md", text, self.rules, register="technical"
        )
        calques = [
            finding
            for finding in findings
            if finding.rule.id == "grammar.punctuation-calque"
        ]
        self.assertEqual(1, len(calques))
        self.assertEqual(2, calques[0].line)

    def test_multiline_quote_is_protected(self) -> None:
        text = "«Стоит отметить,\nчто данный текст процитирован».\nФакт остаётся."
        ids = self.ids(text)
        self.assertNotIn("soft.empty-opening", ids)
        self.assertNotIn("soft.dannyi-adjective", ids)

    def test_straight_quote_is_protected_but_ref_attribute_is_scanned(self) -> None:
        self.assertEqual([], self.ids('Автор написал: "Стоит отметить, что данный текст".'))
        self.assertIn(
            "hygiene.ref-name",
            self.ids('<ref name="0search12">источник</ref>'),
        )

    def test_fence_blockquote_and_inline_code_are_protected(self) -> None:
        text = """\
```go
// Стоит отметить, что данный код — пример.
```
> В современном мире quoted text не правится.
Команда `echo "oaicite:5"` печатает fixture.
"""
        self.assertEqual([], self.ids(text, register="technical"))

    def test_technical_terms_are_not_candidates(self) -> None:
        text = (
            "Пайплайн читает topic. Producer пишет в broker, consumer group "
            "обрабатывает partition. Consumer lag растёт после 20k msg/s: "
            "handler упирается в seq scan. Retry отправляет message в DLQ, "
            "backpressure ограничивает requests. Rate limiter здесь не нужен."
        )
        self.assertEqual([], self.ids(text, register="technical"))

    def test_legitimate_technical_range_is_not_regex_flagged(self) -> None:
        text = "Запрос проходит от broker до consumer через один proxy."
        self.assertEqual([], self.ids(text, register="technical"))

    def test_docs_alias_matches_technical_register(self) -> None:
        text = "Проект демонстрирует приверженность качеству."
        technical = self.ids(text, register="technical")
        docs = self.ids(text, register="docs")
        self.assertEqual(technical, docs)

    def test_literal_system_behavior_is_not_inanimate_intention(self) -> None:
        text = "Система обеспечивает backpressure, limiter режет входящий поток."
        self.assertNotIn(
            "soft.inanimate-intention", self.ids(text, register="technical")
        )

    def test_register_suppresses_bureaucratese(self) -> None:
        text = "Регистрация осуществляется в соответствии с пунктом 4."
        self.assertIn("soft.bureaucratese", self.ids(text, register="general"))
        self.assertNotIn("soft.bureaucratese", self.ids(text, register="legal"))

    def test_legal_register_suppresses_cosmetic_soft_rules(self) -> None:
        text = """\
## Основные Принципы Работы Системы
Конечно! Данный порядок знаменует новую эру. Вот честный список без воды.
"""
        findings = SLOPSPANS.scan_text(
            "legal.md",
            text,
            self.rules,
            language="ru",
            register="legal",
        )
        cosmetic = [
            finding
            for finding in findings
            if finding.rule.id.startswith(("soft.", "format."))
        ]
        self.assertEqual([], cosmetic)

    def test_artifacts_only_exit_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            strict = Path(directory) / "strict.md"
            contextual = Path(directory) / "contextual.md"
            clean = Path(directory) / "clean.md"
            strict.write_text("source turn0search1", encoding="utf-8")
            contextual.write_text("[вставьте ссылку]", encoding="utf-8")
            clean.write_text("Сервис возвращает 200.", encoding="utf-8")

            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    1, SLOPSPANS.main(["--artifacts-only", str(strict)])
                )
                self.assertEqual(
                    0, SLOPSPANS.main(["--artifacts-only", str(contextual)])
                )
                self.assertEqual(
                    0, SLOPSPANS.main(["--artifacts-only", str(clean)])
                )

    def test_json_schema_and_observations_are_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.md"
            path.write_text("Стоит отметить, что retry включён.", encoding="utf-8")

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = SLOPSPANS.main(["--json", str(path)])
            payload = json.loads(buffer.getvalue())
            self.assertEqual(0, code)
            self.assertEqual(1, payload["schema_version"])
            self.assertEqual("markdown", payload["surface"])
            self.assertEqual("general", payload["register"])
            self.assertEqual([], payload["observations"])
            self.assertTrue(payload["spans"])

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = SLOPSPANS.main(["--json", "--observations", str(path)])
            payload = json.loads(buffer.getvalue())
            self.assertEqual(0, code)
            self.assertEqual(1, len(payload["observations"]))
            self.assertIn("sentence_length_cv", payload["observations"][0])

    def test_confluence_surface_skips_macros_attributes_code_and_quotes(self) -> None:
        text = (ROOT / "evals" / "fixtures" / "confluence-rfc-clean.xhtml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            [],
            self.ids(text, register="technical", surface="confluence-storage"),
        )

    def test_confluence_surface_scans_only_visible_editable_prose(self) -> None:
        text = (
            ROOT / "evals" / "fixtures" / "confluence-rfc-findings.xhtml"
        ).read_text(encoding="utf-8")
        findings = SLOPSPANS.scan_text(
            "confluence.xhtml",
            text,
            self.rules,
            language="ru",
            register="technical",
            surface="confluence-storage",
        )
        ids = [finding.rule.id for finding in findings]
        self.assertIn("soft.empty-opening", ids)
        self.assertIn("soft.dannyi-adjective", ids)
        self.assertIn("soft.promotional-language", ids)
        self.assertIn("format.russian-title-case", ids)
        self.assertNotIn("artifact.openai.turn-marker", ids)
        lines = {finding.rule.id: finding.line for finding in findings}
        self.assertEqual(6, lines["format.russian-title-case"])
        self.assertEqual(7, lines["soft.empty-opening"])

    def test_confluence_pretty_printed_four_space_indent_is_scanned(self) -> None:
        text = """\
<div>
    <p>Стоит отметить, что данный сервис важен.</p>
</div>
"""
        ids = self.ids(
            text,
            register="technical",
            surface="confluence-storage",
        )
        self.assertIn("soft.empty-opening", ids)
        self.assertIn("soft.dannyi-adjective", ids)

    def test_unmatched_quote_is_limited_to_its_paragraph(self) -> None:
        text = 'Диск на 5" установлен.\n\nСтоит отметить, что данный сервис важен.'
        ids = self.ids(text, register="technical")
        self.assertIn("soft.empty-opening", ids)
        self.assertIn("soft.dannyi-adjective", ids)

    def test_malformed_confluence_tags_neither_hide_nor_leak_macro_text(self) -> None:
        unclosed = """\
<ac:structured-macro ac:name="panel">
  <p>Текст внутри macro.
</ac:structured-macro>
<p>Стоит отметить, что данный внешний сервис важен.</p>
"""
        unclosed_ids = self.ids(
            unclosed,
            register="technical",
            surface="confluence-storage",
        )
        self.assertIn("soft.empty-opening", unclosed_ids)
        self.assertIn("soft.dannyi-adjective", unclosed_ids)

        extra_close = """\
<ac:structured-macro ac:name="panel">
  </span>
  <p>Стоит отметить, что данный внутренний сервис важен.</p>
</ac:structured-macro>
"""
        self.assertEqual(
            [],
            self.ids(
                extra_close,
                register="technical",
                surface="confluence-storage",
            ),
        )

    def test_confluence_space_variants_match_phrase_rules(self) -> None:
        for space in ("\u00a0", "\u202f", "\u2007"):
            with self.subTest(space=hex(ord(space))):
                ids = self.ids(
                    f"<p>Стоит{space}отметить, что данный сервис важен.</p>",
                    register="technical",
                    surface="confluence-storage",
                )
                self.assertIn("soft.empty-opening", ids)
                self.assertIn("soft.dannyi-adjective", ids)

    def test_visible_cdata_is_scanned_outside_protected_macro(self) -> None:
        text = (
            "<ac:link><ac:plain-text-link-body><![CDATA["
            "Стоит отметить, что данный сервис важен."
            "]]></ac:plain-text-link-body></ac:link>"
        )
        ids = self.ids(
            text,
            register="technical",
            surface="confluence-storage",
        )
        self.assertIn("soft.empty-opening", ids)
        self.assertIn("soft.dannyi-adjective", ids)

    def test_line_numbers_count_newlines_not_unicode_separators(self) -> None:
        text = "Префикс\u2028Стоит отметить, что сервис важен."
        findings = SLOPSPANS.scan_text(
            "separator.md",
            text,
            self.rules,
            language="ru",
            register="technical",
        )
        opening = next(
            item for item in findings if item.rule.id == "soft.empty-opening"
        )
        self.assertEqual(1, opening.line)

    def test_plain_surface_does_not_apply_markdown_blockquote_rules(self) -> None:
        text = "> Стоит отметить, что данный сервис важен."
        plain = self.ids(text, register="technical", surface="plain")
        chat = self.ids(text, register="technical", surface="chat")
        self.assertIn("soft.empty-opening", plain)
        self.assertIn("soft.dannyi-adjective", plain)
        self.assertEqual([], chat)

    def test_placeholder_is_case_insensitive(self) -> None:
        self.assertIn(
            "hygiene.placeholder",
            self.ids("[Укажите название компании]", register="technical"),
        )

    def test_normative_impersonal_gerund_is_not_flagged(self) -> None:
        ids = self.ids(
            "Учитывая нагрузку, нужно добавить реплику.",
            register="technical",
        )
        self.assertNotIn("grammar.dangling-gerund", ids)

    def test_correct_parenthetical_such_as_phrase_is_not_flagged(self) -> None:
        ids = self.ids(
            "Мы используем инструменты, такие как Prometheus и Grafana, для метрик.",
            register="technical",
        )
        self.assertNotIn("grammar.punctuation-calque", ids)

    def test_bom_remains_contextual_hygiene(self) -> None:
        ids = self.ids("\ufeffСервис работает.", register="technical")
        self.assertIn("hygiene.invisible-character", ids)

    def test_registry_validation_failure_paths(self) -> None:
        source = json.loads(
            (ROOT / "scripts" / "rules.json").read_text(encoding="utf-8")
        )
        cases = {}

        duplicate = copy.deepcopy(source)
        duplicate["rules"].append(copy.deepcopy(duplicate["rules"][0]))
        cases["duplicate"] = duplicate

        missing_source = copy.deepcopy(source)
        missing_source["rules"][0]["source"] = "unregistered-source"
        cases["source"] = missing_source

        bad_regex = copy.deepcopy(source)
        bad_regex["rules"][0]["pattern"] = "["
        cases["regex"] = bad_regex

        bad_examples = copy.deepcopy(source)
        del bad_examples["rules"][0]["examples"]["boundary"]
        cases["examples"] = bad_examples

        with tempfile.TemporaryDirectory() as directory:
            for name, payload in cases.items():
                with self.subTest(case=name):
                    path = Path(directory) / f"{name}.json"
                    path.write_text(
                        json.dumps(payload, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    with self.assertRaises(ValueError):
                        SLOPSPANS.load_rules(path)


if __name__ == "__main__":
    unittest.main()
