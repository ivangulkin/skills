#!/usr/bin/env python3
"""Tests for the standalone skill packaging guard."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_skill.py"
SPEC = importlib.util.spec_from_file_location("validate_skill", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot import {SCRIPT}")
VALIDATE_SKILL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VALIDATE_SKILL
SPEC.loader.exec_module(VALIDATE_SKILL)


class ValidateSkillTests(unittest.TestCase):
    def make_skill(
        self,
        directory: str,
        frontmatter: str,
        *,
        body: str = "",
        openai_yaml: str = "policy:\n  allow_implicit_invocation: true\n",
    ) -> Path:
        root = Path(directory) / "sample"
        root.mkdir()
        (root / "SKILL.md").write_text(
            f"---\n{frontmatter}\n---\n{body}",
            encoding="utf-8",
        )
        agents = root / "agents"
        agents.mkdir()
        (agents / "openai.yaml").write_text(openai_yaml, encoding="utf-8")
        references = root / "references"
        references.mkdir()
        (references / "rules.md").write_text(
            "# Rules\n\n## T1. Sample rule\n\nSee [T1].\n", encoding="utf-8"
        )
        rows = "\n".join(f"| `F{n}` | contract {n} |" for n in range(1, 9))
        (references / "contracts.md").write_text(
            f"# Contracts\n\n| id | what |\n|---|---|\n{rows}\n", encoding="utf-8"
        )
        return root

    def test_repository_is_one_standalone_skill(self) -> None:
        self.assertEqual([], VALIDATE_SKILL.validate(ROOT))

    def test_nested_duplicate_and_plugin_manifest_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "sample"
            root.mkdir()
            (root / "SKILL.md").write_text(
                "---\nname: sample\ndescription: Sample skill.\n---\n",
                encoding="utf-8",
            )
            agents = root / "agents"
            agents.mkdir()
            (agents / "openai.yaml").write_text(
                "policy:\n  allow_implicit_invocation: true\n",
                encoding="utf-8",
            )
            nested = root / "vendor"
            nested.mkdir()
            (nested / "SKILL.md").write_text(
                "---\nname: sample\ndescription: Duplicate.\n---\n",
                encoding="utf-8",
            )
            plugin = root / ".codex-plugin"
            plugin.mkdir()
            (plugin / "plugin.json").write_text("{}\n", encoding="utf-8")

            errors = VALIDATE_SKILL.validate(root)
            self.assertTrue(any("Expected one root SKILL.md" in item for item in errors))
            self.assertTrue(any("Plugin packaging" in item for item in errors))

    def test_name_must_be_in_frontmatter_not_body(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_skill(
                directory,
                "description: Sample skill.",
                body="\n```yaml\nname: sample\n```\n",
            )
            errors = VALIDATE_SKILL.validate(root)
            self.assertTrue(any("no name" in item.lower() for item in errors))

    def test_frontmatter_platform_constraints(self) -> None:
        cases = {
            "unexpected": "name: sample\ndescription: Sample skill.\nunknown: true",
            "bad_name": "name: Sample\ndescription: Sample skill.",
            "long_description": "name: sample\ndescription: " + "x" * 1025,
            "malformed_yaml": "name: [sample\ndescription: Sample skill.",
            "angle_description": "name: sample\ndescription: Use <sample>.",
            "todo_description": "name: sample\ndescription: '[TODO: finish]'",
        }
        for case, frontmatter in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = self.make_skill(directory, frontmatter)
                self.assertTrue(VALIDATE_SKILL.validate(root))

    def test_openai_policy_and_local_links_are_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_skill(
                directory,
                "name: sample\ndescription: Sample skill.",
                body="\n[missing](references/missing.md)\n",
                openai_yaml="policy:\n  allow_implicit_invocation: false\n",
            )
            errors = VALIDATE_SKILL.validate(root)
            self.assertTrue(any("implicit" in item.lower() for item in errors))
            self.assertTrue(any("Broken local link" in item for item in errors))


    def test_duplicate_rule_id_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_skill(directory, "name: sample\ndescription: Sample skill.")
            (root / "references" / "other.md").write_text(
                "# Other\n\n## T1. Same id in a second file\n", encoding="utf-8"
            )
            errors = VALIDATE_SKILL.validate(root)
            self.assertTrue(any("Duplicate rule id T1" in item for item in errors))

    def test_dangling_rule_reference_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_skill(directory, "name: sample\ndescription: Sample skill.")
            (root / "references" / "other.md").write_text(
                "# Other\n\nSee [T99] for details.\n", encoding="utf-8"
            )
            errors = VALIDATE_SKILL.validate(root)
            self.assertTrue(any("Dangling rule reference T99" in item for item in errors))

    def test_long_identifier_is_not_a_rule_reference(self) -> None:
        # A DOI or catalogue code shares the shape of a rule id and is not one.
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_skill(directory, "name: sample\ndescription: Sample skill.")
            (root / "references" / "other.md").write_text(
                "# Other\n\ndoi S0266078418000512 and issue T1234567.\n",
                encoding="utf-8",
            )
            self.assertEqual([], VALIDATE_SKILL.validate(root))

    def test_broken_anchor_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_skill(directory, "name: sample\ndescription: Sample skill.")
            (root / "references" / "other.md").write_text(
                "# Other\n\n[go](rules.md#no-such-anchor)\n", encoding="utf-8"
            )
            errors = VALIDATE_SKILL.validate(root)
            self.assertTrue(any("Broken anchor" in item for item in errors))

    def test_existing_anchor_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_skill(directory, "name: sample\ndescription: Sample skill.")
            (root / "references" / "other.md").write_text(
                '# Other\n\n<a id="spot"></a>\n\n[here](other.md#spot)\n'
                "[there](rules.md#t1-sample-rule)\n",
                encoding="utf-8",
            )
            self.assertEqual([], VALIDATE_SKILL.validate(root))


    def test_rule_defined_outside_references_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_skill(directory, "name: sample\ndescription: Sample skill.")
            integration = root / "integration"
            integration.mkdir()
            (integration / "dup.md").write_text(
                "# Guide\n\n## T1. Same id outside references\n", encoding="utf-8"
            )
            errors = VALIDATE_SKILL.validate(root)
            self.assertTrue(any("Duplicate rule id T1" in item for item in errors))
            self.assertTrue(
                any("defined outside references/" in item for item in errors)
            )

    def test_dangling_reference_outside_references_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_skill(directory, "name: sample\ndescription: Sample skill.")
            (root / "README.md").write_text("# Sample\n\nSee [T99].\n", encoding="utf-8")
            errors = VALIDATE_SKILL.validate(root)
            self.assertTrue(any("Dangling rule reference T99" in item for item in errors))

    def test_table_only_ids_must_exist_in_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_skill(directory, "name: sample\ndescription: Sample skill.")
            (root / "references" / "contracts.md").write_text(
                "# Contracts\n\nThe table is gone.\n", encoding="utf-8"
            )
            errors = VALIDATE_SKILL.validate(root)
            self.assertTrue(any("absent from contracts.md" in item for item in errors))

    def test_heading_inside_a_code_block_is_not_an_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_skill(directory, "name: sample\ndescription: Sample skill.")
            (root / "references" / "sample.md").write_text(
                "# Doc\n\n```\n## Secret\n```\n\n[go](sample.md#secret)\n",
                encoding="utf-8",
            )
            errors = VALIDATE_SKILL.validate(root)
            self.assertTrue(any("Broken anchor" in item for item in errors))

    def test_repeated_heading_gets_a_numbered_anchor(self) -> None:
        # GitHub appends -1, -2 … to repeated headings; both anchors resolve.
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_skill(directory, "name: sample\ndescription: Sample skill.")
            (root / "references" / "sample.md").write_text(
                "# Doc\n\n## Note\n\nfirst\n\n## Note\n\n"
                "[one](sample.md#note) [two](sample.md#note-1)\n",
                encoding="utf-8",
            )
            self.assertEqual([], VALIDATE_SKILL.validate(root))

    def test_f_ids_must_sit_in_a_table_row(self) -> None:
        # Every id is backticked, which is one of the forms a search of the
        # whole page accepts — so that search stays green here. The table is
        # gone, and only requiring a row catches that.
        ids = ", ".join(f"`F{n}`" for n in range(1, 9))
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_skill(directory, "name: sample\ndescription: Sample skill.")
            (root / "references" / "contracts.md").write_text(
                f"# Contracts\n\nThe contracts {ids} used to be in a table here.\n",
                encoding="utf-8",
            )
            errors = VALIDATE_SKILL.validate(root)
            self.assertTrue(any("absent from contracts.md" in item for item in errors))

    def test_short_fence_does_not_close_a_longer_one(self) -> None:
        # CommonMark: the closing fence is at least as long as the opening one.
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_skill(directory, "name: sample\ndescription: Sample skill.")
            (root / "references" / "sample.md").write_text(
                "# Doc\n\n````\n```\n## Secret\n```\n````\n\n[go](sample.md#secret)\n",
                encoding="utf-8",
            )
            errors = VALIDATE_SKILL.validate(root)
            self.assertTrue(any("Broken anchor" in item for item in errors))

    def test_suffix_collision_with_a_literal_heading(self) -> None:
        # Headings Note, Note, Note-1 take note, note-1, note-1-1.
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_skill(directory, "name: sample\ndescription: Sample skill.")
            (root / "references" / "sample.md").write_text(
                "# Doc\n\n## Note\n\na\n\n## Note\n\nb\n\n## Note-1\n\nc\n\n"
                "[x](sample.md#note-1) [y](sample.md#note-1-1)\n",
                encoding="utf-8",
            )
            self.assertEqual([], VALIDATE_SKILL.validate(root))

    def test_a_results_directory_elsewhere_is_not_exempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_skill(directory, "name: sample\ndescription: Sample skill.")
            other = root / "references" / "results"
            other.mkdir()
            (other / "note.md").write_text("# Note\n\nSee [T99].\n", encoding="utf-8")
            errors = VALIDATE_SKILL.validate(root)
            self.assertTrue(any("Dangling rule reference T99" in item for item in errors))

    def test_archived_results_are_read_for_duplicates_but_not_references(self) -> None:
        # Reports use P0/P1 as priorities; they are not rule references.
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_skill(directory, "name: sample\ndescription: Sample skill.")
            results = root / "evals" / "results"
            results.mkdir(parents=True)
            (results / "old.md").write_text(
                "# Report\n\nPriority P0 and P1 items.\n", encoding="utf-8"
            )
            self.assertEqual([], VALIDATE_SKILL.validate(root))


if __name__ == "__main__":
    unittest.main()
