"""Tests for the frozen labels and the statistics over them.

The labels are the evaluation set. Once sealed they must not move, and the
numbers quoted about them must come from a command rather than from a figure
someone wrote down -- two figures in this repository have already needed
correcting for being hand-copied.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.label_stats import band, cohen_kappa

ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "benchmark/labels.jsonl"
POINTERS = ROOT / "benchmark/pointers.jsonl"
DIGEST = ROOT / "benchmark/labels.sha256"


def _rows() -> list[dict[str, object]]:
    if not LABELS.exists():
        pytest.skip("labels not present in this checkout")
    return [
        json.loads(line) for line in LABELS.read_text().splitlines() if line.strip()
    ]


class TestTheLabelsAreSealed:
    def test_the_digest_matches(self) -> None:
        """The seal is the whole point. A classifier tuned against labels that
        moved after it was written is measuring itself."""
        actual = hashlib.sha256(LABELS.read_bytes()).hexdigest()
        assert actual == DIGEST.read_text().split()[0]

    def test_every_pointer_has_a_first_pass_label(self) -> None:
        pointers = {
            json.loads(line)["item_id"]
            for line in POINTERS.read_text().splitlines()
            if line.strip()
        }
        labelled = {r["item_id"] for r in _rows() if r.get("pass") != 2}
        assert labelled == pointers

    def test_no_label_is_for_an_unknown_item(self) -> None:
        pointers = {
            json.loads(line)["item_id"]
            for line in POINTERS.read_text().splitlines()
            if line.strip()
        }
        for row in _rows():
            assert row["item_id"] in pointers, row

    def test_the_second_pass_is_a_subset_of_the_first(self) -> None:
        """A re-check of an item never labelled would have nothing to compare
        against, and would silently shrink the ceiling's sample."""
        first = {r["item_id"] for r in _rows() if r.get("pass") != 2}
        second = {r["item_id"] for r in _rows() if r.get("pass") == 2}
        assert second <= first
        assert len(second) >= 20, "too few re-checks to establish a ceiling"

    def test_both_classes_are_present(self) -> None:
        """A single-class evaluation set cannot measure anything."""
        values = [bool(r["regulated"]) for r in _rows() if r.get("pass") != 2]
        assert 0 < sum(values) < len(values)


class TestTheCeilingIsReal:
    def test_it_clears_the_gate(self) -> None:
        """Below 0.40 the question is not reliably answerable and no classifier
        should be scored against these labels. Asserted so that re-labelling
        which degraded the ceiling would fail the build rather than quietly
        producing a flattering score."""
        rows = _rows()
        first = {
            str(r["item_id"]): bool(r["regulated"])
            for r in rows
            if r.get("pass") != 2
        }
        second = {
            str(r["item_id"]): bool(r["regulated"]) for r in rows if r.get("pass") == 2
        }
        pairs = [(first[k], second[k]) for k in second]
        kappa, _, _ = cohen_kappa(pairs)
        assert kappa >= 0.40, f"agreement ceiling collapsed to {kappa:.3f}"


class TestTheKappaArithmetic:
    def test_the_classic_worked_example(self) -> None:
        """50 items: both yes 20, both no 15, split 5 and 10.
        raw 0.70, expected 0.50, kappa 0.40."""
        pairs = (
            [(True, True)] * 20
            + [(False, False)] * 15
            + [(True, False)] * 5
            + [(False, True)] * 10
        )
        kappa, observed, expected = cohen_kappa(pairs)
        assert observed == pytest.approx(0.70)
        assert expected == pytest.approx(0.50)
        assert kappa == pytest.approx(0.40)

    def test_perfect_agreement_on_a_mixed_set_is_one(self) -> None:
        pairs = [(True, True)] * 10 + [(False, False)] * 10
        assert cohen_kappa(pairs)[0] == pytest.approx(1.0)

    def test_one_answer_for_everything_scores_zero_not_one(self) -> None:
        """Both passes said no to all of it. Agreement is total and so is
        chance agreement, so nothing was demonstrated -- returning 1.0 here
        would report a flawless ceiling for an annotator who made no
        distinctions."""
        kappa, observed, expected = cohen_kappa([(False, False)] * 20)
        assert observed == 1.0
        assert expected == 1.0
        assert kappa == 0.0

    def test_high_raw_agreement_can_still_be_poor(self) -> None:
        """Why the chance correction is reported at all."""
        pairs = [(True, True)] * 18 + [(True, False)] * 1 + [(False, True)] * 1
        kappa, observed, _ = cohen_kappa(pairs)
        assert observed == pytest.approx(0.90)
        assert kappa < 0.40

    def test_empty_input_is_zero_not_an_error(self) -> None:
        assert cohen_kappa([]) == (0.0, 0.0, 0.0)

    def test_bands_are_labelled(self) -> None:
        assert band(0.95) == "near-perfect"
        assert band(0.762) == "substantial"
        assert band(0.45) == "moderate"
        assert band(-0.2) == "worse than chance"
