"""Tests for scripts/check_citations.py.

The script reads a library that lives outside this repository, so every test
here builds a small fake one. That keeps the standard test run portable and
still exercises the parts that were wrong three times: the two numbering
series, ranges and enumerations, and notes with their sub-items.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_spec = importlib.util.spec_from_file_location(
    "check_citations", SCRIPTS / "check_citations.py"
)
CHECK = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(CHECK)


def page(series: str, title: str, body: str) -> str:
    return f'---\ntitle: "{title}"\nsection_path: ["{series}","раздел"]\n---\n\n{body}\n'


def parse(tail: str):
    """(paragraphs, points) of the single address in `tail`."""
    paragraphs, points, _ = next(iter(CHECK.addresses_in(tail)))
    return paragraphs, points


class ParsingTests(unittest.TestCase):
    def test_a_range_names_every_paragraph_in_it(self) -> None:
        self.assertEqual([16, 17, 18], parse("§§ 16–18")[0])

    def test_an_enumeration_names_every_paragraph_listed(self) -> None:
        self.assertEqual([61, 63, 65], parse("§§ 61, 63, 65")[0])

    def test_point_numbers_are_not_read_as_paragraphs(self) -> None:
        self.assertEqual([210], parse("§ 210 пп. 2, 3 (в, г, д), 5")[0])

    def test_every_point_in_a_list_is_taken(self) -> None:
        self.assertEqual([2, 3, 5], parse("§ 210 пп. 2, 3 (в, г, д), 5")[1])

    def test_a_range_of_points_is_expanded(self) -> None:
        self.assertEqual([1, 2, 3, 4], parse("§ 204 пп. 1–4")[1])

    def test_a_second_address_after_a_semicolon_is_kept(self) -> None:
        got = [a[0] for a in CHECK.addresses_in("§ 140 п. 3, примечание; § 72")]
        self.assertEqual([[140], [72]], got)

    def test_a_hierarchical_point_is_one_number(self) -> None:
        # `п. 1.3` names one point, not points 1 and 3.
        self.assertEqual([1], parse("§ 213 п. 1.3")[1])

    def test_subpoint_is_not_mistaken_for_a_point(self) -> None:
        # `подп.` ends in `п.` and used to match the point pattern as well,
        # so one citation produced two complaints.
        self.assertEqual([], parse("§ 5, примечание, подп. 2")[1])

    def test_numerals_agree_with_their_count(self) -> None:
        self.assertEqual("пункт", CHECK.plural(21, "пункт", "пункта", "пунктов"))
        self.assertEqual("пункта", CHECK.plural(2, "пункт", "пункта", "пунктов"))
        self.assertEqual("пунктов", CHECK.plural(11, "пункт", "пункта", "пунктов"))

    def test_series_comes_from_the_front_matter(self) -> None:
        self.assertIs(True, CHECK.series_of(page("ПУНКТУАЦИЯ", "Что-то", "x")))
        self.assertIs(False, CHECK.series_of(page("ОРФОГРАФИЯ", "Что-то", "x")))
        self.assertIsNone(CHECK.series_of("# без front matter\n"))


class LibraryTests(unittest.TestCase):
    def build(self, directory: str, citation: str, pages: dict[str, str]):
        root = Path(directory) / "ru"
        (root / "references").mkdir(parents=True)
        (root / "references" / "sample.md").write_text(
            f"**Основание:** {citation}. Норма.\n", encoding="utf-8"
        )
        library = Path(directory) / "rules"
        (library / "lopatin").mkdir(parents=True)
        for name, text in pages.items():
            (library / "lopatin" / name).write_text(text, encoding="utf-8")
        return root, library

    def test_a_paragraph_of_the_other_series_does_not_satisfy_the_citation(self) -> None:
        # § 140 is «Служебные слова» in orthography and «Цитаты» in punctuation.
        with tempfile.TemporaryDirectory() as directory:
            root, library = self.build(
                directory,
                "[ПАС § 140]",
                {"orfo.md": page("ОРФОГРАФИЯ", "Служебные слова", "## § 140.\n\nтекст")},
            )
            problems = list(CHECK.check(root, library))
            self.assertTrue(any("не найден" in p for p in problems))

    def test_the_declared_series_is_believed_over_the_title(self) -> None:
        # The title carries no punctuation word, and that used to decide.
        with tempfile.TemporaryDirectory() as directory:
            root, library = self.build(
                directory,
                "[ПАС § 161]",
                {"p.md": page("ПУНКТУАЦИЯ", "Взаимодействие знаков", "## § 161.\n\nтекст")},
            )
            self.assertEqual([], list(CHECK.check(root, library)))

    def test_a_missing_paragraph_inside_an_enumeration_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, library = self.build(
                directory,
                "[ПАС §§ 61, 999]",
                {"p.md": page("ПУНКТУАЦИЯ", "Знаки препинания", "## § 61.\n\nтекст")},
            )
            problems = list(CHECK.check(root, library))
            self.assertTrue(any("§ 999" in p for p in problems))

    def test_a_note_that_is_not_there_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, library = self.build(
                directory,
                "[ПАС § 97, прим. 99]",
                {
                    "p.md": page(
                        "ПУНКТУАЦИЯ",
                        "Знаки препинания при вставках",
                        "## § 97.\n\nтекст\n\n**Примечание 1**\n\nтекст",
                    )
                },
            )
            problems = list(CHECK.check(root, library))
            self.assertTrue(any("примечания нет" in p for p in problems))

    def test_a_subpoint_of_a_note_is_checked(self) -> None:
        body = (
            "## § 5.\n\nтекст\n\nПримечание. Обычно тире не ставится:\n\n"
            "1) первое;\n\n2) второе.\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root, library = self.build(
                directory,
                "[ПАС § 5, примечание, подп. 77]",
                {"p.md": page("ПУНКТУАЦИЯ", "Тире между членами", body)},
            )
            problems = list(CHECK.check(root, library))
            self.assertEqual(1, len(problems), problems)
            self.assertIn("подпункта нет", problems[0])

    def test_a_subpoint_that_exists_passes(self) -> None:
        body = (
            "## § 5.\n\nтекст\n\nПримечание. Обычно тире не ставится:\n\n"
            "1) первое;\n\n2) второе.\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root, library = self.build(
                directory,
                "[ПАС § 5, примечание, подп. 2]",
                {"p.md": page("ПУНКТУАЦИЯ", "Тире между членами", body)},
            )
            self.assertEqual([], list(CHECK.check(root, library)))


    def test_a_note_belongs_to_the_point_that_names_it(self) -> None:
        # § 15 carries notes at points 1 and 5 but not at 2; searching the whole
        # paragraph made `п. 2, примечание` pass.
        body = (
            "## § 15.\n\n1. текст\n\n\tПримечание\n\tпервое\n\n"
            "2. текст без примечания\n\n3. текст\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root, library = self.build(
                directory,
                "[ПАС § 15 п. 2, примечание]",
                {"p.md": page("ПУНКТУАЦИЯ", "Тире между членами", body)},
            )
            problems = list(CHECK.check(root, library))
            self.assertEqual(1, len(problems), problems)
            self.assertIn("примечания нет", problems[0])

    def test_the_same_note_at_the_point_that_has_one_passes(self) -> None:
        body = (
            "## § 15.\n\n1. текст\n\n\tПримечание\n\tпервое\n\n"
            "2. текст без примечания\n\n3. текст\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root, library = self.build(
                directory,
                "[ПАС § 15 п. 1, примечание]",
                {"p.md": page("ПУНКТУАЦИЯ", "Тире между членами", body)},
            )
            self.assertEqual([], list(CHECK.check(root, library)))


if __name__ == "__main__":
    unittest.main()
