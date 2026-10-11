"""The browser demo must not claim behaviour the linter does not have.

A demo is documentation that runs, which makes it the most load-bearing prose
in the repository: a visitor who pastes code and sees the wrong thing has been
told something false by the tool itself.

Two of the five examples were mislabelled when first written. "the hard pair"
was presented as a linter success and produces **no findings at all** -- both
methods take only `self`, so nothing matches. "a test double" was captioned
*validating is not producing*, implying it would not flag, and it flags: the
money vocabulary contains "charge" and the function is called
`test_charge_is_rejected`. Both now say what they do, and the second is
labelled a false positive, which is the more useful thing to show.

These tests read the examples out of `docs/demo.js` rather than duplicating
them, because a copy would be the same drift one layer down.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from finagent_safeguard.cli.linter import ADVISORY_SIGNALS, scan_source

DOCS = Path(__file__).resolve().parents[1] / "docs"
DEMO_JS = DOCS / "demo.js"
BUNDLE = DOCS / "demo-bundle.json"


def _examples() -> list[tuple[str, str, str]]:
    """(name, why, code) for each example in the demo's source."""
    found = re.findall(
        r"name: '([^']+)',\n\s+why: '([^']*)',\n\s+code: `(.*?)`,",
        DEMO_JS.read_text(),
        re.S,
    )
    assert found, "no examples parsed out of docs/demo.js; has its shape changed?"
    return found


def _exit_code(code: str) -> int:
    findings = scan_source(code, Path("demo.py"))
    counted = [f for f in findings if f.signal not in ADVISORY_SIGNALS]
    return 1 if counted else 0


class TestTheExamplesAreHonest:
    @pytest.mark.parametrize("name,why,code", _examples())
    def test_an_example_claiming_exit_zero_gets_exit_zero(
        self, name: str, why: str, code: str
    ) -> None:
        if "xit 0" not in why:
            pytest.skip("makes no claim about the exit status")
        assert _exit_code(code) == 0, f"{name}: caption claims exit 0"

    @pytest.mark.parametrize("name,why,code", _examples())
    def test_an_example_claiming_a_flag_is_flagged(
        self, name: str, why: str, code: str
    ) -> None:
        if "Flagged" not in why:
            pytest.skip("makes no claim about being flagged")
        assert _exit_code(code) == 1, f"{name}: caption claims it is flagged"

    @pytest.mark.parametrize("name,why,code", _examples())
    def test_every_example_parses(self, name: str, why: str, code: str) -> None:
        """An example that does not parse would show exit 2 and read as a bug
        in the tool rather than in the example.

        Asserted on the parsed tree rather than relying on `ast.parse` raising.
        The vacuity layer caught the first version of this, correctly: a test
        whose only mechanism is an exception it does not name reads as a test
        of something when it is a test of nothing in particular.
        """
        import ast

        tree = ast.parse(code)
        assert tree.body, f"{name}: parses, but defines nothing"

    def test_at_least_one_example_finds_nothing(self) -> None:
        """A demo where everything flags teaches that the tool flags
        everything, which is the complaint it most needs to answer."""
        assert any(_exit_code(code) == 0 for _, _, code in _examples())

    def test_at_least_one_example_shows_a_false_positive(self) -> None:
        """Precision is the tool's weak point and the README says so. A demo
        that hides it is advertising, not documentation."""
        assert any("false positive" in name for name, _, _ in _examples())


class TestTheBundleIsUsable:
    def test_the_bundle_exists_and_is_committed(self) -> None:
        assert BUNDLE.exists(), "run tools/build_demo.py"

    # Freshness is NOT checked here, deliberately. It is checked by
    # `tools/build_demo.py --check`, which the battery runs as layer 1e and CI
    # runs in the fast job.
    #
    # The first version of this file did assert it as a test, and layer 1a
    # immediately reported mutation L21 as newly caught. That was false: a test
    # comparing the bundle against the package detects **any** edit to a
    # bundled module, so it would "catch" every mutation in linter.py,
    # candidates.py, registry.py, policies.py, decorators.py and citation.py --
    # most of the package -- and report the mutation protocol as satisfied
    # while testing nothing about the behaviour mutated.
    #
    # A test that passes for the wrong reason is the failure mode this project
    # exists to catch, and here it would have hidden the others. The check
    # belongs in a tool the suite does not run.

    def test_the_bundle_carries_every_module_the_demo_imports(self) -> None:
        bundle = json.loads(BUNDLE.read_text())
        for needed in (
            "finagent_safeguard/cli/linter.py",
            "finagent_safeguard/regulation/candidates.py",
            "finagent_safeguard/taxonomy/policies.py",
        ):
            assert needed in bundle, needed

    def test_the_demo_names_only_functions_the_package_exports(self) -> None:
        """The demo calls into the package by name from JavaScript, where a
        rename is not a type error. This is the only thing that would catch
        `_render_annotation` being renamed."""
        from finagent_safeguard.cli import linter

        js = DEMO_JS.read_text()
        for symbol in re.findall(r"\b(scan_source|_render_annotation|ADVISORY_SIGNALS|UnparseableSource)\b", js):
            assert hasattr(linter, symbol), f"demo.js calls missing {symbol}"
