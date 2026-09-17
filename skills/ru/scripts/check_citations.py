#!/usr/bin/env python3
"""Resolve every [ПАС § N п. M] style citation against the local source library.

Separate from validate_skill.py on purpose: that one is self-contained and runs
anywhere, this one needs `~/git/personal/language/rules/` on disk and says
nothing useful without it.

What it catches that the validator cannot: a citation whose paragraph exists but
belongs to the other numbering series, a point number that is not in the
paragraph, and a примечание that is not there either. All three kinds happened
while this skill was being written.

What it does not catch: a paragraph that exists, carries the point, and says
something else. Only reading does that.

    python3 scripts/check_citations.py [--library PATH] [--quiet]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_LIBRARY = Path.home() / "git/personal/language/rules"

# Which collection each short name lives in, and whether it is the punctuation
# series. ПАС and Розенталь each number orthography and punctuation
# independently, and the numbers collide — see references/sources.md.
SOURCES = {
    "ПАС": ("lopatin", True),
    "ПАС-орф.": ("lopatin", False),
    "Роз": ("rozental", True),
    "Роз-ЛП": ("literary-editing", True),
}

# The whole citation, so that ranges and lists inside it can be picked apart.
CITATION = re.compile(r"\[(ПАС-орф\.|ПАС|Роз-ЛП|Роз)\s(§+[^\]]*)\]")
# `§ 15`, `§§ 16–18`, `§§ 61, 63, 65` — every paragraph the citation addresses.
RANGE = re.compile(r"(\d+)\s*[–—-]\s*(\d+)")
NUMBER = re.compile(r"\d+")
# A citation can carry several addresses: `§ 140 п. 3, примечание; § 72`.
ADDRESS_SPLIT = re.compile(r"[;]")
# Where the paragraph part of one address ends.
TAIL_MARKER = re.compile(r"пп?\.|подп\.|прим")
# `п. 5`, `пп. 2, 3 (в, г, д), 5`, `пп. 1–4`. `(?<!под)` keeps `подп.` out: it
# ends in `п.` and would match otherwise.
POINTS = re.compile(r"(?<!под)\bпп?\.\s*([\d.,\s–—()а-яё-]+)")
# `подп. 4` is numbered and checkable; `подп. «е»` is a Cyrillic letter and is
# not — sources write those inconsistently.
SUBPOINTS = re.compile(r"подп\.\s*([\d,\s–—-]+)")
CYRILLIC_SUBPOINT = re.compile(r"подп\.\s*[«\"\']?[а-яё]")
NOTES = re.compile(r"прим(?:ечание)?\.?\s*(\d+)?")


def numbers_in(text: str) -> list[int]:
    """Every number the fragment names, ranges expanded."""
    found: list[int] = []
    rest = text
    for lo, hi in RANGE.findall(text):
        found.extend(range(int(lo), int(hi) + 1))
        rest = rest.replace(lo, " ", 1).replace(hi, " ", 1)
    found.extend(int(n) for n in NUMBER.findall(rest))
    return sorted(set(found))


def series_of(text: str) -> bool | None:
    """True for punctuation, False for orthography, None when not declared."""
    match = re.search(r'^section_path:\s*\[(.*?)\]', text, re.MULTILINE)
    if not match:
        return None
    head = match.group(1).upper()
    if "ПУНКТУАЦИЯ" in head:
        return True
    if "ОРФОГРАФИЯ" in head:
        return False
    return None


def paragraph_bodies(library: Path, collection: str, number: int, punctuation: bool):
    """Every (filename, text) for that paragraph in the wanted numbering series."""
    found = []
    directory = library / collection
    if not directory.is_dir():
        return found
    head = re.compile(rf"^#{{1,4}} .*?\**§ ?{number}\**[\.\s]", re.MULTILINE)
    nxt = re.compile(rf"^#{{1,4}} .*?\**§ ?{number + 1}\**[\.\s]", re.MULTILINE)
    for path in sorted(directory.glob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        start = head.search(text)
        if not start:
            continue
        # The dump declares the series in its front matter; for Rosenthal the
        # file name carries it too, and literary-editing has one series only.
        declared = series_of(text)
        if declared is None and collection == "rozental":
            declared = "punctuatio" in path.name
        if declared is None and collection == "literary-editing":
            declared = True
        if declared is None or declared != punctuation:
            continue
        rest = text[start.end():]
        end = nxt.search(rest)
        found.append((path.name, rest[: end.start()] if end else rest))
    return found


def has_point(body: str, point: int) -> bool:
    # Sources write the number as `1.`, `**1.**` or `1)`, and the markdown dump
    # escapes the period in some collections.
    patterns = (
        rf"^\s*{point}\\?\.",
        rf"^\s*\*\*{point}\\?\.\*\*",
        rf"^\s*{point}\)",
    )
    return any(re.search(p, body, re.MULTILINE) for p in patterns)


def has_note(body: str, number: int | None) -> bool:
    if number is None:
        return bool(re.search(r"Примечание", body, re.IGNORECASE))
    return bool(re.search(rf"Примечание\s*{number}\b", body, re.IGNORECASE))


def plural(count: int, one: str, few: str, many: str) -> str:
    """`1 пункт`, `2 пункта`, `5 пунктов` — the tool speaks Russian too."""
    tens = count % 100
    if 11 <= tens <= 14:
        return many
    last = count % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def point_body(body: str, point: int) -> str | None:
    """The text of one numbered point, up to the next one."""
    head = re.search(rf"^\s*(?:\*\*)?{point}\\?[.)]", body, re.MULTILINE)
    if not head:
        return None
    rest = body[head.end():]
    nxt = re.search(rf"^\s*(?:\*\*)?{point + 1}\\?[.)]", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


def has_subpoint(body: str, item: int) -> bool:
    """A numbered item under the first Примечание of the fragment."""
    start = re.search(r"Примечание", body, re.IGNORECASE)
    if not start:
        return False
    return bool(re.search(rf"^\s*{item}\)", body[start.end():], re.MULTILINE))


def addresses_in(tail: str):
    """Split one citation into the addresses it carries.

    `§ 140 п. 3, примечание; § 72` is two: the paragraph with its point and
    note, and a bare paragraph. Each yields (paragraphs, points, rest), where
    rest is what follows the paragraph part and carries the note.
    """
    for part in ADDRESS_SPLIT.split(tail):
        if not part.strip():
            continue
        marker = TAIL_MARKER.search(part)
        head, rest = (part[: marker.start()], part[marker.start():]) if marker else (part, "")
        paragraphs = numbers_in(head)
        points_match = POINTS.search(rest)
        if points_match:
            raw = points_match.group(1)
            # `п. 1.3` is one hierarchical number, not points 1 and 3.
            raw = re.sub(r"(\d+)\.\d+(?:\.\d+)*", r"\1", raw)
            points = numbers_in(raw)
        else:
            points = []
        yield paragraphs, points, rest


def unconsumed(rest: str) -> str:
    """Digits in the address that no rule above claimed."""
    left = rest
    for pattern in (POINTS, SUBPOINTS, NOTES):
        match = pattern.search(left)
        if match:
            left = left[: match.start()] + left[match.end():]
    return " ".join(NUMBER.findall(left))


def check(root: Path, library: Path):
    """Yield one message per citation that does not resolve."""
    for path in sorted((root / "references").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for match in CITATION.finditer(text):
            name, tail = match.group(1), match.group(2)
            collection, punctuation = SOURCES[name]
            series = "пунктуационный" if punctuation else "орфографический"
            for paragraphs, points, rest in addresses_in(tail):
                if not paragraphs:
                    continue
                resolved = []
                for number in paragraphs:
                    bodies = paragraph_bodies(library, collection, number, punctuation)
                    if not bodies:
                        yield (f"{path.name}: [{name} § {number}] — {series} параграф "
                               f"не найден в {collection}/")
                    else:
                        resolved.append((number, bodies))
                left = unconsumed(rest)
                if left:
                    shown = ", ".join(str(n) for n in paragraphs)
                    yield (f"{path.name}: [{name} § {shown}] — форма не разобрана, "
                           f"числа без адреса: {left}")
                if len(resolved) != 1:
                    # A point or note belongs to one paragraph; with several
                    # addressed we cannot tell which.
                    continue
                number, bodies = resolved[0]
                where = f"{path.name}: [{name} § {number}]"
                files = ", ".join(n for n, _ in bodies)
                missing = [p for p in points
                           if not any(has_point(b, p) for _, b in bodies)]
                # One wrong range can name a hundred points; three name the
                # defect just as well.
                for point in missing[:3]:
                    yield f"{where} п. {point} — пункта нет в {files}"
                if len(missing) > 3:
                    rest_count = len(missing) - 3
                    word = plural(rest_count, "пункт", "пункта", "пунктов")
                    yield (f"{where} — и ещё {rest_count} {word} "
                           f"отсутствует в {files}")
                # A note belongs to the point when one is named, and to the
                # whole paragraph otherwise.
                if len(points) == 1 and not missing:
                    scoped = [(n, point_body(b, points[0]) or "") for n, b in bodies]
                    scope = f"{where} п. {points[0]}"
                else:
                    scoped, scope = bodies, where
                if CYRILLIC_SUBPOINT.search(rest):
                    # A Cyrillic sub-item; the note below, if any, still applies.
                    pass
                sub = SUBPOINTS.search(rest)
                if sub:
                    # `примечание, подп. N` — under the note; `п. 5, подп. 4` —
                    # under the point. Which one is decided by the order.
                    note_first = NOTES.search(rest)
                    under_note = bool(note_first and note_first.start() < sub.start())
                    if under_note:
                        if not any(has_note(b, None) for _, b in scoped):
                            yield f"{scope} — примечания нет в {files}"
                            continue
                        fragments = [(n, b[re.search(r"Примечание", b, re.I).end():])
                                     for n, b in scoped
                                     if re.search(r"Примечание", b, re.I)]
                        label = "примечание, подп."
                    else:
                        fragments, label = scoped, "подп."
                    for item in numbers_in(sub.group(1)):
                        if not any(re.search(rf"^\s*{item}\)", b, re.MULTILINE)
                                   for _, b in fragments):
                            yield f"{scope} {label} {item} — подпункта нет в {files}"
                    continue
                note = NOTES.search(rest)
                if note and not any(
                    has_note(b, note.group(1) and int(note.group(1)))
                    for _, b in scoped
                ):
                    shown = f" {note.group(1)}" if note.group(1) else ""
                    yield f"{scope} прим.{shown} — примечания нет в {files}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=DEFAULT_LIBRARY)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    if not args.library.is_dir():
        print(
            f"библиотека не найдена: {args.library}\n"
            "проверка пропущена — это не то же самое, что «ссылки верны»",
            file=sys.stderr,
        )
        return 2

    problems = list(check(root, args.library))
    for problem in problems:
        print(problem)
    if problems:
        return 1
    if not args.quiet:
        print("Все ссылки на параграфы разрешились в нужном ряду нумерации.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
