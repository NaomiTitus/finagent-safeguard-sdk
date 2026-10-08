"""Tests for the agreement gate.

The gate decides whether the whole category plan proceeds, so the arithmetic
has to be right. The first test is a published worked example, not one I
invented -- a tool that only agrees with itself proves nothing.
"""

from __future__ import annotations

import pytest

from tools.kappa import (
    GATE,
    band,
    cohen_kappa,
    confusions,
    per_label_agreement,
    report,
)


class TestAgainstAPublishedExample:
    def test_the_classic_two_by_two(self) -> None:
        """The standard textbook case: 50 items, two raters, yes/no.

        both yes 20, both no 15, A yes B no 5, A no B yes 10.
          raw      = 35/50                     = 0.70
          expected = .5*.6 + .5*.4             = 0.50
          kappa    = (.70-.50)/(1-.50)         = 0.40
        """
        a = ["y"] * 20 + ["n"] * 15 + ["y"] * 5 + ["n"] * 10
        b = ["y"] * 20 + ["n"] * 15 + ["n"] * 5 + ["y"] * 10

        kappa, observed, expected = cohen_kappa(a, b)
        assert observed == pytest.approx(0.70)
        assert expected == pytest.approx(0.50)
        assert kappa == pytest.approx(0.40)


class TestTheChanceCorrectionActuallyBites:
    def test_high_raw_agreement_can_still_be_worthless(self) -> None:
        """The reason kappa exists, as a test.

        Both raters call 95 of 100 items PAYMENT. They agree on 90 -- raw 0.90,
        which reads as excellent. Chance agreement is nearly as high, so the
        earned agreement is poor. If this inverted, the gate would wave through
        a taxonomy nobody can actually apply.
        """
        a = ["PAYMENT"] * 95 + ["PII"] * 5
        b = ["PAYMENT"] * 90 + ["PII"] * 5 + ["PAYMENT"] * 5
        kappa, observed, _ = cohen_kappa(a, b)

        assert observed == pytest.approx(0.90)
        assert kappa < observed / 2
        assert kappa < GATE

    def test_perfect_agreement_is_one(self) -> None:
        a = ["PAYMENT", "PII", "AML", "DORA"]
        assert cohen_kappa(a, list(a))[0] == pytest.approx(1.0)

    def test_total_disagreement_goes_negative(self) -> None:
        a = ["PAYMENT", "PII"] * 10
        b = ["PII", "PAYMENT"] * 10
        assert cohen_kappa(a, b)[0] < 0

    def test_one_label_for_everything_scores_zero_not_one(self) -> None:
        """Both raters label every item PAYMENT. Raw agreement is 1.0 and the
        formula divides by zero. Returning 1.0 here would report flawless
        agreement for raters who made no distinctions at all -- the exact
        false pass this gate exists to prevent."""
        a = b = ["PAYMENT"] * 20
        kappa, observed, expected = cohen_kappa(a, b)
        assert observed == 1.0
        assert expected == 1.0
        assert kappa == 0.0


class TestItRefusesBadInput:
    def test_mismatched_lengths_raise(self) -> None:
        """Truncating would shift every later pairing and corrupt the result
        with no visible symptom."""
        with pytest.raises(ValueError, match="rater A has 3"):
            cohen_kappa(["a", "b", "c"], ["a", "b"])

    def test_empty_input_raises(self) -> None:
        with pytest.raises(ValueError, match="no labels"):
            cohen_kappa([], [])


class TestItSaysWhereTheProblemIs:
    def test_the_recurring_disagreement_is_surfaced(self) -> None:
        """A single kappa hides the actionable detail. If raters separate
        payments cleanly but collide on AML every time, the fix is the AML
        definition -- which only the confusion list reveals."""
        a = ["PAYMENT"] * 8 + ["AML"] * 6
        b = ["PAYMENT"] * 8 + ["PAYMENT"] * 6
        top = confusions(a, b)[0]
        assert top == ("AML", "PAYMENT", 6)

    def test_per_label_separates_the_good_from_the_bad(self) -> None:
        a = ["PAYMENT"] * 8 + ["AML"] * 6
        b = ["PAYMENT"] * 8 + ["PAYMENT"] * 6
        got = per_label_agreement(a, b)
        assert got["PAYMENT"] == (8, 14)
        assert got["AML"] == (0, 6)


class TestTheGateIsReportedHonestly:
    def test_a_wide_interval_fails_even_when_the_estimate_passes(self) -> None:
        """With few items the interval is wide. An estimate above 0.40 whose
        interval reaches below it has not established anything, and the report
        must say so rather than printing a reassuring number."""
        a = ["PAYMENT", "PII", "AML", "PAYMENT", "PII", "AML", "PAYMENT", "PII"]
        b = ["PAYMENT", "PII", "PAYMENT", "PAYMENT", "AML", "AML", "PAYMENT", "PII"]
        kappa, _, _ = cohen_kappa(a, b)
        text = report(a, b)
        assert kappa > GATE
        assert "FAIL" in text
        assert "interval does not" in text

    def test_bands_are_labelled(self) -> None:
        assert band(0.95) == "near-perfect"
        assert band(0.72) == "substantial"
        assert band(0.45) == "moderate"
        assert band(-0.1) == "worse than chance"
