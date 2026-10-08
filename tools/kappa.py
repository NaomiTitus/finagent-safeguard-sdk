#!/usr/bin/env python3
"""Inter-rater agreement for the category taxonomy.

Step 0 of the category plan is a gate, not a formality: if two competent raters
cannot agree on which of the six categories a function falls under, the
taxonomy is unanswerable and no classifier should be built against it. This
module computes the number that decides.

Cohen's kappa corrects raw agreement for the agreement two raters would reach
by chance alone. That correction is the whole point. Two raters who both label
90% of a skewed sample "payment" agree often while demonstrating nothing, and
raw agreement cannot tell that apart from skill.

    kappa = (observed - expected_by_chance) / (1 - expected_by_chance)

Read as: of the agreement that was available to be earned, how much was.

Stdlib only, like the rest of the SDK.
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path

#: Landis & Koch (1977) convention. Widely used, and arbitrary -- it is a
#: reading aid, not a law. The project's own gate is the 0.40 boundary.
BANDS = (
    (0.81, "near-perfect"),
    (0.61, "substantial"),
    (0.41, "moderate"),
    (0.21, "fair"),
    (0.00, "slight"),
    (-1.0, "worse than chance"),
)

#: Below this, Step 0 fails and the taxonomy is the thing to fix.
GATE = 0.40


def band(k: float) -> str:
    for floor, name in BANDS:
        if k >= floor:
            return name
    return "worse than chance"


def cohen_kappa(a: list[str], b: list[str]) -> tuple[float, float, float]:
    """Return (kappa, observed agreement, expected agreement).

    Raises on mismatched lengths rather than silently truncating, because a
    dropped item shifts every subsequent pairing and would corrupt the result
    invisibly.
    """
    if len(a) != len(b):
        raise ValueError(f"rater A has {len(a)} labels, rater B has {len(b)}")
    if not a:
        raise ValueError("no labels to compare")

    n = len(a)
    observed = sum(1 for x, y in zip(a, b) if x == y) / n

    count_a, count_b = Counter(a), Counter(b)
    expected = sum(
        (count_a[label] / n) * (count_b[label] / n)
        for label in set(count_a) | set(count_b)
    )

    if expected == 1.0:
        # Both raters used exactly one label for everything. Agreement is
        # total and chance agreement is also total, so kappa is undefined
        # (0/0). Report 0.0: the raters demonstrated nothing.
        return 0.0, observed, expected

    return (observed - expected) / (1 - expected), observed, expected


def standard_error(a: list[str], b: list[str]) -> float:
    """Approximate standard error, for a confidence interval on kappa.

    Uses the common large-sample approximation. With 40 items the interval is
    wide, which is itself worth reporting: a point estimate of 0.45 whose
    interval reaches below the gate has not passed the gate.
    """
    kappa, observed, expected = cohen_kappa(a, b)
    n = len(a)
    if expected == 1.0:
        return float("nan")
    return math.sqrt(observed * (1 - observed) / (n * (1 - expected) ** 2))


def per_label_agreement(a: list[str], b: list[str]) -> dict[str, tuple[int, int]]:
    """Agreed/total per label, to show *where* the taxonomy breaks down.

    A single kappa hides the useful detail. If raters agree on payments and
    disagree on every AML case, the fix is to the AML definition, not to the
    whole scheme.
    """
    out: dict[str, tuple[int, int]] = {}
    for label in sorted(set(a) | set(b)):
        seen = [(x, y) for x, y in zip(a, b) if label in (x, y)]
        agreed = sum(1 for x, y in seen if x == y)
        out[label] = (agreed, len(seen))
    return out


def confusions(a: list[str], b: list[str]) -> list[tuple[str, str, int]]:
    """The disagreeing pairs, most frequent first.

    This is the actionable output. A pair that recurs is a definition problem
    with a name, and usually a fixable one.
    """
    pairs = Counter(
        tuple(sorted((x, y))) for x, y in zip(a, b) if x != y
    )
    return [(x, y, n) for (x, y), n in pairs.most_common()]


def report(a: list[str], b: list[str]) -> str:
    kappa, observed, expected = cohen_kappa(a, b)
    se = standard_error(a, b)
    lo, hi = kappa - 1.96 * se, kappa + 1.96 * se

    lines = [
        f"items compared        : {len(a)}",
        f"raw agreement         : {observed:.3f}  ({round(observed * len(a))}/{len(a)})",
        f"expected by chance    : {expected:.3f}",
        f"Cohen's kappa         : {kappa:.3f}   ({band(kappa)})",
        f"95% interval          : {lo:.3f} to {hi:.3f}",
        "",
    ]

    verdict = "PASS" if lo >= GATE else "FAIL"
    lines.append(f"gate (kappa >= {GATE}) : {verdict}")
    if verdict == "FAIL":
        if kappa >= GATE:
            lines.append(
                "  The point estimate clears the gate but the interval does not. "
                "Label more items before concluding."
            )
        else:
            lines.append(
                "  The taxonomy is the thing to fix, not the classifier. "
                "Collapse or redefine the categories raters cannot separate."
            )

    lines += ["", "per label (agreed/seen):"]
    for label, (agreed, seen) in per_label_agreement(a, b).items():
        rate = f"{agreed / seen:.2f}" if seen else "n/a"
        lines.append(f"  {label:<34} {agreed:>3}/{seen:<3} {rate}")

    bad = confusions(a, b)
    if bad:
        lines += ["", "disagreements, most frequent first:"]
        for x, y, n in bad:
            lines.append(f"  {n:>3}x  {x}  vs  {y}")

    return "\n".join(lines)


def main(argv: list[str]) -> int:
    """Compare two label files. Each is JSON: {"item_id": "LABEL", ...}.

    Keyed by item rather than ordered by line, so a rater who reorders or
    omits an item is caught instead of silently shifting every pairing.
    """
    if len(argv) != 2:
        print(__doc__)
        print("usage: kappa.py rater_a.json rater_b.json", file=sys.stderr)
        return 2

    ra, rb = (json.loads(Path(p).read_text()) for p in argv)
    shared = sorted(set(ra) & set(rb))
    if missing := (set(ra) ^ set(rb)):
        print(f"note: {len(missing)} item(s) labelled by only one rater, excluded\n")
    if not shared:
        print("no items labelled by both raters", file=sys.stderr)
        return 1

    print(report([ra[k] for k in shared], [rb[k] for k in shared]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
