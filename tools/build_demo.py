#!/usr/bin/env python3
"""Bundle the package for the browser demo, as one generated JSON file.

    python3 tools/build_demo.py           # write docs/demo-bundle.json
    python3 tools/build_demo.py --check   # fail if it is out of date

Why a generated bundle rather than a hand-written copy
------------------------------------------------------
GitHub Pages serves only ``docs/``, so the demo cannot reach ``../`` to read
the package. The options were to copy the source into ``docs/`` by hand, to
fetch it from ``raw.githubusercontent.com`` at a pinned ref, or to generate a
bundle and gate it.

Copying by hand creates a second source of truth, and this project has already
established what happens to prose that drifts from code -- a help string
described an implementation replaced twenty minutes earlier. Fetching from a
pinned ref makes the demo silently show old behaviour. Generating and gating
means the demo cannot disagree with the tests: ``--check`` fails the build when
it would.

The bundle is a plain JSON map of module path to source. It is legible in a
diff, which a wheel would not be, and it needs no build toolchain.

Why this works at all
---------------------
The package is **standard library only** -- verified below, not assumed -- so
Pyodide runs it with no wheels and no network beyond its own runtime. That was
a design decision taken for auditability (a reviewer at a bank can read the
dependencies), and a browser demo running the real linter is a payoff from it
that was never the reason.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "docs" / "demo-bundle.json"

#: Modules the demo needs, in no particular order -- Python resolves them.
#: Listed explicitly rather than globbed so that adding a module to the package
#: is a decision about whether the demo should carry it, not an accident.
MODULES: tuple[str, ...] = (
    "finagent_safeguard/__init__.py",
    "finagent_safeguard/cli/__init__.py",
    "finagent_safeguard/cli/linter.py",
    "finagent_safeguard/core/__init__.py",
    "finagent_safeguard/core/decorators.py",
    "finagent_safeguard/regulation/__init__.py",
    "finagent_safeguard/regulation/candidates.py",
    "finagent_safeguard/regulation/citation.py",
    "finagent_safeguard/regulation/registry.py",
    "finagent_safeguard/taxonomy/__init__.py",
    "finagent_safeguard/taxonomy/policies.py",
)

#: Every module Pyodide ships in its standard library that these files may
#: import. A module outside this set means the demo would need a wheel, which
#: is the moment to stop and reconsider rather than to add one.
STDLIB_OK = frozenset(
    {
        "__future__", "argparse", "ast", "collections", "collections.abc",
        "dataclasses", "datetime", "decimal", "difflib", "enum", "fnmatch",
        "hashlib", "io", "json", "math", "os", "pathlib", "re", "stat",
        "subprocess", "sys", "typing",
    }
)


def _third_party_imports() -> list[str]:
    """Imports that are neither stdlib nor this package.

    The check that keeps the "no wheels" claim true. A single added dependency
    would break the demo at load time with a message nobody would connect to
    the commit that caused it.
    """
    found: set[str] = set()
    for relative in MODULES:
        tree = ast.parse((ROOT / relative).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                found.add(node.module.split(".")[0])
    return sorted(
        name
        for name in found
        if name not in STDLIB_OK and name != "finagent_safeguard"
    )


def build() -> dict[str, str]:
    outside = _third_party_imports()
    if outside:
        raise SystemExit(
            f"error: the demo bundle would need {outside}, which Pyodide has "
            "no wheel for here.\n"
            "  The package is stdlib-only by design; adding a dependency is a "
            "decision to take deliberately,\n"
            "  not one to discover when the demo stops loading."
        )
    return {path: (ROOT / path).read_text() for path in MODULES}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the committed bundle is not what this would write",
    )
    args = parser.parse_args(argv)

    bundle = build()
    rendered = json.dumps(bundle, indent=1, sort_keys=True) + "\n"

    if args.check:
        if not BUNDLE.exists():
            print(f"error: {BUNDLE.relative_to(ROOT)} does not exist; "
                  "run tools/build_demo.py", file=sys.stderr)
            return 1
        if BUNDLE.read_text() != rendered:
            print(
                f"error: {BUNDLE.relative_to(ROOT)} is stale. The browser demo "
                "would show behaviour the tests do not cover.\n"
                "  Run: python3 tools/build_demo.py",
                file=sys.stderr,
            )
            return 1
        print(f"  bundle current: {len(bundle)} modules, "
              f"{sum(len(v) for v in bundle.values()) // 1024} KiB")
        return 0

    BUNDLE.write_text(rendered)
    print(f"  wrote {BUNDLE.relative_to(ROOT)}: {len(bundle)} modules, "
          f"{len(rendered) // 1024} KiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
