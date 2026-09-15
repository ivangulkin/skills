#!/usr/bin/env bash
# Every test runs against tests/fake_codex.py: no API key, no quota, and no
# thread left behind in anyone's Codex.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

echo "== syntax =="
python3 -m compileall -q scripts/duo tests

echo "== no shadowed tests =="
# A class or method defined twice replaces the first: the earlier copy is
# never collected. Names come from the syntax tree, because `class X(` and
# `class X (` are the same name to Python and different strings to grep.
python3 - <<'PY'
import ast, collections, pathlib, sys

tree = ast.parse(pathlib.Path("tests/test_duo.py").read_text())
problems = []
top = collections.Counter(
    node.name for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef))
)
problems += [f"{name} (module)" for name, count in top.items() if count > 1]
for node in tree.body:
    if not isinstance(node, ast.ClassDef):
        continue
    inner = collections.Counter(
        child.name
        for child in node.body
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    problems += [f"{node.name}.{name}" for name, count in inner.items() if count > 1]
if problems:
    sys.exit("these definitions shadow an earlier one and it never runs: " + ", ".join(problems))
PY

echo "== unit and end-to-end =="
# ResourceWarning is an error here: a leaked pipe per round is exactly the kind
# of slow failure this design exists to avoid.
python3 -W error::ResourceWarning -m unittest discover -s tests "$@"
