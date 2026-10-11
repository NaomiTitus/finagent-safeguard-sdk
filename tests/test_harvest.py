"""Tests for the labelling corpus.

The corpus is the evaluation set for the classifier, so the properties worth
guarding are about what it must *not* be: derived from the linter's own
verdicts, unreproducible, or carrying third-party code into this repository.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
POINTERS = ROOT / "benchmark/pointers.jsonl"


def _pointers() -> list[dict[str, object]]:
    if not POINTERS.exists():
        pytest.skip("corpus not harvested in this checkout")
    return [
        json.loads(line) for line in POINTERS.read_text().splitlines() if line.strip()
    ]


class TestNothingIsVendored:
    def test_pointers_carry_no_source(self) -> None:
        """What is committed is where the code lives, never the code. No
        third-party source enters this repository, so no licence obligation is
        triggered and the public history stays clean."""
        for pointer in _pointers():
            assert set(pointer) == {
                "item_id",
                "repo",
                "commit",
                "path",
                "first_line",
                "last_line",
                "function",
                "sha256",
            }

    def test_the_worksheet_is_not_committed(self) -> None:
        """It holds the function bodies, which is the whole reason it is
        regenerated locally rather than checked in."""
        import subprocess

        done = subprocess.run(
            ["git", "check-ignore", "benchmark/worksheet.md"],
            cwd=ROOT,
            capture_output=True,
        )
        assert done.returncode == 0, "benchmark/worksheet.md must be gitignored"


class TestTheSampleIsReproducible:
    def test_every_item_has_a_commit_and_a_content_hash(self) -> None:
        """Without the hash a label silently becomes a label of whatever sits
        at those line numbers now."""
        for pointer in _pointers():
            assert pointer["commit"] != "n/a" or pointer["repo"] == "cpython-stdlib"
            assert len(str(pointer["sha256"])) == 16

    def test_item_ids_are_unique(self) -> None:
        ids = [p["item_id"] for p in _pointers()]
        assert len(ids) == len(set(ids))

    def test_the_seed_is_fixed(self) -> None:
        """A re-draw that produced a different sample would quietly invalidate
        every label already assigned."""
        from tools.harvest_corpus import SEED

        assert isinstance(SEED, int)


class TestTheSampleCanActuallyDiscriminate:
    def test_both_classes_are_represented(self) -> None:
        """A sample the rule fires on at roughly 0% or 100% cannot distinguish
        two methods, however many items it has."""
        from collections import Counter

        repos = Counter(str(p["repo"]) for p in _pointers())
        assert len(repos) >= 4, repos
        # The standard library is the negative class: it contains no financial
        # code, which is why the linter's 51 flags across 155 of its top-level
        # modules were all wrong.
        assert repos["cpython-stdlib"] >= 20
        payment = sum(n for r, n in repos.items() if r != "cpython-stdlib")
        assert payment >= 80, repos

    def test_no_single_repository_dominates(self) -> None:
        """stripe-python alone has over 1,400 files, most of them generated API
        stubs. A sample it dominated would measure boilerplate."""
        from collections import Counter

        repos = Counter(str(p["repo"]) for p in _pointers())
        total = sum(repos.values())
        assert max(repos.values()) / total < 0.4, repos

    def test_trivial_functions_are_excluded(self) -> None:
        """A one-line stub carries no judgement to make, and a corpus padded
        with them reports a high score on a task nobody asked about."""
        for pointer in _pointers():
            span = int(pointer["last_line"]) - int(pointer["first_line"])
            assert span >= 1, pointer["item_id"]


class TestTheTrainingCorpusIsSeparate:
    """Overlap between the training and evaluation sets is the one mistake that
    would make every number in the project meaningless, so it is asserted twice
    -- by identity and by location -- rather than trusted.
    """

    @staticmethod
    def _train() -> list[dict[str, object]]:
        path = ROOT / "benchmark/train-pointers.jsonl"
        if not path.exists():
            pytest.skip("training corpus not drawn in this checkout")
        return [
            json.loads(line) for line in path.read_text().splitlines() if line.strip()
        ]

    def test_no_item_id_is_shared(self) -> None:
        train = {str(p["item_id"]) for p in self._train()}
        evaluation = {str(p["item_id"]) for p in _pointers()}
        assert not (train & evaluation)

    def test_no_function_is_shared(self) -> None:
        """Ids could differ while pointing at the same function. This is the
        check that actually matters: a classifier tested on a function it was
        trained on is measuring its own memory."""
        def where(p: dict[str, object]) -> tuple[str, str, int]:
            return (str(p["repo"]), str(p["path"]), int(p["first_line"]))

        train = {where(p) for p in self._train()}
        evaluation = {where(p) for p in _pointers()}
        shared = train & evaluation
        assert not shared, f"{len(shared)} function(s) in both sets: {sorted(shared)[:5]}"

    def test_the_training_ids_are_prefixed(self) -> None:
        """So a row cannot be mistaken for an evaluation row if the two files
        are ever concatenated by accident -- which is how the first training
        run came to overwrite the evaluation judgements."""
        for pointer in self._train():
            assert str(pointer["item_id"]).startswith("train-"), pointer

    def test_it_is_large_enough_to_train_on(self) -> None:
        """At roughly 22% positive, a thousand items gives about 220 positives,
        and at ten events per feature that is the difference between a twenty
        feature budget and a four feature one."""
        assert len(self._train()) >= 900

    def test_the_class_balance_matches_the_evaluation_set(self) -> None:
        """Training on a different distribution from the test set measures the
        shift rather than the model. The evaluation set is 21.9% positive."""
        path = ROOT / "benchmark/train-labels.jsonl"
        if not path.exists():
            pytest.skip("training labels not generated in this checkout")
        votes: dict[str, list[bool]] = {}
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("regulated") is None:
                continue
            votes.setdefault(str(row["item_id"]), []).append(bool(row["regulated"]))
        mode = [sum(v) * 2 > len(v) for v in votes.values()]
        rate = sum(mode) / len(mode)
        assert 0.15 <= rate <= 0.30, f"training positive rate {rate:.1%}"

    def test_every_training_item_was_labelled(self) -> None:
        path = ROOT / "benchmark/train-labels.jsonl"
        if not path.exists():
            pytest.skip("training labels not generated in this checkout")
        labelled = {
            str(json.loads(line)["item_id"])
            for line in path.read_text().splitlines()
            if line.strip()
        }
        assert {str(p["item_id"]) for p in self._train()} <= labelled
