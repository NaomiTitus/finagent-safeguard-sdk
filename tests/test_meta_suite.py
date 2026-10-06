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


class TestCollectionSizesArePinned:
    """Layer 1b, mechanised.

    Many tests loop over a registry collection and assert per item; each would
    pass on an empty collection, which mutation R12 demonstrated. These pin the
    sizes so a loop cannot quietly shrink to nothing.

    Exact counts, not floors. Floors below the true size let items be deleted
    with the guard still green -- `reference_points >= 4` passed with five
    present, and `provisions >= 18` sat next to an assertion of exactly 23.

    Note what this is and is not: it is a test, not a structural property. An
    earlier docstring claimed it made the collections "incapable of being
    empty". Nothing in registry.py validates non-emptiness and Registry accepts
    empty tuples; the guard lives here.
    """

    def test_instruments(self) -> None:
        assert len(REGISTRY.instruments()) == 7

    def test_obligations(self) -> None:
        assert len(REGISTRY.obligations()) == 12

    def test_exemptions(self) -> None:
        assert len(REGISTRY.exemptions()) == 4

    def test_reference_points(self) -> None:
        assert len(REGISTRY.reference_points()) == 4

    def test_application_dates(self) -> None:
        assert len(REGISTRY.application_dates()) == 2

    def test_provisions(self) -> None:
        assert len(list(REGISTRY.provisions())) == 23

    def test_numeric_parameters(self) -> None:
        assert len(list(REGISTRY.numeric_parameters())) == 12


def _test_files() -> list[Path]:
    """Every file pytest collects, not just the one naming convention.

    Default collection is `test_*.py` AND `*_test.py`; an earlier version
    globbed only the first, so a whole file could opt out of these lints by
    being named the other way.
    """
    return sorted(set(TESTS.rglob("test_*.py")) | set(TESTS.rglob("*_test.py")))


def _test_functions() -> list[tuple[str, str, str, ast.AST]]:
    """(file, name, source, node) for every test pytest would collect.

    Matches pytest's default `python_functions = test*`, and includes
    `AsyncFunctionDef`. An earlier version required `def` and a `test_` prefix,
    so `async def test_x` and `def testX` were both invisible while pytest
    collected them -- three ordinary shapes opting out of the lint silently.
    """
    out: list[tuple[str, str, str, ast.AST]] = []
    for path in _test_files():
        src = path.read_text()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                node.name.startswith("test")
            ):
                out.append(
                    (path.name, node.name, ast.get_source_segment(src, node) or "", node)
                )
    return out


def _asserts_something(node: ast.AST) -> bool:
    """A real assertion, not the characters "assert" in a docstring.

    The substring check this replaces was satisfiable by prose -- and one test
    in this repo had "assert" in its docstring, so deleting its only real
    assertion would have left the lint green.
    """
    for child in ast.walk(node):
        if isinstance(child, ast.Assert):
            return True
        if isinstance(child, ast.Attribute) and child.attr in {"raises", "fail", "warns"}:
            return True
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Attribute) and func.attr in {"raises", "fail"}:
                return True
    return False


class TestNameMatchesAssertion:
    """Layer 1c, partly mechanised.

    A name that promises more than its body delivers is how a suite looks
    thorough while checking something adjacent and easier.
    """

    #: name fragment -> what the body must contain
    #: Widened from six fragments to cover the shapes that actually occur.
    #: "rejected" did not match "rejects"; nothing covered fails, invalid,
    #: forbids, must_, or no_.
    NEGATIVE = (
        "raise", "reject", "refus", "forbid", "block", "fail", "invalid",
        "_error", "cannot", "must_", "does_not",
    )
    #: "no_" was tried and dropped: it matched `makes_no_network_call`, which
    #: promises an absence, not a rejection. A fragment that cannot distinguish
    #: those two produces false positives, and a lint that cries wolf gets
    #: deleted rather than fixed.

    def test_names_promising_a_failure_assert_one(self) -> None:
        offenders = []
        for file, name, body, node in _test_functions():
            if not any(frag in name for frag in self.NEGATIVE):
                continue
            has_raises = any(
                isinstance(c, ast.Attribute) and c.attr == "raises"
                for c in ast.walk(node)
            )
            has_negative = any(
                isinstance(c, ast.Assert)
                and isinstance(c.test, (ast.UnaryOp, ast.Compare))
                for c in ast.walk(node)
            )
            if not (has_raises or has_negative):
                offenders.append(f"{file}::{name}")
        assert not offenders, (
            "these names promise a rejection but assert none: " + ", ".join(offenders)
        )

    def test_every_test_asserts_something(self) -> None:
        """A test with no assertion and no expected exception checks nothing."""
        offenders = [
            f"{file}::{name}"
            for file, name, _body, node in _test_functions()
            if not _asserts_something(node)
        ]
        assert not offenders, "tests with no assertion: " + ", ".join(offenders)

    def test_no_test_is_conditionally_skipped_without_asserting(self) -> None:
        """F-003: `if hasattr(...)` around a whole body turns a missing target
        into a silent pass. Guard the guard."""
        offenders = []
        for file, name, body, _node in _test_functions():
            # Anchored at four or more, not exactly eight. The original was pinned
            # to the one instance it was written against -- a class method's body
            # sits at eight, a module-level test function's at four, so the latter
            # walked straight past.
            for _m in re.finditer(r"^\s{4,}if (hasattr|getattr)\(", body, re.M):
                if "assert hasattr" not in body:
                    offenders.append(f"{file}::{name}")
                break
        assert not offenders, (
            "conditional guards that can silently pass: " + ", ".join(offenders)
        )


class TestWorkflowIsCredentialFree:
    """The workflow comment asserts this job references no secrets. Make it true.

    An earlier version of that comment claimed "a test asserts that this job
    references no secrets, which turns 'runs on a fresh clone' from a promise
    into a structural fact". There was no such test. A comment asserting an
    enforcement that does not exist is worse than no comment, because the next
    reviewer trusts it.
    """

    WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"

    def _job(self, name: str) -> str:
        text = self.WORKFLOW.read_text()
        start = text.index(f"\n  {name}:")
        rest = text[start + 1 :]
        end = re.search(r"\n  \w[\w-]*:\n", rest)
        return rest[: end.start()] if end else rest

    def test_the_fast_job_references_no_secrets(self) -> None:
        body = self._job("fast")
        assert "secrets." not in body, (
            "the fast job must run with no credentials; that is what makes "
            "'passes on a fresh clone' a structural fact rather than a promise"
        )

    def test_the_mutation_job_references_no_secrets(self) -> None:
        assert "secrets." not in self._job("mutation")

    def test_the_staleness_job_is_reachable(self) -> None:
        """It is gated on `schedule`; assert the trigger exists.

        Without this the job is permanently skipped and the one check meant to
        fire with no human fires never.
        """
        header = self.WORKFLOW.read_text().split("jobs:")[0]
        assert "schedule:" in header
        assert "workflow_dispatch:" in header, (
            "without a manual trigger the drift alarm cannot be proven to run, "
            "only assumed to"
        )
        job = self._job("staleness")
        assert "schedule" in job
        assert "workflow_dispatch" in job


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
