#!/usr/bin/env python3
"""Validate the standalone ru skill layout, local references and rule IDs."""

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

# A rule heading looks like "## T17. …" or "### Z20. …". The prefix says which
# reference file owns it; the whole tree shares one namespace, so a duplicate is
# an error wherever the two copies live.
RULE_HEADING = re.compile(r"^#{2,3} ([A-Z])(\d+)\.", re.MULTILINE)
# A bare reference to a rule: `[T17]`, `T17`, `G9–G15` is a range and is left
# alone because its endpoints are checked separately. Rule numbers are one or
# two digits; the bound keeps DOIs and catalogue codes out — `S0266078418000512`
# is not rule S0266078418000512.
RULE_MENTION = re.compile(r"(?<![A-Za-z0-9])([A-Z])(\d{1,2})(?![0-9A-Za-z])")
# F1–F8 live in a table in contracts.md rather than in headings; they are real
# identifiers with no heading of their own.
TABLE_ONLY_IDS = {f"F{n}" for n in range(1, 9)}
# Archived records rather than routing documents: their own letter-and-digit
# codes are priorities, not rules. Matched as an exact relative path so a
# directory named "results" elsewhere in the tree is not silently exempt.
ARCHIVE_PATHS = (("evals", "results"),)


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
    markdown_files = _markdown_files(root)
    for path in markdown_files:
        for target in _broken_local_links(path):
            errors.append(f"Broken local link in {path.relative_to(root)}: {target}")
        for anchor in _broken_local_anchors(root, path):
            errors.append(f"Broken anchor in {path.relative_to(root)}: {anchor}")

    errors.extend(_rule_id_errors(root))

    return errors


def _markdown_files(root: Path) -> list[Path]:
    skipped = {".git", "__pycache__", "fixtures", "inputs"}
    return sorted(
        path
        for path in root.rglob("*.md")
        if not skipped.intersection(path.relative_to(root).parts)
    )


def _without_code(text: str) -> str:
    """Blank out fenced code so a heading inside a sample is not an anchor.

    The lines are kept, blanked rather than deleted, so reported line numbers
    and the shape of the file stay the same.
    """
    out: list[str] = []
    fence: tuple[str, int] | None = None
    for line in text.splitlines():
        stripped = line.lstrip()
        marker = re.match(r"(`{3,}|~{3,})", stripped)
        if fence is None:
            if marker:
                fence = (marker.group(1)[0], len(marker.group(1)))
            out.append("" if marker else line)
            continue
        # CommonMark: the closing fence uses the same character and is at least
        # as long as the opening one, so ``` does not close ````.
        # https://spec.commonmark.org/0.31.2/#fenced-code-blocks
        if marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= fence[1]:
            fence = None
        out.append("")
    return "\n".join(out)


def _slug(heading: str) -> str:
    """GitHub-style anchor for a Markdown heading."""
    text = heading.strip().lstrip("#").strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return re.sub(r"\s+", "-", text)


def _anchors(path: Path) -> set[str]:
    text = _without_code(path.read_text(encoding="utf-8"))
    anchors: set[str] = set()
    # GitHub disambiguates repeated headings by appending -1, -2, ... in order
    # of appearance: https://docs.github.com/en/get-started/writing-on-github/
    # getting-started-with-writing-and-formatting-on-github/
    # basic-writing-and-formatting-syntax#section-links
    seen: dict[str, int] = {}
    for line in text.splitlines():
        if not line.lstrip().startswith("#"):
            continue
        base = _slug(line)
        count = seen.get(base, 0)
        candidate = base if count == 0 else f"{base}-{count}"
        # A heading literally named "Note-1" can already own that anchor, so
        # keep counting until the name is free — which is what GitHub does.
        while candidate in anchors:
            count += 1
            candidate = f"{base}-{count}"
        anchors.add(candidate)
        seen[base] = count + 1
    anchors |= set(re.findall(r'<a id="([^"]+)"', text))
    return anchors


def _broken_local_anchors(root: Path, path: Path) -> list[str]:
    """Report links whose target file exists but whose #anchor does not."""
    text = _without_code(path.read_text(encoding="utf-8"))
    broken: list[str] = []
    for raw_target in MARKDOWN_LINK.findall(text):
        if "#" not in raw_target or "://" in raw_target:
            continue
        target, anchor = raw_target.split("#", 1)
        if not anchor:
            continue
        destination = (path.parent / target).resolve() if target else path
        if not destination.is_file() or destination.suffix != ".md":
            continue
        if anchor not in _anchors(destination):
            broken.append(raw_target)
    return broken


def _rule_id_errors(root: Path) -> list[str]:
    """Rule identifiers are unique across the tree and every mention resolves."""
    references = root / "references"
    if not references.is_dir():
        return ["references/ is missing"]

    errors: list[str] = []
    # A rule may only be defined under references/, but a stray copy of the
    # heading anywhere else is still a duplicate — that is what makes the id
    # ambiguous. So definitions are collected from the whole tree.
    searched = _markdown_files(root)
    defined: dict[str, str] = {}
    for path in searched:
        text = _without_code(path.read_text(encoding="utf-8"))
        for prefix, number in RULE_HEADING.findall(text):
            rule = f"{prefix}{number}"
            where = str(path.relative_to(root))
            if rule in defined:
                errors.append(
                    f"Duplicate rule id {rule}: {defined[rule]} and {where}"
                )
            else:
                defined[rule] = where
                if path.parent != references:
                    errors.append(
                        f"Rule {rule} is defined outside references/: {where}"
                    )

    # F1-F8 have no heading of their own; they live in a table in contracts.md.
    # Trusting the constant alone would let the table disappear unnoticed, so
    # each one has to be found in that file.
    contracts = references / "contracts.md"
    contracts_text = (
        _without_code(contracts.read_text(encoding="utf-8")) if contracts.is_file() else ""
    )
    # The id has to sit in a table row, not merely somewhere on the page:
    # a sentence mentioning F1 must not keep the check green after the table
    # itself is deleted.
    table_rows = [
        line for line in contracts_text.splitlines() if line.lstrip().startswith("|")
    ]
    table_ids = {
        rule
        for rule in TABLE_ONLY_IDS
        if any(re.search(rf"(?<![A-Za-z0-9]){rule}(?![0-9A-Za-z])", row) for row in table_rows)
    }
    for rule in sorted(TABLE_ONLY_IDS - table_ids):
        errors.append(f"Rule {rule} is claimed by the validator but absent from contracts.md")

    known = set(defined) | table_ids
    # Only prefixes that actually name rules; a stray "T3" inside a version
    # string or an English word is not a reference.
    prefixes = {rule[0] for rule in known}
    # Archived reports use their own letter-and-digit codes — P0/P1/P2 there are
    # priorities, not rule P0 — so they are read for duplicate definitions but
    # not for references. Everything a reader is routed through stays in scope.
    for path in searched:
        parts = path.relative_to(root).parts
        if any(parts[: len(prefix)] == prefix for prefix in ARCHIVE_PATHS):
            continue
        for line in _without_code(path.read_text(encoding="utf-8")).splitlines():
            if RULE_HEADING.match(line):
                continue
            # Ranges like G9-G15 name their endpoints; both get checked.
            for prefix, number in RULE_MENTION.findall(line):
                if prefix not in prefixes:
                    continue
                rule = f"{prefix}{number}"
                if rule not in known:
                    errors.append(
                        f"Dangling rule reference {rule} in "
                        f"{path.relative_to(root)}"
                    )
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
