#!/usr/bin/env python3
"""Train the classifier on the 996, then test it once on the human 160.

    python3 tools/train_classifier.py --cv        # cross-validate only
    python3 tools/train_classifier.py --ablate    # which feature groups earn their place
    python3 tools/train_classifier.py --final     # fit, test on the human set, write the model

The discipline this script exists to enforce
--------------------------------------------
Model selection happens on the **training** corpus, by cross-validation. The
human-labelled 160 are touched once, by ``--final``, and never used to choose a
threshold, a feature, or a regularisation strength. Every number from
``--final`` is therefore a measurement rather than a fit.

That is not a precaution against carelessness, it is the whole reason the human
set is small and sealed. Kang, Aw & Lo (arXiv 2202.05982) found the
actionable-warning literature inflated by exactly this: tuning against the set
that is later reported.

Three bounds are printed with every result, because a score means nothing
alone:

    0.762   the human's agreement with herself -- the ceiling
    0.805   Sonnet's agreement with the human -- the teacher, and a distilled
            model cannot exceed it
    61% / 31%   the word-list rule's precision and recall -- the baseline to beat
"""

from __future__ import annotations

import argparse
import ast
import json
import random
import sys
import sysconfig
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finagent_safeguard.cli.classifier import Model, fit  # noqa: E402
from finagent_safeguard.cli.features import (  # noqa: E402
    EXCLUDED_GROUPS,
    FEATURE_GROUPS,
    FEATURE_NAMES,
    MODEL_FEATURES,
    extract,
)

SOURCES = Path(
    "/private/tmp/claude-501/-Users-naomi-Documents-projects-finagent-safeguard-sdk"
    "/821d6a65-f513-48f1-b0c4-309ae435eb16/scratchpad/corpus-src"
)
MODEL_OUT = ROOT / "finagent_safeguard/cli/model.json"

HUMAN_CEILING = 0.762
TEACHER_CEILING = 0.805
RULE_PRECISION, RULE_RECALL = 0.611, 0.314

FOLDS = 5
REPEATS = 5
SEED = 20261011


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------
def _rows(pointers: Path, labels: Path) -> tuple[list[dict[str, float]], list[bool], list[str]]:
    """Features and labels for one corpus, by (repo, path, line).

    Functions are located by line number rather than by name, because a name
    can repeat within a file -- ``validate`` appears in several schwifty
    checksum classes -- and matching on it would silently pair a label with the
    wrong body.
    """
    label_of: dict[str, list[bool]] = {}
    for line in labels.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("pass") == 2 or row.get("regulated") is None:
            continue
        label_of.setdefault(str(row["item_id"]), []).append(bool(row["regulated"]))

    stdlib = Path(sysconfig.get_paths()["stdlib"])
    cache: dict[Path, tuple[str, dict[int, ast.AST]]] = {}
    features: list[dict[str, float]] = []
    targets: list[bool] = []
    ids: list[str] = []

    for line in pointers.read_text().splitlines():
        if not line.strip():
            continue
        p = json.loads(line)
        item = str(p["item_id"])
        votes = label_of.get(item)
        if not votes:
            continue
        base = stdlib if p["repo"] == "cpython-stdlib" else SOURCES / str(p["repo"])
        path = base / str(p["path"])
        if path not in cache:
            try:
                text = path.read_text(encoding="utf-8")
                tree = ast.parse(text)
            except (OSError, SyntaxError, UnicodeDecodeError):
                cache[path] = ("", {})
            else:
                cache[path] = (
                    text,
                    {
                        n.lineno: n
                        for n in ast.walk(tree)
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    },
                )
        text, index = cache[path]
        node = index.get(int(p["first_line"]))
        if node is None or not text:
            continue
        segment = "\n".join(
            text.splitlines()[int(p["first_line"]) - 1 : int(p["last_line"])]
        )
        assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        features.append(extract(node, segment))
        targets.append(sum(votes) * 2 > len(votes))
        ids.append(item)
    return features, targets, ids


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------
def _auc(scores: list[float], truth: list[bool]) -> float:
    """Area under the ROC curve, by rank, with ties shared.

    The headline metric for this model, because its job is to **order** a
    review queue rather than to answer yes or no. Kappa and precision are
    threshold-dependent, and a model trained at one base rate and scored at
    another can rank correctly while thresholding badly -- which is exactly
    what leave-one-repository-out does to it. Reporting only kappa there
    conflates a calibration failure with a discrimination failure.

    Returns 0.5 when one class is absent: there is nothing to discriminate, and
    a NaN would silently poison any average it entered.
    """
    positives = [s for s, t in zip(scores, truth) if t]
    negatives = [s for s, t in zip(scores, truth) if not t]
    if not positives or not negatives:
        return 0.5
    wins = 0.0
    for p in positives:
        for n in negatives:
            if p > n:
                wins += 1.0
            elif p == n:
                wins += 0.5
    return wins / (len(positives) * len(negatives))


def _measure(scores: list[float], truth: list[bool], threshold: float) -> dict[str, float]:
    tp = sum(1 for s, t in zip(scores, truth) if s >= threshold and t)
    fp = sum(1 for s, t in zip(scores, truth) if s >= threshold and not t)
    fn = sum(1 for s, t in zip(scores, truth) if s < threshold and t)
    tn = len(truth) - tp - fp - fn
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    po = (tp + tn) / len(truth)
    # Cohen's kappa against the labels, so the figure is comparable to every
    # other agreement number in this project rather than only to itself.
    p_model = (tp + fp) / len(truth)
    p_truth = (tp + fn) / len(truth)
    pe = p_model * p_truth + (1 - p_model) * (1 - p_truth)
    kappa = (po - pe) / (1 - pe) if pe != 1.0 else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "kappa": kappa,
        "accuracy": po,
        "auc": _auc(scores, truth),
        "tp": float(tp), "fp": float(fp), "fn": float(fn), "tn": float(tn),
    }


def _folds(targets: list[bool], k: int, rng: random.Random) -> list[list[int]]:
    """Stratified: each fold keeps the class balance.

    With about 21% positive, an unstratified fold of 200 can easily land with
    too few positives to measure precision at all, and the resulting spread
    would be an artefact of the split rather than of the model.
    """
    positive = [i for i, t in enumerate(targets) if t]
    negative = [i for i, t in enumerate(targets) if not t]
    rng.shuffle(positive)
    rng.shuffle(negative)
    out: list[list[int]] = [[] for _ in range(k)]
    for group in (positive, negative):
        for position, index in enumerate(group):
            out[position % k].append(index)
    return out


def _cross_validate(
    features: list[dict[str, float]],
    targets: list[bool],
    names: tuple[str, ...],
    *,
    repeats: int = REPEATS,
) -> list[dict[str, float]]:
    results: list[dict[str, float]] = []
    for repeat in range(repeats):
        rng = random.Random(SEED + repeat)
        for held in _folds(targets, FOLDS, rng):
            keep = [i for i in range(len(targets)) if i not in set(held)]
            model = fit(
                [features[i] for i in keep],
                [targets[i] for i in keep],
                names=names,
                steps=2500,
            )
            scores = [model.score(features[i]) for i in held]
            results.append(_measure(scores, [targets[i] for i in held], 0.5))
    return results


#: A fold needs this many positives before its kappa or precision means
#: anything. Below it the metric is dominated by one or two items.
#:
#: The published pooled LORO kappa of 0.017 was arrived at by averaging over
#: six folds, two of which held 0 and 1 positives -- unscoreable by
#: construction, and dragging the figure towards zero. Reporting that as "the
#: model does not generalise" conflated "cannot be measured here" with
#: "measured and found absent".
MIN_POSITIVES = 10


def _repo_of(item_id: str) -> str:
    """The repository an item came from, out of its id.

    Ids are ``[train-]<repo>:<index>``, so the repository is recoverable
    without re-reading the pointer files.
    """
    return item_id.split(":", 1)[0].removeprefix("train-")


def leave_one_repo_out(
    features: list[dict[str, float]],
    targets: list[bool],
    repos: list[str],
    names: tuple[str, ...],
) -> dict[str, dict[str, float]]:
    """Train on every repository but one, score on the one held out.

    This is the split the stratified cross-validation could not do. Both
    corpora are drawn from the same six repositories, whose positive rates run
    from 0% to 70%, so a model can score well by recognising which codebase a
    function came from -- and a stratified split puts the same repositories on
    both sides, so it cannot detect that.

    A feature that only works within a repository shows no gain here, which is
    the filter the project did not have.
    """
    out: dict[str, dict[str, float]] = {}
    for held in sorted(set(repos)):
        train = [i for i, r in enumerate(repos) if r != held]
        test = [i for i, r in enumerate(repos) if r == held]
        positives = sum(targets[i] for i in test)
        if not any(targets[i] for i in train):
            continue
        model = fit(
            [features[i] for i in train],
            [targets[i] for i in train],
            names=names,
            steps=2500,
        )
        scores = [model.score(features[i]) for i in test]
        truth = [targets[i] for i in test]
        measured = _measure(scores, truth, 0.5)
        measured["n"] = float(len(test))
        measured["positives"] = float(positives)
        measured["scoreable"] = 1.0 if positives >= MIN_POSITIVES else 0.0
        out[held] = measured
    return out


def _repo_prior_baseline(
    targets: list[bool], repos: list[str]
) -> dict[str, dict[str, float]]:
    """The thing any feature set must beat: guess from the repository alone.

    Under leave-one-repository-out this is deliberately crippled -- the held-out
    repository's own rate is unknown, so it can only fall back on the pooled
    rate of the others. That is the honest baseline for an unseen codebase, and
    it is why the in-corpus version of this baseline (kappa 0.503) was never a
    fair comparison.
    """
    out: dict[str, dict[str, float]] = {}
    for held in sorted(set(repos)):
        train = [i for i, r in enumerate(repos) if r != held]
        test = [i for i, r in enumerate(repos) if r == held]
        rate = sum(targets[i] for i in train) / len(train)
        scores = [rate] * len(test)
        truth = [targets[i] for i in test]
        measured = _measure(scores, truth, 0.5)
        measured["n"] = float(len(test))
        measured["positives"] = float(sum(truth))
        measured["scoreable"] = 1.0 if sum(truth) >= MIN_POSITIVES else 0.0
        out[held] = measured
    return out


def _macro(results: dict[str, dict[str, float]], key: str) -> float:
    """Average over scoreable folds only, unweighted.

    Unweighted so a 300-row repository does not drown a 41-row one, and
    scoreable-only so a fold with one positive cannot define the result.
    """
    usable = [m[key] for m in results.values() if m["scoreable"]]
    return sum(usable) / len(usable) if usable else 0.0


def _report_loro(
    results: dict[str, dict[str, float]], label: str
) -> None:
    print(f"\n{label}")
    print(f"  {'held-out repo':<18} {'n':>5} {'pos':>5} {'AUC':>7} {'kappa':>7} "
          f"{'prec':>6} {'rec':>6}")
    print("  " + "-" * 62)
    for repo, m in sorted(results.items()):
        mark = "" if m["scoreable"] else "   (too few positives to score)"
        print(f"  {repo:<18} {m['n']:>5.0f} {m['positives']:>5.0f} "
              f"{m['auc']:>7.3f} {m['kappa']:>7.3f} "
              f"{m['precision']:>5.0%} {m['recall']:>5.0%}{mark}")
    scoreable = [r for r, m in results.items() if m["scoreable"]]
    print(f"  macro over {len(scoreable)} scoreable fold(s): "
          f"AUC {_macro(results, 'auc'):.3f}   kappa {_macro(results, 'kappa'):.3f}")


def _spread(results: list[dict[str, float]], key: str) -> tuple[float, float, float]:
    values = sorted(r[key] for r in results)
    mid = sum(values) / len(values)
    return values[0], mid, values[-1]


def _report_cv(results: list[dict[str, float]], label: str) -> None:
    print(f"\n{label}  ({len(results)} folds)")
    for key in ("kappa", "precision", "recall", "f1"):
        low, mean, high = _spread(results, key)
        print(f"  {key:<10} {mean:>6.3f}   range {low:.3f} to {high:.3f}")


# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cv", action="store_true", help="cross-validate on the training corpus")
    ap.add_argument("--ablate", action="store_true", help="drop each feature group in turn")
    ap.add_argument("--final", action="store_true", help="fit, test on the human set, write the model")
    ap.add_argument("--loro", action="store_true",
                    help="leave-one-repository-out, against the repo-prior baseline")
    ap.add_argument("--loro-ablate", action="store_true",
                    help="drop each feature group and re-measure under LORO")
    args = ap.parse_args(argv)

    train_x, train_y, train_ids = _rows(
        ROOT / "benchmark/train-pointers.jsonl", ROOT / "benchmark/train-labels.jsonl"
    )
    if len(train_x) < 500:
        print(f"only built {len(train_x)} training rows; is the corpus cloned?", file=sys.stderr)
        return 2
    print(f"training corpus   {len(train_x)} rows, {sum(train_y)} positive "
          f"({sum(train_y) / len(train_y):.1%})")
    print(f"features          {len(MODEL_FEATURES)} fitted of {len(FEATURE_NAMES)} produced")
    print(f"\nbounds to read every figure against")
    print(f"  human ceiling     kappa {HUMAN_CEILING:.3f}")
    print(f"  teacher (Sonnet)  kappa {TEACHER_CEILING:.3f}")
    print(f"  word-list rule    precision {RULE_PRECISION:.0%}, recall {RULE_RECALL:.0%}")

    repos = [_repo_of(i) for i in train_ids]

    if args.loro or args.loro_ablate:
        print("\n" + "=" * 70)
        print("LEAVE-ONE-REPOSITORY-OUT")
        print("The split the stratified cross-validation could not do. Both corpora")
        print("draw from the same six repositories, whose positive rates run from 0%")
        print("to 70%, so a model can score by recognising the codebase -- and a")
        print("stratified split puts the same repositories on both sides.")
        print("=" * 70)
        baseline = _repo_prior_baseline(train_y, repos)
        _report_loro(baseline, "BASELINE: the pooled rate of the other repositories")
        loro_full = leave_one_repo_out(train_x, train_y, repos, MODEL_FEATURES)
        _report_loro(loro_full, f"MODEL: the {len(MODEL_FEATURES)} fitted features")
        print(f"\n  model minus baseline:  AUC {_macro(loro_full, 'auc') - _macro(baseline, 'auc'):+.3f}"
              f"   kappa {_macro(loro_full, 'kappa') - _macro(baseline, 'kappa'):+.3f}")
        print("\n  AUC is the figure to read: the deliverable ranks a review queue,")
        print("  and a model trained at one base rate and scored at another can rank")
        print("  correctly while thresholding badly.")

    if args.loro_ablate:
        base_auc = _macro(loro_full, "auc")
        print(f"\nLORO ablation -- macro AUC with each group removed (full {base_auc:.3f})")
        print("  A feature that only works inside a repository shows no gain here.")
        print("  " + "-" * 62)
        for group, members in FEATURE_GROUPS.items():
            if group in EXCLUDED_GROUPS:
                # Removing a group the model was never fitted on cannot change
                # anything, and printing a +0.000 delta for it reads as "this
                # group is useless" when the truth is "this group is already
                # out". The reason it is out belongs on the row.
                print(
                    f"  without {group:<12} not fitted -- excluded at "
                    f"-0.069 AUC, see EXCLUDED_GROUPS"
                )
                continue
            kept = tuple(n for n in MODEL_FEATURES if n not in members)
            if not kept:
                continue
            loro_without = leave_one_repo_out(train_x, train_y, repos, kept)
            score = _macro(loro_without, "auc")
            delta = base_auc - score
            verdict = "earns it" if delta > 0.01 else "no measurable gain"
            print(f"  without {group:<12} AUC {score:.3f}   delta {delta:+.3f}   {verdict}")

    if args.cv or not (args.ablate or args.final or args.loro or args.loro_ablate):
        _report_cv(_cross_validate(train_x, train_y, MODEL_FEATURES), "cross-validated")

    if args.ablate:
        full = _cross_validate(train_x, train_y, MODEL_FEATURES, repeats=2)
        _, base, _ = _spread(full, "f1")
        print(f"\nablation -- F1 with each group removed (full model {base:.3f})")
        for group, members in FEATURE_GROUPS.items():
            kept = tuple(n for n in MODEL_FEATURES if n not in members)
            if not kept:
                continue
            without = _cross_validate(train_x, train_y, kept, repeats=2)
            _, score, _ = _spread(without, "f1")
            delta = base - score
            verdict = "earns it" if delta > 0.01 else "no measurable gain"
            print(f"  without {group:<12} {score:.3f}   delta {delta:+.3f}   {verdict}")

    if args.final:
        test_x, test_y, test_ids = _rows(
            ROOT / "benchmark/pointers.jsonl", ROOT / "benchmark/labels.jsonl"
        )
        print(f"\nheld-out human set  {len(test_x)} rows, {sum(test_y)} positive")

        model = fit(
            train_x,
            train_y,
            names=MODEL_FEATURES,
            steps=6000,
            provenance={
                "trained_on": "benchmark/train-labels.jsonl",
                "labelled_by": "claude-sonnet-5, mode of 3 runs",
                "rows": str(len(train_x)),
                "positives": str(sum(train_y)),
                "tested_on": "benchmark/labels.jsonl (human, sealed)",
                "human_ceiling_kappa": str(HUMAN_CEILING),
                "teacher_kappa": str(TEACHER_CEILING),
            },
        )
        scores = [model.score(row) for row in test_x]

        print("\nrisk-coverage on the human set -- precision at each threshold")
        print(f"  {'threshold':>9} {'coverage':>9} {'precision':>10} {'recall':>8}")
        for threshold in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
            m = _measure(scores, test_y, threshold)
            flagged = sum(1 for s in scores if s >= threshold)
            print(f"  {threshold:>9.1f} {flagged / len(scores):>8.0%} "
                  f"{m['precision']:>10.0%} {m['recall']:>8.0%}")

        at_half = _measure(scores, test_y, 0.5)
        print(f"\nat threshold 0.5")
        for key in ("kappa", "precision", "recall", "f1"):
            print(f"  {key:<10} {at_half[key]:.3f}")
        print(f"  tp {at_half['tp']:.0f}  fp {at_half['fp']:.0f}  "
              f"fn {at_half['fn']:.0f}  tn {at_half['tn']:.0f}")

        print("\nagainst the bounds")
        print(f"  vs word-list rule   precision {at_half['precision']:.0%} against "
              f"{RULE_PRECISION:.0%}, recall {at_half['recall']:.0%} against {RULE_RECALL:.0%}")
        print(f"  vs teacher          kappa {at_half['kappa']:.3f} against {TEACHER_CEILING:.3f}")
        print(f"  vs human ceiling    kappa {at_half['kappa']:.3f} against {HUMAN_CEILING:.3f}")

        print("\nlearned coefficients, largest first")
        for name, weight in sorted(
            model.weights.items(), key=lambda kv: -abs(kv[1])
        ):
            print(f"  {name:<20} {weight:+.3f}")
        print(f"  {'(intercept)':<20} {model.intercept:+.3f}")

        MODEL_OUT.write_text(model.to_json() + "\n")
        print(f"\nwritten to {MODEL_OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
