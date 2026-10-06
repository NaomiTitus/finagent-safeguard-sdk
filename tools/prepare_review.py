#!/usr/bin/env python3
"""Assemble a cold-review packet from a git ref. The author does not choose.

    python3 tools/prepare_review.py main..ci/enforce-review-layers

Two levers this removes from whoever requests the review:

1. **Material selection.** The packet is derived from the diff by fixed rules,
   not picked by hand. A prior review could not verify a defence because the
   author had forgotten to include ``conftest.py``; another flagged a claim as
   unknowable for the same reason. Rules do not forget.
2. **The criterion.** It is read from ``docs/review-criteria.toml``, keyed by
   branch, and the run fails if the branch has no entry. An author who writes
   the criterion at review time decides what passing means.

The packet carries a MANIFEST listing every file included with its SHA-256, and
every exclusion rule applied, so a third party can check what the reviewer saw
without taking anyone's word for it.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRITERIA = ROOT / "docs" / "review-criteria.toml"

#: Diff these paths. Everything else (docs, drafts, the log) is excluded on
#: purpose: it carries the author's framing, which is what the cold review exists
#: to do without.
CODE_PATHS = ("finagent_safeguard/", "tests/", "tools/", ".github/")

EXCLUSIONS = (
    "docs/** - carries the author's reasoning, plan and prior reviews",
    "*.md at repo root - same",
    "git history and commit messages - same",
    "corpus files not referenced by the diff - not under review",
)


def sh(*args: str) -> str:
    out = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    return out.stdout


def criterion_for(branch: str) -> str:
    with CRITERIA.open("rb") as fh:
        table = tomllib.load(fh)["criteria"]
    if branch not in table:
        raise SystemExit(
            f"No committed criterion for {branch!r} in {CRITERIA.relative_to(ROOT)}.\n"
            "Add one before requesting a review. A criterion written at review "
            "time is a description of what was built, not a test of it."
        )
    return " ".join(table[branch].split())


def referenced_corpus(diff: str) -> list[Path]:
    """Every pinned provision the diff mentions, by CELEX or subdivision id."""
    celexes = set(re.findall(r"\b[03]\d{4}[LRD]\d{4}(?:-\d{8})?\b", diff))
    subdivisions = set(re.findall(r"\b(?:art|anx)_\d+[a-z]?\b", diff))
    found: list[Path] = []
    for path in sorted((ROOT / "corpus").glob("*/*.json")):
        if path.parent.name in celexes or path.stem in subdivisions:
            found.append(path)
    return found


def main(argv: list[str]) -> int:
    if len(argv) != 1 or ".." not in argv[0]:
        raise SystemExit("usage: prepare_review.py <base>..<head>")
    rng = argv[0]
    base, head = rng.split("..", 1)
    out = ROOT / ".review-packets" / head.replace("/", "-")
    shutil.rmtree(out, ignore_errors=True)
    (out / "corpus").mkdir(parents=True)

    diff = sh("git", "diff", rng, "--", *CODE_PATHS)
    if not diff.strip():
        raise SystemExit(f"empty diff for {rng}")
    (out / "diff.patch").write_text(diff)
    (out / "criterion.txt").write_text(criterion_for(head) + "\n")

    results = "\n".join(
        (
            sh(sys.executable, "-m", "pytest", "tests/", "-q", "--no-header").strip(),
            sh(sys.executable, "-m", "mypy", "--strict", "finagent_safeguard/").strip(),
            sh(sys.executable, "tools/run_mutations.py", "--check").strip(),
        )
    )
    (out / "results.txt").write_text(results + "\n")

    shutil.copy(ROOT / "tests" / "conftest.py", out / "conftest.py")
    corpus = referenced_corpus(diff)
    for path in corpus:
        shutil.copy(path, out / "corpus" / f"{path.parent.name}__{path.name}")

    lines = [f"review packet for {rng}", "", "INCLUDED (sha256):"]
    for path in sorted(out.rglob("*")):
        if path.is_file() and path.name != "MANIFEST.txt":
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
            lines.append(f"  {digest}  {path.relative_to(out)}")
    lines += ["", "EXCLUDED (by rule, not by choice):"]
    lines += [f"  - {rule}" for rule in EXCLUSIONS]
    lines += [
        "",
        f"corpus files auto-selected from the diff: {len(corpus)}",
        "criterion source: docs/review-criteria.toml (committed)",
    ]
    (out / "MANIFEST.txt").write_text("\n".join(lines) + "\n")

    print("\n".join(lines))
    print(f"\npacket: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
