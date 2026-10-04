"""Suite-level guards. These make two of the three Layer 1 review techniques
enforced properties rather than habits someone has to remember.

See docs/review-protocol.md. Layer 1c (name-assertion match) is only partly
mechanisable; the convention lint below catches crude mismatches and the
semantic version stays a human job.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from finagent_safeguard.regulation.registry import REGISTRY

TESTS = Path(__file__).parent


class TestCollectionsAreNonEmpty:
    """Layer 1b, mechanised.

    Twenty-one tests loop over a registry collection and assert per item. Each
    would pass on an empty collection -- mutation R12 proved it. Rather than
    add a count guard to twenty-one tests, make the collections themselves
    incapable of being empty: if they cannot empty, no loop over them is vacuous.
    """

    def test_instruments(self) -> None:
        assert len(REGISTRY.instruments()) >= 7

    def test_obligations(self) -> None:
        assert len(REGISTRY.obligations()) >= 11

    def test_exemptions(self) -> None:
        assert len(REGISTRY.exemptions()) >= 4

    def test_reference_points(self) -> None:
        assert len(REGISTRY.reference_points()) >= 4

    def test_application_dates(self) -> None:
        assert len(REGISTRY.application_dates()) >= 2

    def test_provisions(self) -> None:
        assert len(list(REGISTRY.provisions())) >= 18

    def test_numeric_parameters(self) -> None:
        assert len(list(REGISTRY.numeric_parameters())) >= 11


def _test_functions() -> list[tuple[str, str, str]]:
    """(file, test name, source) for every test in the suite."""
    out: list[tuple[str, str, str]] = []
    for path in sorted(TESTS.rglob("test_*.py")):
        src = path.read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                out.append((path.name, node.name, ast.get_source_segment(src, node) or ""))
    return out


class TestNameMatchesAssertion:
    """Layer 1c, partly mechanised.

    A name that promises more than its body delivers is how a suite looks
    thorough while checking something adjacent and easier.
    """

    #: name fragment -> what the body must contain
    NEGATIVE = ("raises", "rejected", "_refus", "is_not_", "cannot_", "does_not_satisfy")

    def test_names_promising_a_failure_assert_one(self) -> None:
        offenders = []
        for file, name, body in _test_functions():
            if not any(frag in name for frag in self.NEGATIVE):
                continue
            if "pytest.raises" not in body and "assert not" not in body:
                offenders.append(f"{file}::{name}")
        assert not offenders, (
            "these names promise a rejection but assert none: " + ", ".join(offenders)
        )

    def test_every_test_asserts_something(self) -> None:
        """A test with no assertion and no expected exception checks nothing."""
        offenders = []
        for file, name, body in _test_functions():
            if "assert" in body or "pytest.raises" in body or "pytest.fail" in body:
                continue
            offenders.append(f"{file}::{name}")
        assert not offenders, "tests with no assertion: " + ", ".join(offenders)

    def test_no_test_is_conditionally_skipped_without_asserting(self) -> None:
        """F-003: `if hasattr(...)` around a whole body turns a missing target
        into a silent pass. Guard the guard."""
        offenders = []
        for file, name, body in _test_functions():
            for m in re.finditer(r"^\s{8}if (hasattr|getattr)\(", body, re.M):
                if "assert hasattr" not in body:
                    offenders.append(f"{file}::{name}")
                break
        assert not offenders, (
            "conditional guards that can silently pass: " + ", ".join(offenders)
        )


class TestReviewPacketsAreSelfServe:
    """The review process must not depend on the author's choices.

    Two levers were removed: which materials a cold reviewer sees, and what
    criterion it judges against. These tests keep them removed.
    """

    def test_every_criterion_is_substantive(self) -> None:
        import tomllib

        path = Path(__file__).resolve().parents[1] / "docs" / "review-criteria.toml"
        with path.open("rb") as fh:
            criteria = tomllib.load(fh)["criteria"]
        assert criteria, "no committed criteria"
        for branch, text in criteria.items():
            assert len(text.split()) >= 12, (
                f"{branch}: a criterion of {len(text.split())} words is a label, "
                "not a test of the work"
            )

    def test_a_branch_without_a_criterion_cannot_be_reviewed(self) -> None:
        """An author who writes the criterion at review time decides what
        passing means."""
        import importlib.util

        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            "prep", root / "tools" / "prepare_review.py"
        )
        assert spec and spec.loader
        prep = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prep)
        with pytest.raises(SystemExit, match="No committed criterion"):
            prep.criterion_for("some/branch-nobody-wrote-a-criterion-for")

    def test_the_packet_cannot_carry_the_author_s_reasoning(self) -> None:
        """docs/ holds the plan, the drafts and every prior review. A cold
        reviewer that reads them inherits the framing it exists to do without."""
        import importlib.util

        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            "prep", root / "tools" / "prepare_review.py"
        )
        assert spec and spec.loader
        prep = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prep)
        assert not any(p.startswith("docs") for p in prep.CODE_PATHS)
        assert any("docs" in rule for rule in prep.EXCLUSIONS), (
            "the exclusion must be stated in the manifest, not merely implied"
        )
