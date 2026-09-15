#!/usr/bin/env python3
"""Validate the standalone no-ai-slop skill layout and local references."""

from __future__ import annotations

import argparse
from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[1]
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---(?:\n|\Z)", re.DOTALL)
MARKDOWN_LINK = re.compile(r"\]\(([^)]+)\)")
SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ALLOWED_FRONTMATTER = {
    "name",
    "description",
    "license",
    "allowed-tools",
    "metadata",
    # A pointer skill keeps its old name resolvable without competing for
    # automatic invocation. Claude Code reads this key; Codex reads
    # policy.allow_implicit_invocation in agents/openai.yaml.
    "disable-model-invocation",
}
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024


def _skill_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("SKILL.md")
        if not {".git", "__pycache__"}.intersection(path.relative_to(root).parts)
    )


def _broken_local_links(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    broken: list[str] = []
    for raw_target in MARKDOWN_LINK.findall(text):
        target = raw_target.split("#", 1)[0]
        if not target or "://" in target or target.startswith("mailto:"):
            continue
        if not (path.parent / target).resolve().exists():
            broken.append(raw_target)
    return broken


def validate(root: Path = ROOT) -> list[str]:
    """Return deterministic layout errors; an empty list means valid."""
    root = root.resolve()
    errors: list[str] = []
    entrypoint = root / "SKILL.md"
    if not entrypoint.is_file():
        return ["Root SKILL.md is missing"]

    skill_files = _skill_files(root)
    if skill_files != [entrypoint]:
        relative = [str(path.relative_to(root)) for path in skill_files]
        errors.append(f"Expected one root SKILL.md, found: {relative}")

    text = entrypoint.read_text(encoding="utf-8")
    match = FRONTMATTER.match(text)
    frontmatter: dict[str, object] = {}
    if match is None:
        errors.append("SKILL.md frontmatter is missing or not closed")
    else:
        try:
            loaded = yaml.safe_load(match.group(1))
        except yaml.YAMLError as exc:
            errors.append(f"Invalid SKILL.md YAML frontmatter: {exc}")
        else:
            if isinstance(loaded, dict):
                frontmatter = loaded
            else:
                errors.append("SKILL.md frontmatter must be a YAML mapping")

    unexpected = set(frontmatter) - ALLOWED_FRONTMATTER
    if unexpected:
        errors.append(f"Unexpected SKILL.md frontmatter keys: {sorted(unexpected)}")

    name = frontmatter.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("SKILL.md has no name in frontmatter")
    else:
        name = name.strip()
        if len(name) > MAX_NAME_LENGTH or not SKILL_NAME.fullmatch(name):
            errors.append(f"Invalid skill name: {name!r}")
        if name != root.name:
            errors.append(f"Skill name {name!r} does not match {root.name!r}")

    description = frontmatter.get("description")
    if not isinstance(description, str) or not description.strip():
        errors.append("SKILL.md has no description in frontmatter")
    else:
        description = description.strip()
        if len(description) > MAX_DESCRIPTION_LENGTH:
            errors.append(
                f"SKILL.md description exceeds {MAX_DESCRIPTION_LENGTH} characters"
            )
        if "<" in description or ">" in description:
            errors.append("SKILL.md description cannot contain angle brackets")
        if description.startswith("[TODO:"):
            errors.append("SKILL.md description contains an unfinished TODO")

    forbidden = [
        path
        for path in root.rglob("plugin.json")
        if ".codex-plugin" in path.parts and ".git" not in path.parts
    ]
    forbidden.extend(
        path
        for path in root.rglob("marketplace.json")
        if path.parent.name == "plugins"
        and path.parent.parent.name == ".agents"
        and ".git" not in path.parts
    )
    for path in sorted(set(forbidden)):
        errors.append(f"Plugin packaging is not allowed: {path.relative_to(root)}")

    openai_yaml = root / "agents" / "openai.yaml"
    if not openai_yaml.is_file():
        errors.append("agents/openai.yaml is missing")
    else:
        try:
            openai_data = yaml.safe_load(openai_yaml.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            errors.append(f"Invalid agents/openai.yaml: {exc}")
        else:
            if not isinstance(openai_data, dict):
                errors.append("agents/openai.yaml must be a YAML mapping")
            else:
                policy = openai_data.get("policy")
                # A pointer skill and the skill it points at have opposite
                # contracts: exactly one of them may be invoked implicitly,
                # or the two compete for the same triggers again.
                wants_implicit = frontmatter.get("disable-model-invocation") is not True
                actual = (
                    policy.get("allow_implicit_invocation")
                    if isinstance(policy, dict)
                    else None
                )
                if not isinstance(policy, dict) or actual is not wants_implicit:
                    errors.append(
                        "agents/openai.yaml policy.allow_implicit_invocation must be "
                        f"{wants_implicit} to match SKILL.md frontmatter"
                    )

    # Fixtures and eval inputs carry deliberately broken citation artifacts;
    # they are scanner input, not documentation.
    skipped = {".git", "__pycache__", "fixtures", "inputs"}
    markdown_files = sorted(
        path
        for path in root.rglob("*.md")
        if not skipped.intersection(path.relative_to(root).parts)
    )
    for path in markdown_files:
        for target in _broken_local_links(path):
            errors.append(f"Broken local link in {path.relative_to(root)}: {target}")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate one standalone, implicitly invoked skill folder."
    )
    parser.add_argument("skill", nargs="?", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        errors = validate(args.skill)
    except (OSError, UnicodeError) as exc:
        print(f"Cannot validate skill: {exc}")
        return 2
    if errors:
        print("Standalone skill validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Standalone skill validation passed: one root SKILL.md, no plugin packaging.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
