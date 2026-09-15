#!/usr/bin/env python3
"""Report mechanically detectable prose findings as exact spans.

The scanner has four rule tiers, three of which run by default:

* artifact-strict: narrow tool/service residue. ``--artifacts-only`` scans
  only this tier and exits 1 on a hit, which makes it suitable for CI.
* hygiene-contextual: placeholders, tracking parameters, and invisible
  characters with legitimate non-AI sources. Report-only.
* soft-candidate: lexical and syntactic locations that require editorial
  judgment, register gating, and Terminology Lock. Report-only.
* demo-only: OFF by default, enabled with ``--demo-rules``. Grammar patterns
  kept to show where regex recognition stops, not to check Russian. A regex
  cannot find the subject of a sentence, so these fire on correct text; they
  are not part of the normal check path.

The report always ends with what was NOT inspected. A clean run means no
pattern matched, never that the text is correct.

Raw document measurements are opt-in through ``--observations``. They have no
quality threshold and are never converted to an authorship or "slop" score.

Fenced and indented code, inline code, block quotes, and quoted prose are
protected. ``--surface confluence-storage`` extracts visible XHTML text while
protecting tag attributes, structured macros, code, and quoted blocks.
Hard-wrapped prose is joined by paragraph so multi-line patterns can still
match while line numbers point to the first matched line.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import statistics
import sys
from typing import Iterable


RULES_PATH = Path(__file__).with_name("rules.json")
TIERS = ("artifact-strict", "hygiene-contextual", "soft-candidate", "demo-only")
DEFAULT_TIERS = frozenset(TIERS[:3])
NOT_CHECKED = (
    "Не проверялось: факты и числа, причинность, отрицание, область условия, "
    "статус и уверенность, сохранность цитат и терминов, связность, структура, "
    "соответствие источнику. Совпадение — повод открыть карточку, не диагноз."
)
REGISTERS = (
    "general",
    "technical",
    "docs",
    "marketing",
    "article",
    "business",
    "scientific",
    "legal",
    "chat",
    "commit",
    "literary",
    "personal",
)
SURFACES = ("plain", "markdown", "confluence-storage", "chat")

INLINE_CODE = re.compile(r"`[^`\n]*`")
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING_OR_ITEM = re.compile(r"^\s*(?:#{1,6}\s|[-+*]\s|\d+[.)]\s)")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")
WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
QUOTES = {"«": "»", "“": "”", '"': '"'}
CONFLUENCE_PROTECTED_TAGS = {
    "ac:structured-macro",
    "ac:task-list",
    "blockquote",
    "code",
    "pre",
    "q",
    "script",
    "style",
}
CONFLUENCE_BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "figure",
    "footer",
    "header",
    "hr",
    "main",
    "nav",
    "ol",
    "p",
    "section",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}
CONFLUENCE_VOID_TAGS = {
    "br",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "source",
    "wbr",
}
SPACE_EQUIVALENTS = str.maketrans({"\u00a0": " ", "\u202f": " ", "\u2007": " "})


@dataclass(frozen=True)
class Rule:
    id: str
    label: str
    tier: str
    language: str
    pattern: str
    hint: str
    off_registers: tuple[str, ...]
    source: str
    examples: dict[str, str]
    regex: re.Pattern[str]


@dataclass(frozen=True)
class Segment:
    start_line: int
    text: str


@dataclass(frozen=True)
class Finding:
    rule: Rule
    path: str
    line: int
    text: str


class ConfluenceVisibleTextParser(HTMLParser):
    """Extract editable prose from Confluence storage XHTML.

    Tag attributes and structured macros are document infrastructure, not
    prose. They remain available to ``termguard.py`` for byte-stability checks
    but never reach lexical rules here.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[str] = []
        self.protected_count = 0

    def _append_boundary(self) -> None:
        if self.parts and not self.parts[-1][-1:].isspace():
            self.parts.append(" ")

    def _preserve_tag_newlines(self) -> None:
        raw = self.get_starttag_text() or ""
        if "\n" in raw:
            self.parts.append("\n" * raw.count("\n"))

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        del attrs
        tag = tag.lower()
        self._preserve_tag_newlines()
        if tag in CONFLUENCE_VOID_TAGS:
            if self.protected_count == 0 and tag in CONFLUENCE_BLOCK_TAGS:
                self._append_boundary()
            return
        self.stack.append(tag)
        if tag in CONFLUENCE_PROTECTED_TAGS:
            self.protected_count += 1
            return
        if self.protected_count > 0:
            return
        if re.fullmatch(r"h[1-6]", tag):
            self._append_boundary()
            self.parts.append("#" * int(tag[1]) + " ")
        elif tag == "li":
            self._append_boundary()
            self.parts.append("- ")
        elif tag in CONFLUENCE_BLOCK_TAGS:
            self._append_boundary()

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        del attrs
        tag = tag.lower()
        self._preserve_tag_newlines()
        if self.protected_count == 0 and tag in CONFLUENCE_BLOCK_TAGS:
            self._append_boundary()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        try:
            index = len(self.stack) - 1 - self.stack[::-1].index(tag)
        except ValueError:
            return
        popped = self.stack[index:]
        del self.stack[index:]
        self.protected_count = max(
            0,
            self.protected_count
            - sum(item in CONFLUENCE_PROTECTED_TAGS for item in popped),
        )
        if self.protected_count > 0 or tag in CONFLUENCE_PROTECTED_TAGS:
            return
        if (
            tag in CONFLUENCE_BLOCK_TAGS
            or tag == "li"
            or re.fullmatch(r"h[1-6]", tag)
        ):
            self._append_boundary()

    def handle_data(self, data: str) -> None:
        if self.protected_count > 0:
            self.parts.append("\n" * data.count("\n"))
            return
        self.parts.append(data)

    def handle_comment(self, data: str) -> None:
        self.parts.append("\n" * data.count("\n"))

    def handle_decl(self, decl: str) -> None:
        self.parts.append("\n" * decl.count("\n"))

    def unknown_decl(self, data: str) -> None:
        if data.startswith("CDATA["):
            content = data[6:]
            if self.protected_count > 0:
                self.parts.append("\n" * content.count("\n"))
            else:
                self.parts.append(content)
            return
        self.parts.append("\n" * data.count("\n"))

    def text(self) -> str:
        return "".join(self.parts)


def visible_surface_text(text: str, surface: str) -> str:
    """Return prose-bearing text for the selected document surface."""
    if surface != "confluence-storage":
        return text.translate(SPACE_EQUIVALENTS)
    parser = ConfluenceVisibleTextParser()
    parser.feed(text)
    parser.close()
    return parser.text().translate(SPACE_EQUIVALENTS)


def load_rules(path: Path | str = RULES_PATH) -> list[Rule]:
    """Load and validate the rule registry."""
    registry_path = Path(path)
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 2 or not isinstance(data.get("rules"), list):
        raise ValueError(f"Unsupported rule registry: {registry_path}")
    source_registry = data.get("source_registry")
    if not isinstance(source_registry, dict) or not source_registry:
        raise ValueError(f"Rule registry has no source_registry: {registry_path}")
    for source_id, record in source_registry.items():
        if not isinstance(record, dict) or not {
            "kind",
            "accessed",
            "immutable",
            "claim",
        }.issubset(record):
            raise ValueError(f"Incomplete source record: {source_id}")
        if not isinstance(record["immutable"], bool):
            raise ValueError(f"Invalid immutable flag: {source_id}")

    required = {
        "id",
        "label",
        "tier",
        "language",
        "pattern",
        "hint",
        "off_registers",
        "source",
        "examples",
    }
    seen: set[str] = set()
    rules: list[Rule] = []
    for raw in data["rules"]:
        missing = required - set(raw)
        if missing:
            raise ValueError(f"Rule is missing fields {sorted(missing)}: {raw}")
        rule_id = raw["id"]
        if rule_id in seen:
            raise ValueError(f"Duplicate rule id: {rule_id}")
        if raw["tier"] not in TIERS:
            raise ValueError(f"Unknown tier for {rule_id}: {raw['tier']}")
        if raw["language"] not in ("any", "ru", "en"):
            raise ValueError(f"Unknown language for {rule_id}: {raw['language']}")
        off = tuple(raw["off_registers"])
        unknown_registers = set(off) - set(REGISTERS)
        if unknown_registers:
            raise ValueError(
                f"Unknown off_registers for {rule_id}: {sorted(unknown_registers)}"
            )
        examples = raw["examples"]
        if not isinstance(examples, dict) or not {
            "positive",
            "negative",
            "boundary",
        }.issubset(examples):
            raise ValueError(f"Rule {rule_id} needs positive/negative/boundary examples")
        source_ids = [part.strip() for part in raw["source"].split(";")]
        missing_sources = [
            source_id for source_id in source_ids if source_id not in source_registry
        ]
        if missing_sources:
            raise ValueError(
                f"Rule {rule_id} has unregistered sources: {missing_sources}"
            )
        try:
            regex = re.compile(raw["pattern"])
        except re.error as exc:
            raise ValueError(f"Invalid regex for {rule_id}: {exc}") from exc
        seen.add(rule_id)
        rules.append(
            Rule(
                id=rule_id,
                label=raw["label"],
                tier=raw["tier"],
                language=raw["language"],
                pattern=raw["pattern"],
                hint=raw["hint"],
                off_registers=off,
                source=raw["source"],
                examples=examples,
                regex=regex,
            )
        )
    return rules


def _flush(buffer: list[tuple[int, str]], out: list[Segment]) -> None:
    if not buffer:
        return
    out.append(Segment(buffer[0][0], "\n".join(line for _, line in buffer)))
    buffer.clear()


def _protect_quotes(line: str, quote_end: str | None) -> tuple[str, str | None]:
    """Blank quoted text while preserving character offsets and quote state."""
    chars = list(line)
    i = 0
    in_tag = False
    while i < len(chars):
        ch = chars[i]
        if quote_end is not None:
            chars[i] = " "
            if ch == quote_end:
                quote_end = None
            i += 1
            continue
        if ch == "<":
            in_tag = True
        elif ch == ">":
            in_tag = False
        if ch in QUOTES and not (ch == '"' and in_tag):
            quote_end = QUOTES[ch]
            chars[i] = " "
            i += 1
            continue
        i += 1
    return "".join(chars), quote_end


def prose_segments(text: str, *, surface: str = "markdown") -> list[Segment]:
    """Return prose segments with protected material removed."""
    if surface not in SURFACES:
        raise ValueError(f"Unknown surface: {surface}")
    text = visible_surface_text(text, surface)
    markdown_markup = surface in {"markdown", "chat"}
    segments: list[Segment] = []
    buffer: list[tuple[int, str]] = []
    fenced = False
    quote_end: str | None = None

    for line_no, original in enumerate(text.split("\n"), 1):
        fence_match = FENCE.match(original) if markdown_markup else None
        if fence_match:
            _flush(buffer, segments)
            fenced = not fenced
            quote_end = None
            continue
        if markdown_markup and (
            fenced or original.startswith("    ") or original.startswith("\t")
        ):
            _flush(buffer, segments)
            continue
        if markdown_markup and original.lstrip().startswith(">"):
            _flush(buffer, segments)
            quote_end = None
            continue

        if not original.strip():
            _flush(buffer, segments)
            quote_end = None
            continue

        line = original
        if markdown_markup:
            line = INLINE_CODE.sub(lambda match: " " * len(match.group(0)), line)
        line, quote_end = _protect_quotes(line, quote_end)
        if not line.strip():
            _flush(buffer, segments)
            continue

        if markdown_markup and HEADING_OR_ITEM.match(line):
            _flush(buffer, segments)
            segments.append(Segment(line_no, line))
            continue
        buffer.append((line_no, line))

    _flush(buffer, segments)
    return segments


def _language_enabled(rule: Rule, language: str) -> bool:
    if rule.language == "any":
        return True
    return language == "both" or rule.language == language


def applicable_rules(
    rules: Iterable[Rule],
    *,
    language: str,
    register: str,
    tiers: set[str] | None = None,
) -> list[Rule]:
    """Filter rules by language, register, and tier."""
    if register == "docs":
        register = "technical"
    return [
        rule
        for rule in rules
        if _language_enabled(rule, language)
        and register not in rule.off_registers
        and rule.tier in (DEFAULT_TIERS if tiers is None else tiers)
    ]


def scan_text(
    path: str,
    text: str,
    rules: Iterable[Rule],
    *,
    language: str = "ru",
    register: str = "general",
    surface: str = "markdown",
    tiers: set[str] | None = None,
) -> list[Finding]:
    """Scan protected prose and return located findings."""
    active = applicable_rules(
        rules, language=language, register=register, tiers=tiers
    )
    findings: list[Finding] = []
    for segment in prose_segments(text, surface=surface):
        for rule in active:
            for match in rule.regex.finditer(segment.text):
                raw = match.group(0)
                # Sentence-anchored rules swallow the previous sentence's
                # terminator, which can sit on the previous line. Report the
                # line and the span of the first character the editor has to
                # act on. A match made entirely of that punctuation keeps it.
                stripped = raw.lstrip(" \t\r\n.!?")
                anchor = len(raw) - len(stripped) if stripped else 0
                fragment = re.sub(r"\s+", " ", raw[anchor:]).strip()
                if not fragment:
                    continue
                line = segment.start_line + segment.text[
                    : match.start() + anchor
                ].count("\n")
                findings.append(Finding(rule, path, line, fragment))
    return findings


def observations(path: str, text: str, *, surface: str = "markdown") -> dict[str, object]:
    """Return raw document measurements without thresholds or interpretation."""
    segments = prose_segments(text, surface=surface)
    body_parts = [
        segment.text
        for segment in segments
        if not segment.text.lstrip().startswith("#")
    ]
    body = "\n".join(body_parts)
    sentences = [
        sentence
        for sentence in SENTENCE_SPLIT.split(body)
        if WORD.search(sentence)
    ]
    lengths = [len(WORD.findall(sentence)) for sentence in sentences]
    words = sum(lengths)
    mean = statistics.fmean(lengths) if lengths else None
    cv = None
    if len(lengths) >= 2 and mean:
        cv = statistics.pstdev(lengths) / mean

    headings = [
        segment for segment in segments if segment.text.lstrip().startswith("#")
    ]
    list_items = [
        segment
        for segment in segments
        if re.match(r"^\s*(?:[-+*]\s|\d+[.)]\s)", segment.text)
    ]
    return {
        "file": path,
        "surface": surface,
        "words": words,
        "sentences": len(lengths),
        "sentence_words_min": min(lengths) if lengths else None,
        "sentence_words_max": max(lengths) if lengths else None,
        "sentence_words_mean": round(mean, 2) if mean is not None else None,
        "sentence_length_cv": round(cv, 3) if cv is not None else None,
        "em_dash_count": body.count("—"),
        "headings": len(headings),
        "list_items": len(list_items),
    }


def _finding_dict(finding: Finding) -> dict[str, object]:
    return {
        "rule_id": finding.rule.id,
        "tier": finding.rule.tier,
        "label": finding.rule.label,
        "hint": finding.rule.hint,
        "source": finding.rule.source,
        "file": finding.path,
        "line": finding.line,
        "text": finding.text,
    }


def _render(findings: list[Finding], raw_observations: list[dict[str, object]]) -> str:
    if not findings and not raw_observations:
        return "Механических совпадений не найдено.\n" + NOT_CHECKED

    tier_titles = {
        "artifact-strict": "STRICT TOOL RESIDUE",
        "hygiene-contextual": "CONTEXTUAL HYGIENE",
        "soft-candidate": "SOFT CANDIDATES",
        "demo-only": "DEMO ONLY — границы распознавания, не результат проверки",
    }
    lines: list[str] = []
    for tier in TIERS:
        tier_findings = [finding for finding in findings if finding.rule.tier == tier]
        if not tier_findings:
            continue
        lines.append(tier_titles[tier])
        grouped: dict[str, list[Finding]] = {}
        for finding in tier_findings:
            grouped.setdefault(finding.rule.id, []).append(finding)
        for rule_id in sorted(grouped):
            hits = grouped[rule_id]
            rule = hits[0].rule
            lines.append(f"\n{rule.label} [{rule.id}] — {len(hits)}")
            lines.append(f"  {rule.hint}")
            for hit in hits[:12]:
                lines.append(f"    {hit.path}:{hit.line}  {hit.text}")
            if len(hits) > 12:
                lines.append(f"    … ещё {len(hits) - 12}")
        lines.append("")

    if raw_observations:
        lines.append("RAW OBSERVATIONS — без thresholds и quality verdict")
        for item in raw_observations:
            lines.append(
                "  {file} [{surface}]: words={words}, sentences={sentences}, "
                "sentence_min={sentence_words_min}, sentence_max={sentence_words_max}, "
                "sentence_mean={sentence_words_mean}, sentence_cv={sentence_length_cv}, "
                "em_dash={em_dash_count}, headings={headings}, list_items={list_items}".format(
                    **item
                )
            )

    lines.append(
        "Это spans и raw observations, не score и не authorship verdict. "
        "Contextual hygiene требует проверки source; soft candidate требует "
        "register, Terminology Lock и редакторского решения."
    )
    lines.append(NOT_CHECKED)
    return "\n".join(lines).rstrip()


def _read_source(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report prose findings as spans. No score or authorship verdict."
    )
    parser.add_argument("files", nargs="+")
    parser.add_argument("--lang", choices=("ru", "en", "both"), default="ru")
    parser.add_argument("--register", choices=REGISTERS, default="general")
    parser.add_argument("--surface", choices=SURFACES, default="markdown")
    parser.add_argument(
        "--artifacts-only",
        action="store_true",
        help="scan artifact-strict only; exit 1 on a hit",
    )
    parser.add_argument(
        "--demo-rules",
        action="store_true",
        help="also run demo-only grammar patterns; they fire on correct text",
    )
    parser.add_argument(
        "--observations",
        action="store_true",
        help="add raw document measurements without thresholds",
    )
    parser.add_argument("--no-structure", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--rules", type=Path, default=RULES_PATH)
    args = parser.parse_args(argv)

    try:
        rules = load_rules(args.rules)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Rule registry error: {exc}", file=sys.stderr)
        return 2

    findings: list[Finding] = []
    raw_observations: list[dict[str, object]] = []
    if args.artifacts_only:
        tiers: set[str] | None = {"artifact-strict"}
    elif args.demo_rules:
        tiers = set(TIERS)
    else:
        tiers = None
    for path in args.files:
        try:
            text = _read_source(path)
        except (OSError, UnicodeError) as exc:
            print(f"Cannot read {path}: {exc}", file=sys.stderr)
            return 2
        findings.extend(
            scan_text(
                path,
                text,
                rules,
                language=args.lang,
                register=args.register,
                surface=args.surface,
                tiers=tiers,
            )
        )
        if args.observations and not args.artifacts_only and not args.no_structure:
            raw_observations.append(observations(path, text, surface=args.surface))

    if args.json:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "surface": args.surface,
                    "register": args.register,
                    "spans": [_finding_dict(finding) for finding in findings],
                    "observations": raw_observations,
                    "not_checked": NOT_CHECKED,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(_render(findings, raw_observations))

    strict_found = any(
        finding.rule.tier == "artifact-strict" for finding in findings
    )
    return 1 if args.artifacts_only and strict_found else 0


if __name__ == "__main__":
    raise SystemExit(main())
