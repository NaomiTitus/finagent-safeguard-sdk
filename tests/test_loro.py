"""Tests for the leave-one-repository-out measurement.

This measurement exists because the stratified cross-validation lied. Both
corpora are drawn from the same six repositories, whose positive rates run from
0% to 70%, so a model can score well by recognising which codebase a function
came from -- and a stratified split puts the same repositories on both sides,
which makes that undetectable.

Measured: a one-bit lookup of "is this python-sepaxml?" scored kappa 0.503
against the model's 0.511, and a paired bootstrap put the difference at +0.008
with 95% CI [-0.182, +0.208]. The model was indistinguishable from the lookup.

So these tests guard the filter itself, not the model.
"""

from __future__ import annotations

import pytest

from tools.train_classifier import (
    MIN_POSITIVES,
    _auc,
    _macro,
    _repo_of,
    leave_one_repo_out,
)


class TestAucIsRankBased:
    def test_perfect_separation_is_one(self) -> None:
        assert _auc([0.9, 0.8, 0.2, 0.1], [True, True, False, False]) == 1.0

    def test_inverted_separation_is_zero(self) -> None:
        assert _auc([0.1, 0.2, 0.8, 0.9], [True, True, False, False]) == 0.0

    def test_all_tied_is_a_half(self) -> None:
        """A model that assigns the same score to everything discriminates
        nothing, and ties count as half a win each."""
        assert _auc([0.5] * 4, [True, True, False, False]) == 0.5

    def test_a_single_class_is_a_half_not_an_error(self) -> None:
        """cpython-stdlib holds 0 positives, so its fold has nothing to
        discriminate. Returning NaN would silently poison any average it
        entered -- which is how the pooled LORO figure first came to read
        0.017 and be reported as "the model does not generalise"."""
        assert _auc([0.9, 0.1], [False, False]) == 0.5
        assert _auc([0.9, 0.1], [True, True]) == 0.5

    def test_it_is_threshold_free(self) -> None:
        """The property that makes it the right headline for a ranker: shifting
        every score by a constant cannot change the ordering, and a model
        trained at one base rate and scored at another differs by roughly
        that."""
        scores = [0.9, 0.6, 0.4, 0.1]
        truth = [True, True, False, False]
        shifted = [s * 0.1 + 0.45 for s in scores]
        assert _auc(scores, truth) == _auc(shifted, truth)


class TestRepositoryIsRecoverableFromAnItemId:
    @pytest.mark.parametrize(
        "item_id,repo",
        [
            ("stripe-python:007", "stripe-python"),
            ("train-stripe-python:0042", "stripe-python"),
            ("cpython-stdlib:000", "cpython-stdlib"),
            ("train-python-sepaxml:0003", "python-sepaxml"),
        ],
    )
    def test_both_corpora_parse(self, item_id: str, repo: str) -> None:
        assert _repo_of(item_id) == repo


class TestTheFoldsAreHonest:
    @staticmethod
    def _toy() -> tuple[list[dict[str, float]], list[bool], list[str]]:
        """Three repositories where one feature is real and one is repo-shaped.

        ``signal`` carries the label everywhere. ``style`` carries it only
        inside repository a, which is what a house convention looks like.
        """
        rows: list[dict[str, float]] = []
        labels: list[bool] = []
        repos: list[str] = []
        for repo in ("a", "b", "c"):
            for i in range(40):
                positive = i % 4 == 0
                rows.append(
                    {
                        "signal": 1.0 if positive else 0.0,
                        "style": (1.0 if positive else 0.0) if repo == "a" else 0.0,
                    }
                )
                labels.append(positive)
                repos.append(repo)
        return rows, labels, repos

    def test_every_repository_is_held_out_once(self) -> None:
        rows, labels, repos = self._toy()
        results = leave_one_repo_out(rows, labels, repos, ("signal", "style"))
        assert set(results) == {"a", "b", "c"}

    def test_a_held_out_repository_is_not_in_its_own_training_set(self) -> None:
        """The whole point. If this failed the measurement would be the
        stratified one wearing a different name."""
        rows, labels, repos = self._toy()
        results = leave_one_repo_out(rows, labels, repos, ("signal",))
        for repo, measured in results.items():
            assert measured["n"] == repos.count(repo)

    def test_a_transferable_feature_scores(self) -> None:
        rows, labels, repos = self._toy()
        results = leave_one_repo_out(rows, labels, repos, ("signal",))
        assert _macro(results, "auc") > 0.9

    def test_a_repository_shaped_feature_does_not(self) -> None:
        """`style` predicts the label perfectly inside repository a and not at
        all elsewhere. A stratified split would reward it; this must not."""
        rows, labels, repos = self._toy()
        results = leave_one_repo_out(rows, labels, repos, ("style",))
        assert _macro(results, "auc") < 0.7

    def test_a_fold_with_too_few_positives_is_marked_unscoreable(self) -> None:
        rows, labels, repos = self._toy()
        # Make repository c almost purely negative, as cpython-stdlib is.
        for i, repo in enumerate(repos):
            if repo == "c":
                labels[i] = False
        results = leave_one_repo_out(rows, labels, repos, ("signal",))
        assert results["c"]["scoreable"] == 0.0
        assert results["a"]["scoreable"] == 1.0

    def test_the_macro_average_excludes_unscoreable_folds(self) -> None:
        """An unscoreable fold contributes kappa 0.000 by construction. Letting
        it into the average is how a real result got reported as 0.017."""
        results = {
            "good": {"auc": 0.80, "kappa": 0.50, "scoreable": 1.0},
            "empty": {"auc": 0.50, "kappa": 0.00, "scoreable": 0.0},
        }
        assert _macro(results, "auc") == 0.80

    def test_no_scoreable_fold_gives_zero_rather_than_an_error(self) -> None:
        results = {"empty": {"auc": 0.5, "kappa": 0.0, "scoreable": 0.0}}
        assert _macro(results, "auc") == 0.0

    def test_the_positives_floor_is_documented_and_nonzero(self) -> None:
        assert MIN_POSITIVES >= 5
