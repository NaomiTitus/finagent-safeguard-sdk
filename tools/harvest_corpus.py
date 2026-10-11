#!/usr/bin/env python3
"""Harvest a labelling corpus from permissively-licensed Python, by pointer.

    python3 tools/harvest_corpus.py --clone      # fetch the sources
    python3 tools/harvest_corpus.py              # sample and write pointers
    python3 tools/harvest_corpus.py --worksheet  # regenerate the local worksheet

What gets committed is **pointers only**: repository, commit, path, line range,
and a SHA-256 of the function's source. No third-party code enters this
repository, so no licence obligation is triggered and the public history stays
clean. The worksheet a human labels from is regenerated locally from those
pointers and is gitignored.

Why the sample is drawn this way
--------------------------------
The labels this corpus carries are the *evaluation* set, and the one thing that
must not happen is that they end up measuring the linter against itself.

So the sample is **not** stratified by the linter's verdict. It is stratified by
repository -- payment libraries against the standard library -- which is a fact
about where the code came from and not about what the rule thinks of it. Within
each repository the draw is uniform at a fixed seed. Kang, Aw & Lo (arXiv
2202.05982) found the actionable-warning literature inflated by exactly this
mistake: labels derived from the tool being measured, with the oracle
"produc[ing] labels that do not agree with human oracles".

The standard library is in the sample as the negative class. It contains no
financial code, which is why the linter's 51 flags across 155 of its top-level
modules were all wrong, and that makes it the honest source of negatives.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import random
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = Path(
    "/private/tmp/claude-501/-Users-naomi-Documents-projects-finagent-safeguard-sdk"
    "/821d6a65-f513-48f1-b0c4-309ae435eb16/scratchpad/corpus-src"
)
OUT = ROOT / "benchmark"
POINTERS = OUT / "pointers.jsonl"
WORKSHEET = OUT / "worksheet.md"

#: Fixed so the draw is reproducible by anyone re-running this.
SEED = 20261009

#: A different seed for the training draw, so the two samples are independent
#: rather than the second being a prefix of the first.
TRAIN_SEED = 20261011

#: Training-set draw, sized for the feature budget rather than for balance.
#:
#: At roughly 22% positive, a thousand items yields about 220 positives, and at
#: the usual ten events per feature that lifts the budget from four features to
#: twenty -- the difference between a model that can learn something and one
#: that can only confirm the word lists.
#:
#: python-sepaxml and pyiso20022 are taken whole. They are the densest sources
#: of positives (80% and 12% in the evaluation set) and between them hold under
#: two hundred functions, so drawing proportionally would waste them. Everything
#: else is sampled, and the standard library supplies negatives.
TRAIN_DRAW: dict[str, int] = {
    "python-sepaxml": 200,
    "pyiso20022": 200,
    "schwifty": 150,
    "stripe-python": 300,
    "django-oscar": 250,
    "cpython-stdlib": 150,
}

#: Repository, clone URL, licence as verified by reading the file, and how many
#: functions to draw. Counts are weighted towards the payment libraries because
#: the standard library would otherwise swamp the sample -- it is the negative
#: class, not the subject.
@dataclass(frozen=True, slots=True)
class Source:
    name: str
    url: str
    licence: str
    draw: int


SOURCE_LIST: tuple[Source, ...] = (
    Source("stripe-python", "https://github.com/stripe/stripe-python.git", "MIT", 30),
    Source("python-sepaxml", "https://github.com/raphaelm/python-sepaxml.git", "MIT", 20),
    Source("pyiso20022", "https://github.com/phoughton/pyiso20022.git", "MIT", 25),
    Source("schwifty", "https://github.com/mdomke/schwifty.git", "MIT", 20),
    Source("django-oscar", "https://github.com/django-oscar/django-oscar.git", "BSD-3", 30),
    Source("cpython-stdlib", "", "PSF", 35),
)


@dataclass(frozen=True, slots=True)
class Pointer:
    """Where a function lives, and nothing of its content.

    ``sha256`` is of the function's exact source text, so a worksheet
    regenerated later can prove it is looking at the same code the label was
    assigned to. Without it a label silently becomes a label of whatever is at
    those line numbers now.
    """

    item_id: str
    repo: str
    commit: str
    path: str
    first_line: int
    last_line: int
    function: str
    sha256: str


def _clone() -> int:
    SOURCES.mkdir(parents=True, exist_ok=True)
    for source in SOURCE_LIST:
        if not source.url:
            continue
        target = SOURCES / source.name
        if target.exists():
            print(f"  have {source.name}")
            continue
        print(f"  cloning {source.name} ...")
        done = subprocess.run(
            ["git", "clone", "-q", "--depth", "1", source.url, str(target)],
            capture_output=True,
            text=True,
        )
        if done.returncode != 0:
            print(f"    failed: {done.stderr.strip()[:120]}", file=sys.stderr)
            return 1
    return 0


def _commit_of(repo: Path) -> str:
    if not repo.exists():
        return "n/a"
    done = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
    )
    return done.stdout.strip() or "n/a"


def _files(source: Source) -> list[Path]:
    if source.name == "cpython-stdlib":
        import sysconfig

        stdlib = Path(sysconfig.get_paths()["stdlib"])
        return sorted(p for p in stdlib.glob("*.py"))
    root = SOURCES / source.name
    # Tests and examples are kept. pyiso20022's payment builders live in them,
    # and excluding them is how a first attempt at measuring that library
    # missed 27 of its 66 functions and reported the wrong figure.
    return sorted(p for p in root.rglob("*.py") if "/.git/" not in str(p))


def _functions(path: Path) -> list[tuple[str, int, int, str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    out: list[tuple[str, int, int, str]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                segment = ast.get_source_segment(text, child) or ""
                if segment:
                    out.append(
                        (
                            f"{prefix}{child.name}",
                            child.lineno,
                            child.end_lineno or child.lineno,
                            segment,
                        )
                    )
                walk(child, f"{prefix}{child.name}.")

    walk(tree, "")
    return out


def _sample(
    *, train: bool = False, exclude: set[tuple[str, str, int]] | None = None
) -> list[Pointer]:
    """Draw the sample. ``train`` switches to the larger, independent draw.

    ``exclude`` holds (repo, path, first_line) triples already drawn, so the
    training set cannot overlap the evaluation set. Overlap would mean testing
    on items the model was trained on, which is the one mistake that would make
    every number meaningless.
    """
    rng = random.Random(TRAIN_SEED if train else SEED)
    excluded = exclude or set()
    pointers: list[Pointer] = []

    for source in SOURCE_LIST:
        repo = SOURCES / source.name
        commit = _commit_of(repo)
        found: list[tuple[Path, str, int, int, str]] = []
        for path in _files(source):
            for name, first, last, segment in _functions(path):
                # Dunder-only stubs and one-line pass-throughs carry no
                # judgement to make, and a corpus padded with them reports a
                # high score for a task nobody asked about.
                if last - first < 1:
                    continue
                found.append((path, name, first, last, segment))

        found = [
            f for f in found
            if (source.name, _relative(f[0], SOURCES / source.name, source), f[2])
            not in excluded
        ]
        rng.shuffle(found)
        draw = TRAIN_DRAW.get(source.name, 0) if train else source.draw
        prefix = "train-" if train else ""
        for index, (path, name, first, last, segment) in enumerate(found[:draw]):
            relative = _relative(path, repo, source)
            pointers.append(
                Pointer(
                    item_id=f"{prefix}{source.name}:{index:04d}",
                    repo=source.name,
                    commit=commit[:12],
                    path=relative,
                    first_line=first,
                    last_line=last,
                    function=name,
                    sha256=hashlib.sha256(segment.encode()).hexdigest()[:16],
                )
            )
    return pointers


def _relative(path: Path, repo: Path, source: Source) -> str:
    return str(path.relative_to(repo)) if source.url else path.name


def _worksheet(pointers: list[Pointer]) -> str:
    """The local, gitignored file a human labels from.

    Holds the function source so the judgement can actually be made, and is
    regenerated from the pointers rather than committed, so no third-party code
    enters this repository.
    """
    lines = [
        "# Labelling worksheet",
        "",
        "Regenerate with `python3 tools/harvest_corpus.py --worksheet`. Gitignored:",
        "it holds third-party source, which this repository does not vendor.",
        "",
        "For each item write **yes** or **no** in `benchmark/labels.jsonl`:",
        "",
        '    {"item_id": "stripe-python:000", "regulated": true}',
        "",
        "The question is one thing only, and it is deliberately not the six-way",
        "category question -- trained annotators reach a Krippendorff's alpha of",
        "0.251 on that:",
        "",
        "> **Does this function handle money movement or personal data?**",
        "",
        "Judge the function as written. Not the module, not what it could be used",
        "for. If you cannot tell from the function, that is a `no` -- and worth",
        "noting, because it is the same limit the linter has.",
        "",
        "Label 20 of these a second time, a day apart, without looking at your",
        "first answers. That gives the agreement ceiling, and without it a",
        "classifier scoring 0.85 cannot be told from noise.",
        "",
        "---",
        "",
    ]
    for pointer in pointers:
        repo = SOURCES / pointer.repo
        if pointer.repo == "cpython-stdlib":
            import sysconfig

            path = Path(sysconfig.get_paths()["stdlib"]) / pointer.path
        else:
            path = repo / pointer.path
        try:
            text = path.read_text(encoding="utf-8")
            segment = "\n".join(
                text.splitlines()[pointer.first_line - 1 : pointer.last_line]
            )
        except OSError:
            segment = "(source unavailable -- run --clone)"
        lines += [
            f"## `{pointer.item_id}`  {pointer.function}",
            f"`{pointer.repo}@{pointer.commit}` · `{pointer.path}`"
            f" lines {pointer.first_line}-{pointer.last_line}",
            "",
            "```python",
            segment[:1800],
            "```",
            "",
        ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clone", action="store_true", help="fetch the sources")
    parser.add_argument(
        "--worksheet", action="store_true", help="regenerate the worksheet only"
    )
    parser.add_argument(
        "--train",
        action="store_true",
        help="draw the larger training sample, excluding the evaluation items",
    )
    args = parser.parse_args(argv)

    if args.clone:
        return _clone()

    missing = [
        s.name for s in SOURCE_LIST if s.url and not (SOURCES / s.name).exists()
    ]
    if missing:
        print(f"missing sources: {missing}; run --clone first", file=sys.stderr)
        return 2

    OUT.mkdir(exist_ok=True)

    if args.train:
        if not POINTERS.exists():
            print("draw the evaluation set first", file=sys.stderr)
            return 2
        held = {
            (str(r["repo"]), str(r["path"]), int(r["first_line"]))
            for r in (
                json.loads(line)
                for line in POINTERS.read_text().splitlines()
                if line.strip()
            )
        }
        pointers = _sample(train=True, exclude=held)
        target = ROOT / "benchmark/train-pointers.jsonl"
        target.write_text(
            "".join(json.dumps(asdict(p)) + "\n" for p in pointers)
        )
        print(f"  wrote {len(pointers)} training pointers to {target.relative_to(ROOT)}")
        from collections import Counter

        for repo, count in Counter(p.repo for p in pointers).most_common():
            print(f"    {repo:<18} {count}")
        drawn = {(p.repo, p.path, p.first_line) for p in pointers}
        overlap = drawn & held
        print(f"\n  overlap with the evaluation set: {len(overlap)}")
        return 1 if overlap else 0

    if args.worksheet and POINTERS.exists():
        pointers = [
            Pointer(**json.loads(line))
            for line in POINTERS.read_text().splitlines()
            if line.strip()
        ]
    else:
        pointers = _sample()
        POINTERS.write_text(
            "".join(json.dumps(asdict(p)) + "\n" for p in pointers)
        )
        print(f"  wrote {len(pointers)} pointers to {POINTERS.relative_to(ROOT)}")

    WORKSHEET.write_text(_worksheet(pointers))
    print(f"  wrote {WORKSHEET.relative_to(ROOT)} ({len(pointers)} items to label)")

    from collections import Counter

    for repo, count in Counter(p.repo for p in pointers).most_common():
        print(f"    {repo:<18} {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
