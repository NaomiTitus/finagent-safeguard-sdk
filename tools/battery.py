#!/usr/bin/env python3
"""Run the review battery. One command, so it is not a thing to remember.

    python3 tools/battery.py              # against HEAD
    python3 tools/battery.py --base main  # against another ref

Layers 1a and 1b already run in CI, because a test file and a mutation baseline
are both just things CI executes. Layer 1c is the one that has no natural home:
"does every guard we added have a mutation row?" is a question about the diff,
not about the code, so nothing was asking it. It caught three unprotected
guards in a single day -- each time because somebody thought to ask.

F-014 in docs/review-log.md is the reason this exists rather than a note in a
checklist: a written lesson failed twice as a control before it was replaced
with a mechanism. This is the mechanism.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Things that look like definitions but need no mutation row of their own.
_EXEMPT = re.compile(r"^(?:_{2}.*_{2}|test_|Test)")


def _run(label: str, command: list[str], env: dict[str, str] | None = None) -> bool:
    import os

    merged = {**os.environ, **(env or {})}
    done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, env=merged)
    ok = done.returncode == 0
    tail = (done.stdout or done.stderr).strip().splitlines()
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    for line in tail[-3:]:
        print(f"        {line}")
    return ok


def _added_definitions(base: str) -> dict[str, set[str]]:
    """Names introduced by the diff, per file, that a mutation row could target.

    Reads the diff rather than the tree, because the question is "what did this
    change add that nothing proves is working", and the tree cannot answer it.
    """
    diff = subprocess.run(
        ["git", "diff", "-U0", base, "--", "finagent_safeguard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).stdout

    added: dict[str, set[str]] = {}
    current = ""
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        body = line[1:].strip()
        found = re.match(r"(?:async\s+)?def\s+(\w+)", body) or re.match(
            r"([A-Z][A-Z0-9_]{2,})(?::|\s*=)", body
        )
        if found and not _EXEMPT.match(found.group(1)):
            added.setdefault(current, set()).add(found.group(1))
    return added


def _mutation_anchors() -> list[str]:
    """The `find` string of every mutation row, read by executing the module.

    Imported rather than regex-scraped: the rows are a Python literal, and a
    regex over them would misread the escaped newlines several rows contain.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_mut", ROOT / "tools/run_mutations.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return [row[1] for row in module.MUTATIONS.values()]


def _bodies(path: Path) -> dict[str, str]:
    """Source of every function and class in a file, keyed by bare name."""
    import ast

    if not path.exists():
        return {}
    source = path.read_text()
    out: dict[str, str] = {}
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            out[node.name] = ast.get_source_segment(source, node) or ""
    # Module-level constants have no body; treat the whole module as theirs so
    # a row anchored on the assignment line counts.
    out.setdefault("", source)
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (
                [node.target] if isinstance(node, ast.AnnAssign) else node.targets
            )
            for target in targets:
                if isinstance(target, ast.Name):
                    out[target.id] = ast.get_source_segment(source, node) or ""
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="HEAD", help="ref to diff against for layer 1c")
    args = parser.parse_args(argv)

    print("=" * 70)
    results = [
        _run("tests", [sys.executable, "-m", "pytest", "tests/", "-q", "--no-header"]),
        _run("mypy --strict (package)", [sys.executable, "-m", "mypy", "--strict", "finagent_safeguard/"]),
        _run(
            "mypy --strict (tools)",
            [sys.executable, "-m", "mypy", "--strict", "tools/kappa.py",
             "tools/entailment.py", "tools/check_entailment.py", "tools/battery.py"],
        ),
        _run("layer 1a: mutation baseline", [sys.executable, "tools/run_mutations.py", "--check"],
             env={"PYTHONDONTWRITEBYTECODE": "1"}),
        _run("layer 1b: vacuity", [sys.executable, "-m", "pytest", "tests/test_meta_suite.py", "-q", "--no-header"]),
    ]

    print("-" * 70)
    print(f"layer 1c: name assertion (diff against {args.base})")
    anchors = _mutation_anchors()
    unprotected: list[str] = []
    added = _added_definitions(args.base)
    if not added:
        print("        no new definitions in the diff")
    for path, names in sorted(added.items()):
        bodies = _bodies(ROOT / path)
        for name in sorted(names):
            # A row targets a definition when its anchor text sits inside that
            # definition's source. Matching the *name* instead was the obvious
            # shortcut and it was wrong in both directions: rows anchor on code
            # lines, so L30 inside _vocabularies looked like no coverage, while
            # a row merely mentioning a name in its note looked like coverage.
            body = bodies.get(name, "")
            covered = any(a and a in body for a in anchors)
            print(f"        {'ok  ' if covered else 'GAP '} {path}: {name}")
            if not covered:
                unprotected.append(f"{path}:{name}")

    print("=" * 70)
    if unprotected:
        print(f"\n{len(unprotected)} new definition(s) with no mutation row:")
        for item in unprotected:
            print(f"  - {item}")
        print(
            "\nA guard nothing can prove is working is not protected. Add a row to\n"
            "tools/run_mutations.py naming the test that must fail when it breaks,\n"
            "or say in the commit message why this one needs none."
        )

    failed = results.count(False)
    if failed:
        print(f"\n{failed} check(s) failed.")
        return 1
    if unprotected:
        return 1
    print("\nAll checks passed, every new definition has a mutation row.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
