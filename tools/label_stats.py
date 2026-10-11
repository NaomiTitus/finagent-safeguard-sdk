#!/usr/bin/env python3
"""Statistics over the frozen labels, and the ceiling they imply.

    python3 tools/label_stats.py

Reads ``benchmark/labels.jsonl`` and reports what the corpus actually contains:
the positive rate, the per-source breakdown, the detector's rate on the same
items, and the annotator's agreement with themselves.

Every number the project quotes about the benchmark comes from here rather than
from a figure someone wrote down once. Two figures in this repository have
already had to be corrected for being hand-copied -- "0 of 66" and a
transcribed label count -- so the rule is that a quotable number is a command.

The agreement ceiling is the point of the second pass
-----------------------------------------------------
A classifier cannot be meaningfully scored above the rate at which the
annotator agrees with themselves. Reporting an accuracy without that ceiling
beside it makes the two mean opposite things: 0.85 against a ceiling of 0.95 is
a weak result, and 0.85 against a ceiling of 0.76 is suspicious.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "benchmark/labels.jsonl"
POINTERS = ROOT / "benchmark/pointers.jsonl"
DIGEST = ROOT / "benchmark/labels.sha256"

#: Landis & Koch bands, as in tools/kappa.py. A reading aid, not a law.
BANDS = ((0.81, "near-perfect"), (0.61, "substantial"), (0.41, "moderate"),
         (0.21, "fair"), (0.00, "slight"))


def band(value: float) -> str:
    for floor, name in BANDS:
        if value >= floor:
            return name
    return "worse than chance"


def cohen_kappa(pairs: list[tuple[bool, bool]]) -> tuple[float, float, float]:
    """Agreement corrected for chance, over (first pass, second pass) pairs."""
    n = len(pairs)
    if not n:
        return 0.0, 0.0, 0.0
    observed = sum(a == b for a, b in pairs) / n
    first = sum(a for a, _ in pairs) / n
    second = sum(b for _, b in pairs) / n
    expected = first * second + (1 - first) * (1 - second)
    if expected == 1.0:
        # Both passes answered one way for everything: agreement is total and
        # so is chance agreement, so nothing was demonstrated.
        return 0.0, observed, expected
    return (observed - expected) / (1 - expected), observed, expected


def _load() -> tuple[dict[str, dict[str, object]], dict[str, bool], dict[str, dict[str, object]]]:
    if not LABELS.exists():
        print(f"no labels at {LABELS.relative_to(ROOT)}", file=sys.stderr)
        raise SystemExit(2)

    first: dict[str, dict[str, object]] = {}
    second: dict[str, bool] = {}
    for line in LABELS.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("pass") == 2:
            second[str(row["item_id"])] = bool(row["regulated"])
        else:
            first[str(row["item_id"])] = row

    pointers = {
        str(r["item_id"]): r
        for r in (
            json.loads(line)
            for line in POINTERS.read_text().splitlines()
            if line.strip()
        )
    }
    return first, second, pointers


def _is_test(pointer: dict[str, object]) -> bool:
    """Whether the item is a test or example, decided from the path.

    Mechanical on purpose. ``docs/labelling-decisions.md`` records why this must
    not live in the labels: anything a machine can decide should be decided by
    the machine, where it can be changed without re-labelling.
    """
    path = str(pointer["path"]).lower()
    function = str(pointer["function"]).lower()
    return (
        "/test" in path
        or path.startswith("test")
        or "_test" in path
        or function.startswith("test_")
        or "example" in path
    )


def _detector_rate(pointers: dict[str, dict[str, object]]) -> dict[str, bool] | None:
    """What the current linter says about each item, for comparison only.

    Never used to choose or weight anything -- the sample was drawn by
    repository, deliberately not by the rule's verdict, because labels derived
    from the tool being measured are what inflated the actionable-warning
    literature (Kang, Aw & Lo, arXiv 2202.05982).
    """
    import sysconfig

    sources = Path(
        "/private/tmp/claude-501/-Users-naomi-Documents-projects-finagent-safeguard-sdk"
        "/821d6a65-f513-48f1-b0c4-309ae435eb16/scratchpad/corpus-src"
    )
    if not sources.exists():
        return None

    sys.path.insert(0, str(ROOT))
    from finagent_safeguard.cli import linter

    stdlib = Path(sysconfig.get_paths()["stdlib"])
    out: dict[str, bool] = {}
    cache: dict[Path, set[str]] = {}
    for item_id, pointer in pointers.items():
        repo = str(pointer["repo"])
        base = stdlib if repo == "cpython-stdlib" else sources / repo
        path = base / str(pointer["path"])
        if path not in cache:
            try:
                cache[path] = {
                    f.function for f in linter.scan_source(
                        path.read_text(encoding="utf-8"), path
                    )
                }
            except Exception:
                cache[path] = set()
        out[item_id] = str(pointer["function"]) in cache[path]
    return out


def main() -> int:
    first, second, pointers = _load()

    missing = set(pointers) - set(first)
    extra = set(first) - set(pointers)
    if missing or extra:
        print(f"labels do not match pointers: {sorted(missing | extra)}", file=sys.stderr)
        return 1

    if DIGEST.exists():
        actual = hashlib.sha256(LABELS.read_bytes()).hexdigest()
        recorded = DIGEST.read_text().split()[0]
        state = "matches" if actual == recorded else "DOES NOT MATCH"
        print(f"labels digest       {actual[:16]}... {state} benchmark/labels.sha256")
        if actual != recorded:
            print("  The frozen labels have been edited since they were sealed.",
                  file=sys.stderr)
            return 1

    total = len(first)
    yes = sum(bool(r["regulated"]) for r in first.values())
    flagged = sum(bool(r.get("flagged")) for r in first.values())
    print(f"\nitems               {total}")
    print(f"regulated           {yes}  ({yes / total:.1%})")
    print(f"not regulated       {total - yes}")
    print(f"flagged as hard     {flagged}")

    print("\nby source")
    per: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for item_id, row in first.items():
        bucket = per[str(pointers[item_id]["repo"])]
        bucket[0] += 1
        bucket[1] += bool(row["regulated"])
    for repo, (n, y) in sorted(per.items(), key=lambda kv: -kv[1][1] / kv[1][0]):
        print(f"  {repo:<18} {y:>3}/{n:<4} {y / n:>5.0%}")

    print("\nby kind (decided from the path, not from the labels)")
    for kind, want in (("test or example", True), ("production", False)):
        ids = [i for i in first if _is_test(pointers[i]) is want]
        y = sum(bool(first[i]["regulated"]) for i in ids)
        print(f"  {kind:<18} {y:>3}/{len(ids):<4} {y / len(ids):>5.0%}")

    print("\nagreement ceiling")
    pairs = [(bool(first[i]["regulated"]), second[i]) for i in second if i in first]
    kappa, observed, expected = cohen_kappa(pairs)
    print(f"  re-checked items   {len(pairs)}")
    print(f"  raw agreement      {round(observed * len(pairs))}/{len(pairs)} = {observed:.1%}")
    print(f"  expected by chance {expected:.3f}")
    print(f"  Cohen's kappa      {kappa:.3f}  ({band(kappa)})")
    disagreed = [i for i in second if i in first and bool(first[i]["regulated"]) != second[i]]
    for item_id in sorted(disagreed):
        print(f"    changed: {item_id:<22} {pointers[item_id]['function']}")
    if kappa < 0.40:
        print("\n  Below 0.40. The question is not reliably answerable and no")
        print("  classifier should be scored against these labels.")
        return 1

    detector = _detector_rate(pointers)
    if detector is None:
        print("\ndetector comparison   skipped (corpus sources not present)")
        return 0

    fires = sum(detector.values())
    print(f"\nthe current linter, on the same items")
    print(f"  fires on           {fires}  ({fires / total:.1%})")
    confusion = Counter(
        (bool(first[i]["regulated"]), detector[i]) for i in first
    )
    tp = confusion[(True, True)]
    fp = confusion[(False, True)]
    fn = confusion[(True, False)]
    print(f"  true positives     {tp}")
    print(f"  false positives    {fp}")
    print(f"  false negatives    {fn}")
    if tp + fp:
        print(f"  precision          {tp / (tp + fp):.1%}")
    if tp + fn:
        print(f"  recall             {tp / (tp + fn):.1%}")
    print("\n  This is the baseline the classifier has to beat, measured against")
    print("  labels drawn independently of it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
