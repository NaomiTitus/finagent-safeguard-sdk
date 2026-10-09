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
