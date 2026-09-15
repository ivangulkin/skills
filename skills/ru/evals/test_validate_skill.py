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


if __name__ == "__main__":
    unittest.main()
