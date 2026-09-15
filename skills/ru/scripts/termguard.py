#!/usr/bin/env python3
"""Check deterministic Fact-Lock and Terminology Lock invariants.

The checker compares a source document with an edited document. It verifies:

* no numeric token or URL was added or removed;
* Markdown code/quotes/logs and Confluence protected blocks remain
  byte-identical for the selected surface;
* Confluence storage tags, attributes, macro bodies, and identifiers remain
  byte-identical when ``--surface confluence-storage`` is selected;
* every explicitly supplied term spec remains present with its source count.

It cannot validate paraphrased facts, causality, status, or uncertainty. Those
remain behavioral eval assertions and human/model review tasks.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys


FENCED = re.compile(r"(?ms)^[ \t]*(```|~~~).*?^[ \t]*\1[ \t]*$")
INDENTED = re.compile(r"(?m)(?:^(?: {4}|\t).*(?:\n|$))+")
BLOCKQUOTE = re.compile(r"(?m)(?:^[ \t]*>.*(?:\n|$))+")
INLINE_CODE = re.compile(r"`[^`\n]+`")
GUILLEMETS = re.compile(r"«.*?»", re.DOTALL)
CURLY_QUOTES = re.compile(r"“.*?”", re.DOTALL)
LOG_LINE = re.compile(
    r"(?m)^[ \t]*(?:\d{4}-\d{2}-\d{2}T\S+\s+)?(?:TRACE|DEBUG|INFO|WARN|ERROR|FATAL)\b.*$"
)
URL = re.compile(r"https?://[^\s)>\]}]+")
NUMBER = re.compile(
    r"(?<!\w)(?:p\d{1,4}|v\d+(?:\.\d+){0,3})(?!\w)"
    # Dotted versions need two or more groups. A single group like "4.2" falls
    # through to the alternative below so that its magnitude suffix is kept.
    r"|(?<![\w.])\d+(?:\.\d+){2,3}(?![\w.])"
    r"|(?<![\w.])\d+(?:[.,]\d+)?(?:[kKmM]|\s*(?:млн|млрд|тыс))?"
    r"(?:\s*(?:%|мс|ms|s|секунд\w*|минут\w*|час\w*|КБ|МБ|ГБ|KB|MB|GB|msg/s|rps))?"
    r"(?!\w)",
    re.IGNORECASE,
)
SURFACES = ("plain", "markdown", "confluence-storage", "chat")
XHTML_TAG = re.compile(r"(?s)<[^>]+>")
CONFLUENCE_PROTECTED_TAGS = {
    "ac:structured-macro",
    "ac:task-list",
    "blockquote",
    "code",
    "pre",
    "q",
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


@dataclass(frozen=True)
class Report:
    missing_numbers: tuple[str, ...]
    added_numbers: tuple[str, ...]
    missing_urls: tuple[str, ...]
    added_urls: tuple[str, ...]
    missing_protected: tuple[str, ...]
    added_protected: tuple[str, ...]
    missing_terms: tuple[str, ...]
    source_missing_terms: tuple[str, ...]
    reduced_terms: tuple[str, ...]
    checked_terms: int
    content_removed: bool
    markup_changed: bool
    surface: str

    @property
    def ok(self) -> bool:
        return not any(
            (
                self.missing_numbers,
                self.added_numbers,
                self.missing_urls,
                self.added_urls,
                self.missing_protected,
                self.added_protected,
                self.missing_terms,
                self.source_missing_terms,
                self.reduced_terms,
                self.content_removed,
                self.markup_changed,
            )
        )

    # Coverage belongs to the report, not to one renderer. A caller reading
    # --json must be able to see that "ok" covers recognised literals only:
    # every counterexample in the research notes — causality, a flipped sign,
    # two numbers swapped between objects — passes this check.
    CHECKED = (
        "распознанные числовые токены",
        "распознанные URL",
        "распознанные защищённые фрагменты",
        "наличие редактируемого текста",
        "явно заданные термины — только при --term или --terms-file",
    )
    NOT_CHECKED = (
        "причинность: «После обновления» → «Из-за обновления» проходит",
        "отрицание и его область, в том числе внутри прямых кавычек",
        "статус работы: вид глагола не сравнивается",
        "степень уверенности",
        "привязка числа к объекту: числа сравниваются как мультимножество",
        "знак числа: минус в токен не входит",
        "единицы вне закрытого списка: «5 дней» и «5 недель» — одно число",
        "порядок защищённых фрагментов: переставленные команды проходят",
        "пересказ факта своими словами",
        "литералы, которые скрипт не распознаёт как числа, URL или фрагменты",
    )

    def coverage(self) -> dict[str, object]:
        checked = list(self.CHECKED)
        if not self.checked_terms:
            checked = [c for c in checked if not c.startswith("явно заданные")]
        return {
            "checked": checked,
            "not_checked": list(self.NOT_CHECKED),
            "meaning": (
                "Совпадение распознанных литералов. Не сохранность смысла."
            ),
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "coverage": self.coverage(),
            "missing_numbers": list(self.missing_numbers),
            "added_numbers": list(self.added_numbers),
            "missing_urls": list(self.missing_urls),
            "added_urls": list(self.added_urls),
            "missing_protected": list(self.missing_protected),
            "added_protected": list(self.added_protected),
            "missing_terms": list(self.missing_terms),
            "source_missing_terms": list(self.source_missing_terms),
            "reduced_terms": list(self.reduced_terms),
            "checked_terms": self.checked_terms,
            "content_removed": self.content_removed,
            "markup_changed": self.markup_changed,
            "surface": self.surface,
        }


class ConfluenceProtectedSpanParser(HTMLParser):
    """Locate complete protected XHTML blocks, including malformed nesting."""

    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=False)
        self.source = text
        self.line_offsets = [0]
        self.line_offsets.extend(match.end() for match in re.finditer("\n", text))
        self.stack: list[str] = []
        self.protected_count = 0
        self.start: int | None = None
        self.spans: list[str] = []

    def _index(self) -> int:
        line, column = self.getpos()
        return self.line_offsets[line - 1] + column

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        del attrs
        tag = tag.lower()
        if tag in CONFLUENCE_VOID_TAGS:
            return
        self.stack.append(tag)
        if tag not in CONFLUENCE_PROTECTED_TAGS:
            return
        if self.protected_count == 0:
            self.start = self._index()
        self.protected_count += 1

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        del attrs
        if tag.lower() not in CONFLUENCE_PROTECTED_TAGS or self.protected_count > 0:
            return
        start = self._index()
        raw = self.get_starttag_text() or ""
        self.spans.append(self.source[start : start + len(raw)])

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
        if self.protected_count > 0 or self.start is None:
            return
        end_start = self._index()
        end = self.source.find(">", end_start)
        end = len(self.source) if end < 0 else end + 1
        self.spans.append(self.source[self.start : end])
        self.start = None

    def finish(self) -> list[str]:
        self.close()
        if self.start is not None:
            self.spans.append(self.source[self.start :])
            self.start = None
        return self.spans


def confluence_protected_spans(text: str) -> list[str]:
    parser = ConfluenceProtectedSpanParser(text)
    parser.feed(text)
    return parser.finish()


def _counter_diff(before: Counter[str], after: Counter[str]) -> tuple[str, ...]:
    return tuple(sorted((before - after).elements()))


def protected_spans(
    text: str, *, surface: str = "markdown"
) -> Counter[str]:
    """Extract byte-stable spans. Duplicates remain significant."""
    if surface not in SURFACES:
        raise ValueError(f"Unknown surface: {surface}")
    spans: list[str] = []
    regexes = [GUILLEMETS, CURLY_QUOTES, LOG_LINE]
    if surface in {"markdown", "chat"}:
        regexes.extend((FENCED, INDENTED, BLOCKQUOTE, INLINE_CODE))
    for regex in regexes:
        spans.extend(match.group(0) for match in regex.finditer(text))
    if surface == "confluence-storage":
        spans.extend(confluence_protected_spans(text))
    return Counter(spans)


def _without_spans(text: str, spans: Counter[str]) -> str:
    for span, count in spans.items():
        text = text.replace(span, "", count)
    return text


def editable_text(text: str, *, surface: str) -> str:
    """Return unprotected prose used only for complete-collapse detection."""
    if surface == "confluence-storage":
        text = _without_spans(text, Counter(confluence_protected_spans(text)))
        text = XHTML_TAG.sub("", text)
        return _without_spans(text, protected_spans(text, surface="plain"))
    if surface in {"markdown", "chat"}:
        for regex in (FENCED, INDENTED, BLOCKQUOTE):
            text = regex.sub("", text)
        for regex in (INLINE_CODE, GUILLEMETS, CURLY_QUOTES, LOG_LINE):
            text = regex.sub("", text)
        return text
    for regex in (GUILLEMETS, CURLY_QUOTES, LOG_LINE):
        text = regex.sub("", text)
    return text


def _term_regex(raw: str) -> re.Pattern[str]:
    literal = raw.startswith("literal:")
    value = raw[len("literal:") :] if literal else raw
    alternatives = (value,) if literal else tuple(value.split("|"))
    patterns: list[str] = []
    for alternative in alternatives:
        alternative = alternative.strip()
        if not alternative:
            raise ValueError(f"Empty locked-term alternative: {raw!r}")
        if literal:
            pattern = re.escape(alternative)
        else:
            parts = re.split(r"\s+", alternative)
            pattern = r"\s+".join(re.escape(part) for part in parts)
            if alternative[0].isalnum() or alternative[0] == "_":
                pattern = r"(?<!\w)" + pattern
            if alternative[-1].isalnum() or alternative[-1] == "_":
                pattern += r"(?!\w)"
        patterns.append(pattern)
    return re.compile("(?:" + "|".join(patterns) + ")", 0 if literal else re.I)


def _term_count(text: str, raw: str) -> int:
    return len(_term_regex(raw).findall(text))


def compare(
    source: str,
    edited: str,
    terms: list[str] | tuple[str, ...],
    *,
    surface: str = "markdown",
) -> Report:
    """Compare deterministic invariants between source and edited text."""
    if surface not in SURFACES:
        raise ValueError(f"Unknown surface: {surface}")
    source_numbers = Counter(NUMBER.findall(source))
    edited_numbers = Counter(NUMBER.findall(edited))
    source_urls = Counter(URL.findall(source))
    edited_urls = Counter(URL.findall(edited))
    source_protected = protected_spans(source, surface=surface)
    edited_protected = protected_spans(edited, surface=surface)
    source_editable = editable_text(source, surface=surface)
    edited_editable = editable_text(edited, surface=surface)
    content_removed = bool(source_editable.strip()) and not bool(
        edited_editable.strip()
    )
    markup_changed = False
    if surface == "confluence-storage":
        markup_changed = tuple(XHTML_TAG.findall(source)) != tuple(
            XHTML_TAG.findall(edited)
        )

    unique_terms = tuple(dict.fromkeys(term.strip() for term in terms if term.strip()))
    missing_terms: list[str] = []
    source_missing_terms: list[str] = []
    reduced_terms: list[str] = []
    for term in unique_terms:
        source_count = _term_count(source, term)
        edited_count = _term_count(edited, term)
        if source_count == 0:
            source_missing_terms.append(term)
        elif edited_count == 0:
            missing_terms.append(term)
        elif edited_count < source_count:
            reduced_terms.append(f"{term}: {source_count} -> {edited_count}")
    return Report(
        missing_numbers=_counter_diff(source_numbers, edited_numbers),
        added_numbers=_counter_diff(edited_numbers, source_numbers),
        missing_urls=_counter_diff(source_urls, edited_urls),
        added_urls=_counter_diff(edited_urls, source_urls),
        missing_protected=_counter_diff(source_protected, edited_protected),
        added_protected=_counter_diff(edited_protected, source_protected),
        missing_terms=tuple(sorted(missing_terms)),
        source_missing_terms=tuple(sorted(source_missing_terms)),
        reduced_terms=tuple(sorted(reduced_terms)),
        checked_terms=len(unique_terms),
        content_removed=content_removed,
        markup_changed=markup_changed,
        surface=surface,
    )


def _read_terms(path: Path | None, direct: list[str]) -> list[str]:
    terms = list(direct)
    if path is not None:
        for line in path.read_text(encoding="utf-8").splitlines():
            term = line.strip()
            if term and not term.startswith("# "):
                terms.append(term)
    return terms


def _render(report: Report) -> str:
    if report.ok:
        cov = report.coverage()
        checked = "\n".join(f"  - {item}" for item in cov["checked"])
        not_checked = "\n".join(f"  - {item}" for item in cov["not_checked"])
        terminology = (
            f"Проверено term specs: {report.checked_terms}."
            if report.checked_terms
            else "НЕ проверялось: терминология — передайте --term или --terms-file."
        )
        return (
            f"Проверено и совпало:\n{checked}\n"
            f"{terminology}\n\n"
            f"НЕ проверялось ничем из перечисленного:\n{not_checked}\n"
            f"{cov['meaning']}"
        )
    labels = (
        ("Удалены numbers", report.missing_numbers),
        ("Добавлены numbers", report.added_numbers),
        ("Удалены URLs", report.missing_urls),
        ("Добавлены URLs", report.added_urls),
        ("Изменены/удалены protected spans", report.missing_protected),
        ("Добавлены protected spans", report.added_protected),
        ("Исчезли locked terms", report.missing_terms),
        ("Locked terms отсутствуют в source", report.source_missing_terms),
        ("Сократилась кратность locked terms", report.reduced_terms),
    )
    lines = ["Нарушены deterministic invariants:"]
    for label, values in labels:
        if values:
            lines.append(f"- {label}: {', '.join(repr(value) for value in values)}")
    if report.markup_changed:
        lines.append("- Изменена последовательность Confluence tags или attributes")
    if report.content_removed:
        lines.append("- Удалён весь редактируемый prose content")
    if report.checked_terms:
        lines.append(f"Terminology Lock: проверено term specs — {report.checked_terms}.")
    else:
        lines.append(
            "Terminology Lock не проверялся: передайте --term или --terms-file."
        )
    lines.append(
        "Causality, status, uncertainty и paraphrased facts этим checker не покрыты."
    )
    return "\n".join(lines)


def _detect_surface(source: Path, edited: Path) -> str:
    suffixes = {source.suffix.lower(), edited.suffix.lower()}
    if suffixes & {".html", ".xhtml"}:
        return "confluence-storage"
    return "markdown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check deterministic Fact-Lock and Terminology Lock invariants."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("edited", type=Path)
    parser.add_argument("--term", action="append", default=[])
    parser.add_argument("--terms-file", type=Path)
    parser.add_argument("--surface", choices=SURFACES)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        source = args.source.read_text(encoding="utf-8")
        edited = args.edited.read_text(encoding="utf-8")
        terms = _read_terms(args.terms_file, args.term)
    except (OSError, UnicodeError) as exc:
        print(f"Cannot read input: {exc}", file=sys.stderr)
        return 2

    surface = args.surface or _detect_surface(args.source, args.edited)
    try:
        report = compare(source, edited, terms, surface=surface)
    except ValueError as exc:
        print(f"Invalid term spec: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(_render(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
