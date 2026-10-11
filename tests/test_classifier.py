"""Tests for the advisory classifier's arithmetic.

The classifier is a **ranker**, not a guard: nothing in the linter's write path
consults it, and a wrong score misorders a review queue rather than mislabels
a function. So these tests do not pin its accuracy -- that is measured
leave-one-repository-out in `tools/train_classifier.py`, and reported as a
number with a confidence interval rather than asserted as a threshold.

What they pin is narrower and more durable: that the implementation computes
the objective it documents, and that the three ways a stored model can be
silently wrong all fail loudly instead.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from finagent_safeguard.cli.classifier import Model, fit, sigmoid
from finagent_safeguard.cli.features import FEATURE_NAMES, MODEL_FEATURES


class TestSigmoid:
    def test_it_is_a_half_at_zero(self) -> None:
        assert sigmoid(0.0) == pytest.approx(0.5)

    def test_it_is_symmetric(self) -> None:
        assert sigmoid(2.0) + sigmoid(-2.0) == pytest.approx(1.0)

    @pytest.mark.parametrize("z", [800.0, -800.0, 1e9, -1e9])
    def test_a_confident_margin_does_not_overflow(self, z: float) -> None:
        """``math.exp(800)`` raises OverflowError, and a large margin is what a
        confident model produces -- so the naive form fails on the *easy* cases
        while looking correct on every test that stays near the origin.
        """
        value = sigmoid(z)
        assert math.isfinite(value)
        assert 0.0 <= value <= 1.0

    def test_it_saturates_towards_the_right_end(self) -> None:
        assert sigmoid(800.0) > 0.99
        assert sigmoid(-800.0) < 0.01


def _separable() -> tuple[list[dict[str, float]], list[bool]]:
    """A corpus where one feature carries the label and the other is noise."""
    rows: list[dict[str, float]] = []
    labels: list[bool] = []
    for i in range(40):
        positive = i % 2 == 0
        rows.append({"signal": 1.0 if positive else 0.0, "noise": float(i % 5)})
        labels.append(positive)
    return rows, labels


NAMES = ("signal", "noise")


class TestFit:
    def test_it_learns_the_feature_that_carries_the_label(self) -> None:
        rows, labels = _separable()
        model = fit(rows, labels, names=NAMES)
        assert model.weights["signal"] > 0.0
        assert model.weights["signal"] > abs(model.weights["noise"])

    def test_it_ranks_positives_above_negatives(self) -> None:
        """The only property the tool actually relies on. A ranker may be
        badly calibrated and still be useful; it may not be badly ordered."""
        rows, labels = _separable()
        model = fit(rows, labels, names=NAMES)
        positives = [model.score(r) for r, y in zip(rows, labels) if y]
        negatives = [model.score(r) for r, y in zip(rows, labels) if not y]
        assert min(positives) > max(negatives)

    def test_it_is_deterministic(self) -> None:
        """What makes it eligible to run in CI at all. No seed, no shuffle, no
        model server -- the same input gives the same score every time."""
        rows, labels = _separable()
        first = fit(rows, labels, names=NAMES)
        second = fit(rows, labels, names=NAMES)
        assert first.weights == second.weights
        assert first.intercept == second.intercept

    def test_the_intercept_learns_the_base_rate(self) -> None:
        """The intercept is deliberately unregularised. With no informative
        feature the model should fall back to the prior, and at a 10% base rate
        that prior is a real quantity, not something to shrink towards zero.
        """
        rows = [{"signal": 1.0, "noise": 1.0} for _ in range(100)]
        labels = [i < 10 for i in range(100)]
        model = fit(rows, labels, names=NAMES)
        # Tight on purpose. With uninformative features the maximum-likelihood
        # intercept is exactly log(p / (1 - p)), so the fit should reach the
        # base rate to within numerical convergence. At abs=0.03 this test
        # passed even with the intercept regularised (which lands on 0.1199),
        # so the loose tolerance was hiding the exact bias it names.
        assert model.score(rows[0]) == pytest.approx(0.1, abs=0.005)

    def test_a_constant_feature_still_yields_a_finite_score(self) -> None:
        """Standardisation divides by the spread, and a feature that never
        varies has a spread of zero. In a leave-one-repository-out fold that
        is routine, not exotic."""
        rows = [{"signal": 1.0, "noise": 7.0} for _ in range(20)]
        labels = [i % 2 == 0 for i in range(20)]
        model = fit(rows, labels, names=NAMES)
        assert math.isfinite(model.score(rows[0]))

    def test_stronger_regularisation_shrinks_the_weights(self) -> None:
        rows, labels = _separable()
        loose = fit(rows, labels, names=NAMES, l2=0.01)
        tight = fit(rows, labels, names=NAMES, l2=20.0)
        assert abs(tight.weights["signal"]) < abs(loose.weights["signal"])

    def test_a_diverged_fit_is_refused_rather_than_returning_nan(self) -> None:
        """Found by this test file, not by the training runs.

        Gradient descent at a fixed rate diverges for a large enough `l2`, and
        the weights become NaN. Every comparison against NaN is False, so such
        a model does not score *badly* -- it scores unorderably, returning a
        float for every row while ranking the queue at random. A ranker that
        has quietly stopped ranking is the worst available outcome, so the fit
        refuses instead.
        """
        rows, labels = _separable()
        with pytest.raises(ValueError, match="diverged"):
            fit(rows, labels, names=NAMES, l2=10_000.0)

    def test_mismatched_rows_and_labels_are_refused(self) -> None:
        rows, labels = _separable()
        with pytest.raises(ValueError, match="rows against"):
            fit(rows, labels[:-1], names=NAMES)

    def test_an_empty_corpus_is_refused(self) -> None:
        with pytest.raises(ValueError, match="nothing to fit"):
            fit([], [], names=NAMES)


class TestScore:
    def test_a_missing_feature_is_refused_not_read_as_zero(self) -> None:
        """Features are positional in effect even though they are addressed by
        name. A missing key defaulted to zero would produce a plausible score
        from an incomplete row, which is worse than no score."""
        rows, labels = _separable()
        model = fit(rows, labels, names=NAMES)
        with pytest.raises(ValueError, match="missing features"):
            model.score({"signal": 1.0})

    def test_the_score_is_a_probability(self) -> None:
        rows, labels = _separable()
        model = fit(rows, labels, names=NAMES)
        for row in rows:
            assert 0.0 <= model.score(row) <= 1.0


class TestSerialisation:
    def _written(self, tmp_path: Path) -> tuple[Model, Path]:
        rows, labels = _separable()
        model = fit(rows, labels, names=NAMES)
        path = tmp_path / "model.json"
        path.write_text(model.to_json())
        return model, path

    def test_json_is_readable_rather_than_a_pickle(self, tmp_path: Path) -> None:
        """The reason the model is hand-written at all: a reviewer at a bank
        can read the coefficients. A pickle is a binary blob nobody can
        review and that breaks on a library version bump."""
        model, path = self._written(tmp_path)
        data = json.loads(path.read_text())
        assert set(data) == {"weights", "intercept", "feature_names", "provenance"}
        assert set(data["weights"]) == set(NAMES)

    def test_a_model_fitted_on_other_features_is_refused(self, tmp_path: Path) -> None:
        """The quiet failure this guards: every score would still be produced,
        just wrongly, because a renamed feature reads as an absent one."""
        _, path = self._written(tmp_path)
        with pytest.raises(ValueError, match="different features"):
            Model.load(path)

    def test_a_matching_model_round_trips(self, tmp_path: Path) -> None:
        row = {name: 1.0 for name in FEATURE_NAMES}
        model = fit([row, {name: 0.0 for name in FEATURE_NAMES}], [True, False])
        path = tmp_path / "model.json"
        path.write_text(model.to_json())
        loaded = Model.load(path)
        assert loaded.feature_names == MODEL_FEATURES
        assert loaded.score(row) == pytest.approx(model.score(row))

    def test_provenance_travels_with_the_artefact(self, tmp_path: Path) -> None:
        """Everything needed to reproduce the fit is kept with the model, not
        in a commit message that the file will outlive."""
        rows, labels = _separable()
        model = fit(rows, labels, names=NAMES, provenance={"corpus": "train"})
        assert json.loads(model.to_json())["provenance"] == {"corpus": "train"}
