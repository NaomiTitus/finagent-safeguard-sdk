"""L2-regularised logistic regression, in the standard library.

Twenty-odd coefficients in a JSON file, loaded at scan time and evaluated with
arithmetic. No numpy, no scikit-learn, no pickle.

Why hand-written rather than imported
-------------------------------------
Three reasons, and the third is the one that matters.

The SDK is stdlib-only by design, so a dependency would be a change of
character rather than a convenience. A pickled estimator is a binary blob
nobody can review and that breaks on a library version bump. And a reviewer at
a bank can read sixteen named coefficients in a minute and see exactly what the
model believes -- which is the same reason CodeQL ships word lists and Bearer
ships rules rather than weights.

Logistic regression specifically, rather than a tree or an ensemble, because it
returns a **calibrated probability**. Precision at a chosen coverage needs a
score to threshold; a label alone cannot produce a risk-coverage curve. With
about 213 positives in the training corpus, roughly twenty features is already
the ceiling at the usual ten events per feature, so a model with more capacity
would be fitting noise and reporting it as skill.

Determinism
-----------
Fully deterministic: the same input gives the same score, every time, with no
network and no model server. That is what makes it eligible to run in CI at
all, and it is a property of the coefficients being data rather than a service.
Whether it *should* gate a build is a separate question that belongs to the
measured precision, not to this module.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from finagent_safeguard.cli.features import FEATURE_NAMES

__all__ = ["Model", "fit", "sigmoid"]


def sigmoid(z: float) -> float:
    """Logistic function, written to not overflow.

    ``math.exp(800)`` raises OverflowError, and a large positive margin is
    exactly what a confident model produces, so the naive form fails on the
    easy cases rather than the hard ones.
    """
    if z >= 0.0:
        return 1.0 / (1.0 + math.exp(-min(z, 500.0)))
    e = math.exp(max(z, -500.0))
    return e / (1.0 + e)


@dataclass(frozen=True, slots=True)
class Model:
    """A fitted model: one weight per feature, plus an intercept."""

    weights: dict[str, float]
    intercept: float
    #: Recorded so a stored model cannot be silently applied to a different
    #: feature set. Features are positional in effect even when addressed by
    #: name, and a renamed feature would otherwise read as a zero.
    feature_names: tuple[str, ...]
    #: Everything needed to reproduce the fit, kept with the artefact rather
    #: than in a commit message.
    provenance: dict[str, str]

    def score(self, features: dict[str, float]) -> float:
        """Probability that the function handles money or personal data."""
        missing = set(self.feature_names) - set(features)
        if missing:
            raise ValueError(f"missing features: {sorted(missing)}")
        z = self.intercept + sum(
            self.weights[name] * features[name] for name in self.feature_names
        )
        return sigmoid(z)

    def to_json(self) -> str:
        return json.dumps(
            {
                "weights": self.weights,
                "intercept": self.intercept,
                "feature_names": list(self.feature_names),
                "provenance": self.provenance,
            },
            indent=2,
            sort_keys=True,
        )

    @classmethod
    def load(cls, path: Path) -> Model:
        data = json.loads(path.read_text())
        names = tuple(data["feature_names"])
        if names != FEATURE_NAMES:
            # A stored model and a changed feature module is the quiet failure
            # this guards: every score would still be produced, just wrongly.
            raise ValueError(
                "stored model was fitted on different features; retrain.\n"
                f"  stored:  {names}\n  current: {FEATURE_NAMES}"
            )
        return cls(
            weights={str(k): float(v) for k, v in data["weights"].items()},
            intercept=float(data["intercept"]),
            feature_names=names,
            provenance={str(k): str(v) for k, v in data["provenance"].items()},
        )


def fit(
    rows: list[dict[str, float]],
    labels: list[bool],
    *,
    l2: float = 1.0,
    rate: float = 0.5,
    steps: int = 4000,
    names: tuple[str, ...] = FEATURE_NAMES,
    provenance: dict[str, str] | None = None,
) -> Model:
    """Fit by full-batch gradient descent on the regularised log-likelihood.

    Full-batch rather than stochastic because the corpus is a thousand rows:
    the whole gradient costs nothing and removes a source of run-to-run
    variation that would have to be explained.

    The intercept is deliberately **not** regularised. Penalising it would
    shrink the model's estimate of the base rate towards zero, and at 21%
    positive that is a real quantity the model should be free to learn.

    Features are standardised internally and the weights are returned on the
    original scale, so a reader of the JSON sees coefficients in the units the
    features are actually expressed in.
    """
    if len(rows) != len(labels):
        raise ValueError(f"{len(rows)} rows against {len(labels)} labels")
    if not rows:
        raise ValueError("nothing to fit")

    n = len(rows)
    # Standardise: the features mix counts, flags and ratios, and on raw scales
    # one gradient step that suits a 0/1 flag is far too large for a count.
    means = {k: sum(r[k] for r in rows) / n for k in names}
    spreads: dict[str, float] = {}
    for k in names:
        variance = sum((r[k] - means[k]) ** 2 for r in rows) / n
        spreads[k] = math.sqrt(variance) or 1.0

    scaled = [
        [(r[k] - means[k]) / spreads[k] for k in names] for r in rows
    ]
    y = [1.0 if label else 0.0 for label in labels]

    w = [0.0] * len(names)
    b = 0.0
    for _ in range(steps):
        gradients = [0.0] * len(names)
        bias_gradient = 0.0
        for row, target in zip(scaled, y):
            z = b + sum(wi * xi for wi, xi in zip(w, row))
            error = sigmoid(z) - target
            bias_gradient += error
            for j, xi in enumerate(row):
                gradients[j] += error * xi
        for j in range(len(w)):
            # L2 on the weights only; see the docstring on the intercept.
            w[j] -= rate * (gradients[j] / n + l2 * w[j] / n)
        b -= rate * bias_gradient / n

    # Back to original units, so the stored coefficients are readable.
    weights = {k: w[j] / spreads[k] for j, k in enumerate(names)}
    intercept = b - sum(w[j] * means[k] / spreads[k] for j, k in enumerate(names))

    return Model(
        weights=weights,
        intercept=intercept,
        feature_names=names,
        provenance=provenance or {},
    )
