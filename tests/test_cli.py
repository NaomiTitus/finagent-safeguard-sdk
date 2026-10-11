"""Tests for the `finagent-lint` command line.

Exit status is the whole interface as far as CI is concerned, so most of these
assert a number. The 1/2 distinction carries the weight: 1 means the tool
worked and found something, 2 means the tool did not work. A pipeline that
treats those the same will one day stop gating and not notice.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from finagent_safeguard.cli.linter import ADVISORY_SIGNALS, main, unresolved_functions

MONEY = (
    "from decimal import Decimal\n\n\n"
    "def execute_credit_transfer(debtor_iban: str, amount: Decimal) -> None:\n"
    "    pass\n"
)
CLEAN = "def greet(greeting: str) -> str:\n    return greeting\n"


def _write(root: Path, name: str, body: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


class TestExitStatus:
    def test_a_clean_tree_exits_zero(self, tmp_path: Path) -> None:
        _write(tmp_path, "clean.py", CLEAN)
        assert main([str(tmp_path)]) == 0

    def test_a_finding_exits_one(self, tmp_path: Path) -> None:
        _write(tmp_path, "pay.py", MONEY)
        assert main([str(tmp_path)]) == 1

    def test_a_missing_path_exits_two(self, tmp_path: Path) -> None:
        """Not zero. A linter that exits 0 because it scanned nothing is worse
        than one that fails: the typo silently disables the gate."""
        assert main([str(tmp_path / "nope.py")]) == 2

    def test_an_unparseable_file_exits_two(self, tmp_path: Path) -> None:
        """`scan_source` raises UnparseableSource, which is not a SyntaxError.
        Catching only the latter let it escape and crash the process, and
        Python exits 1 on an uncaught exception -- so a crashed run looked
        exactly like a successful run that found something."""
        _write(tmp_path, "bad.py", "def broken(:\n")
        assert main([str(tmp_path)]) == 2

    def test_one_unparseable_file_fails_the_whole_run(self, tmp_path: Path) -> None:
        """A file the tool could not read is not a clean file."""
        _write(tmp_path, "clean.py", CLEAN)
        _write(tmp_path, "bad.py", "def broken(:\n")
        assert main([str(tmp_path)]) == 2

    def test_fix_still_exits_one(self, tmp_path: Path) -> None:
        """Every flag --fix inserts is unresolved by construction, so the
        developer still has work to do. Going green on the strength of having
        written something would defeat the point."""
        _write(tmp_path, "pay.py", MONEY)
        assert main([str(tmp_path), "--fix"]) == 1


class TestDiffOnlyWritesNothing:
    def test_the_file_is_untouched(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "pay.py", MONEY)
        before = path.read_bytes()
        main([str(path), "--diff-only"])
        assert path.read_bytes() == before

    def test_it_still_reports_the_finding(self, tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
        path = _write(tmp_path, "pay.py", MONEY)
        main([str(path), "--diff-only"])
        out = capsys.readouterr().out
        assert "execute_credit_transfer" in out
        assert "@regulated_tool" in out


class TestApprovingTheExactEdit:
    """The two-invocation workflow: read a diff, then authorise that diff.

    Exit status matters as much as the refusal. A `--fix` that refuses because
    the approval is stale must not exit 0, or a pipeline treats "I wrote
    nothing because you approved something else" as success.
    """

    def _digest(self, path: Path, capsys) -> str:  # type: ignore[no-untyped-def]
        main([str(path), "--diff-only"])
        for line in capsys.readouterr().out.splitlines():
            if line.strip().startswith("plan "):
                return line.split()[1]
        raise AssertionError("--diff-only printed no plan digest to approve")

    def test_diff_only_prints_a_digest_to_approve(
        self, tmp_path: Path, capsys  # type: ignore[no-untyped-def]
    ) -> None:
        path = _write(tmp_path, "pay.py", MONEY)
        assert len(self._digest(path, capsys)) == 12

    def test_a_clean_file_offers_no_digest(
        self, tmp_path: Path, capsys  # type: ignore[no-untyped-def]
    ) -> None:
        """There is nothing to approve, so offering a handle for it would
        invite a developer to authorise an empty edit."""
        path = _write(tmp_path, "ok.py", CLEAN)
        main([str(path), "--diff-only"])
        assert "plan " not in capsys.readouterr().out

    def test_the_printed_digest_authorises_the_write(
        self, tmp_path: Path, capsys  # type: ignore[no-untyped-def]
    ) -> None:
        path = _write(tmp_path, "pay.py", MONEY)
        digest = self._digest(path, capsys)
        assert main([str(path), "--fix", "--expect-plan", digest]) == 1
        assert b"REVIEW_REQUIRED" in path.read_bytes()

    def test_a_stale_approval_refuses_and_does_not_exit_zero(
        self, tmp_path: Path, capsys  # type: ignore[no-untyped-def]
    ) -> None:
        path = _write(tmp_path, "pay.py", MONEY)
        digest = self._digest(path, capsys)
        path.write_text(MONEY + "\n\ndef charge_card(pan: str) -> None:\n    pass\n")
        before = path.read_bytes()

        assert main([str(path), "--fix", "--expect-plan", digest]) == 2
        assert path.read_bytes() == before
        err = capsys.readouterr().err
        assert "not the plan" in err

    def test_expect_plan_without_fix_is_a_usage_error(
        self, tmp_path: Path
    ) -> None:
        """Exit 2, not 1: the tool did not work, rather than found something."""
        path = _write(tmp_path, "pay.py", MONEY)
        with pytest.raises(SystemExit) as exc:
            main([str(path), "--expect-plan", "abc123"])
        assert exc.value.code == 2

    def test_a_malformed_digest_is_a_usage_error_not_a_refusal(
        self, tmp_path: Path
    ) -> None:
        """A typo must not read as "the file changed". One is the developer's
        mistake to fix in the shell; the other sends them to re-read a diff."""
        path = _write(tmp_path, "pay.py", MONEY)
        with pytest.raises(SystemExit) as exc:
            main([str(path), "--fix", "--expect-plan", "not-a-digest"])
        assert exc.value.code == 2


class TestUnresolvedFlags:
    SOURCE = (
        "from finagent_safeguard.core.decorators import regulated_tool\n"
        "from finagent_safeguard.taxonomy.policies import FinancialCategory\n\n\n"
        "@regulated_tool(FinancialCategory.REVIEW_REQUIRED)\n"
        "def half_done(amount: float, iban: str) -> None: ...\n\n\n"
        "@regulated_tool(FinancialCategory.PSD2_PAYMENT_EXECUTION)\n"
        "def confirmed(amount: float, iban: str) -> None: ...\n"
    )

    def test_an_unresolved_flag_fails_the_run(self, tmp_path: Path) -> None:
        _write(tmp_path, "m.py", self.SOURCE)
        assert main([str(tmp_path)]) == 1

    def test_only_the_unresolved_one_is_named(self) -> None:
        assert unresolved_functions(self.SOURCE) == ["half_done"]

    def test_a_confirmed_category_is_not_flagged(self) -> None:
        source = (
            "from finagent_safeguard.core.decorators import regulated_tool\n"
            "from finagent_safeguard.taxonomy.policies import FinancialCategory\n\n\n"
            "@regulated_tool(FinancialCategory.PSD2_PAYMENT_EXECUTION)\n"
            "def confirmed(amount: float) -> None: ...\n"
        )
        assert unresolved_functions(source) == []

    def test_nested_and_method_flags_are_found_by_dotted_name(self) -> None:
        """A flag inside a class or a nested def is still unresolved. The
        linter's first cold review found nested functions were invisible to
        detection; the same blind spot here would let a flag hide."""
        source = (
            "from finagent_safeguard.core.decorators import regulated_tool\n"
            "from finagent_safeguard.taxonomy.policies import FinancialCategory\n\n\n"
            "class Ledger:\n"
            "    @regulated_tool(FinancialCategory.REVIEW_REQUIRED)\n"
            "    def post(self, amount: float) -> None: ...\n\n\n"
            "def outer() -> None:\n"
            "    @regulated_tool(FinancialCategory.REVIEW_REQUIRED)\n"
            "    def inner(amount: float) -> None: ...\n"
        )
        assert sorted(unresolved_functions(source)) == ["Ledger.post", "outer.inner"]

    def test_a_decorator_merely_mentioning_the_name_is_not_a_flag(self) -> None:
        """Resolved through the AST, not by searching the text. A substring
        test over source has caused three separate defects in this file."""
        source = (
            "REVIEW_REQUIRED = 'a string that merely mentions it'\n\n\n"
            "def plain(amount: float) -> None:\n"
            "    '''See FinancialCategory.REVIEW_REQUIRED for details.'''\n"
        )
        assert unresolved_functions(source) == []


class TestAdvisorySignals:
    SOURCE = (
        "from finagent_safeguard import bank_client\n\n\n"
        "def helper(payload: str) -> str:\n"
        "    return payload\n"
    )

    def test_an_advisory_only_finding_does_not_fail_by_default(
        self, tmp_path: Path
    ) -> None:
        """The bank-client backstop produced 51 flags and 0 correct ones over
        155 stdlib modules. A build that fails on it fails constantly and gets
        switched off, which is worse than one that reports and continues."""
        _write(tmp_path, "m.py", self.SOURCE)
        assert main([str(tmp_path)]) == 0

    def test_strict_makes_it_fail(self, tmp_path: Path) -> None:
        _write(tmp_path, "m.py", self.SOURCE)
        assert main([str(tmp_path), "--strict"]) == 1

    def test_it_is_still_reported_without_strict(self, tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
        """Not failing is not the same as not saying."""
        _write(tmp_path, "m.py", self.SOURCE)
        main([str(tmp_path)])
        out = capsys.readouterr().out
        assert "helper" in out
        assert "advisory" in out

    def test_the_advisory_set_is_not_empty(self) -> None:
        """An empty set would silently make --strict meaningless and every
        advisory finding build-breaking."""
        assert ADVISORY_SIGNALS


class TestTheDiffRatchet:
    """`--since REF` counts only findings in code the diff touched.

    This is what makes the gate adoptable rather than merely correct. Run over
    a brownfield codebase the linter flags every pre-existing regulated
    function, exits non-zero forever, and gets switched off -- at which point
    it protects nothing. Measured: 2,678 of 70,826 GitHub Python repositories
    carry any PEP 484 annotation, while Dropbox reached roughly four million
    annotated lines, and what they credit is raising strictness for *new* code.
    """

    @staticmethod
    def _repo(root: Path) -> None:
        import subprocess

        def git(*args: str) -> None:
            subprocess.run(
                ["git", *args], cwd=root, check=True, capture_output=True
            )

        git("init", "-q", ".")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        (root / "legacy.py").write_text(
            "from decimal import Decimal\n\n\n"
            "def old_transfer(debtor_iban: str, amount: Decimal) -> None:\n"
            "    pass\n\n\n"
            "def old_profile(email: str, personnummer: str) -> None:\n"
            "    pass\n"
        )
        git("add", "-A")
        git("commit", "-q", "-m", "base")

    @staticmethod
    def _commit(root: Path, body: str, message: str) -> None:
        import subprocess

        (root / "legacy.py").write_text(body)
        for args in (["add", "-A"], ["commit", "-q", "-m", message]):
            subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    def _run(self, root: Path, *extra: str) -> int:
        import os

        cwd = Path.cwd()
        try:
            os.chdir(root)
            return main(["legacy.py", *extra])
        finally:
            os.chdir(cwd)

    def test_without_the_ratchet_the_whole_tree_fails(self, tmp_path: Path) -> None:
        self._repo(tmp_path)
        assert self._run(tmp_path) == 1

    def test_editing_a_regulated_function_fails(self, tmp_path: Path) -> None:
        """The body changed under an untouched signature, which is exactly the
        change most worth classifying -- and the change a signature-only
        ratchet would wave through."""
        self._repo(tmp_path)
        body = (tmp_path / "legacy.py").read_text().replace(
            "def old_transfer(debtor_iban: str, amount: Decimal) -> None:\n    pass",
            "def old_transfer(debtor_iban: str, amount: Decimal) -> None:\n    audit(amount)",
        )
        self._commit(tmp_path, body, "touch one")
        assert self._run(tmp_path, "--since", "HEAD~1") == 1

    def test_editing_unrelated_code_passes(self, tmp_path: Path) -> None:
        """The property the whole feature exists for: honest work on an
        unannotated codebase is not blocked by somebody else's backlog."""
        self._repo(tmp_path)
        body = (tmp_path / "legacy.py").read_text() + (
            "\n\ndef add_docs(text: str) -> str:\n    return text\n"
        )
        self._commit(tmp_path, body, "unrelated")
        assert self._run(tmp_path, "--since", "HEAD~1") == 0

    def test_pre_existing_findings_are_still_printed(
        self, tmp_path: Path, capsys
    ) -> None:  # type: ignore[no-untyped-def]
        """A ratchet that hides the backlog is a blindfold. The count is the
        only honest measure of how much there is to do."""
        self._repo(tmp_path)
        body = (tmp_path / "legacy.py").read_text() + (
            "\n\ndef add_docs(text: str) -> str:\n    return text\n"
        )
        self._commit(tmp_path, body, "unrelated")
        self._run(tmp_path, "--since", "HEAD~1")
        out = capsys.readouterr().out
        assert "pre-existing" in out
        assert "old_transfer" in out
        assert "outside the diff" in out

    def test_an_unusable_ref_is_two_not_a_silent_pass(self, tmp_path: Path) -> None:
        """"Nothing changed" and "I cannot tell what changed" mean opposite
        things. Treating the second as the first would suppress every finding
        and report success."""
        self._repo(tmp_path)
        assert self._run(tmp_path, "--since", "no-such-ref") == 2

    def test_a_deletion_next_to_a_regulated_function_does_not_pull_it_in(
        self, tmp_path: Path
    ) -> None:
        """A pure deletion reports a hunk count of 0 at the line it vacated.

        Deliberately adjacent: the deleted comment sat immediately above
        `old_transfer`, so a hunk widened by even one line would reach its
        signature and the ratchet would demand classification for a function
        nobody touched. Testing a deletion in the middle of a file passes
        whether or not that bug is present, which is how the first version of
        this test let a mutation survive.
        """
        import subprocess

        subprocess.run(["git", "init", "-q", "."], cwd=tmp_path, check=True, capture_output=True)
        for args in (["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
        (tmp_path / "legacy.py").write_text(
            "from decimal import Decimal\n\n\n"
            "# a comment that will be deleted\n"
            "def old_transfer(debtor_iban: str, amount: Decimal) -> None:\n"
            "    pass\n"
        )
        for args in (["add", "-A"], ["commit", "-q", "-m", "base"]):
            subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

        self._commit(
            tmp_path,
            "from decimal import Decimal\n\n\n"
            "def old_transfer(debtor_iban: str, amount: Decimal) -> None:\n"
            "    pass\n",
            "drop the comment",
        )
        assert self._run(tmp_path, "--since", "HEAD~1") == 0

    def test_a_deletion_adds_no_lines_to_classify(self, tmp_path: Path) -> None:
        """A pure deletion reports a hunk count of 0. Nothing is there to
        classify, so it must not resurrect the neighbouring function."""
        self._repo(tmp_path)
        body = (tmp_path / "legacy.py").read_text().replace(
            "\n\ndef old_profile(email: str, personnummer: str) -> None:\n    pass\n", "\n"
        )
        self._commit(tmp_path, body, "delete one")
        assert self._run(tmp_path, "--since", "HEAD~1") == 0


class TestExcludingPaths:
    """A first run that is 80% test-file noise gets the tool switched off.

    Exclusion is an adoption aid, not a security boundary: everything it skips
    is simply not scanned, so the failure mode of a too-broad pattern is a
    quiet pass. That is why the count is always reported and a pattern matching
    nothing warns.
    """

    def _tree(self, root: Path) -> None:
        _write(root, "src/app/pay.py", MONEY)
        _write(root, "src/tests/test_pay.py", MONEY)

    def test_without_exclude_everything_is_scanned(
        self, tmp_path: Path, capsys  # type: ignore[no-untyped-def]
    ) -> None:
        self._tree(tmp_path)
        main([str(tmp_path)])
        assert "2 file(s) scanned" in capsys.readouterr().out

    def test_a_nested_directory_pattern_matches(
        self, tmp_path: Path, capsys  # type: ignore[no-untyped-def]
    ) -> None:
        """The case plain `fnmatch` gets wrong, and the one a developer will
        actually type. `fnmatch` anchors at the start of the string, so
        'tests/*' does not match 'src/tests/test_pay.py' -- the obvious pattern
        would silently exclude nothing.
        """
        self._tree(tmp_path)
        main([str(tmp_path), "--exclude", "tests/*"])
        out = capsys.readouterr().out
        assert "1 file(s) scanned" in out
        assert "1 file(s) excluded" in out

    def test_a_bare_directory_name_excludes_its_contents(
        self, tmp_path: Path, capsys  # type: ignore[no-untyped-def]
    ) -> None:
        """`--exclude tests` plainly means the directory, to any reader."""
        self._tree(tmp_path)
        main([str(tmp_path), "--exclude", "tests"])
        assert "1 file(s) excluded" in capsys.readouterr().out

    def test_a_filename_pattern_matches(
        self, tmp_path: Path, capsys  # type: ignore[no-untyped-def]
    ) -> None:
        self._tree(tmp_path)
        main([str(tmp_path), "--exclude", "*/test_*.py"])
        assert "1 file(s) excluded" in capsys.readouterr().out

    def test_the_count_is_always_reported(
        self, tmp_path: Path, capsys  # type: ignore[no-untyped-def]
    ) -> None:
        """Silence about what was skipped is how a too-broad pattern turns
        into a green build that means nothing."""
        self._tree(tmp_path)
        main([str(tmp_path), "--exclude", "src/*"])
        out = capsys.readouterr().out
        assert "0 file(s) scanned" in out
        assert "2 file(s) excluded" in out

    def test_a_pattern_matching_nothing_warns(
        self, tmp_path: Path, capsys  # type: ignore[no-untyped-def]
    ) -> None:
        """The commonest mistake: a pattern that matches nothing, and a run
        that therefore looks clean for the wrong reason."""
        self._tree(tmp_path)
        main([str(tmp_path), "--exclude", "nope/*"])
        captured = capsys.readouterr()
        assert "matched no files" in captured.err
        assert "2 file(s) scanned" in captured.out

    def test_an_explicitly_named_file_is_still_excluded(
        self, tmp_path: Path, capsys  # type: ignore[no-untyped-def]
    ) -> None:
        """A flag that silently does not apply is worse than one that is not
        there. If the pattern matches, the file is skipped however it was
        reached."""
        self._tree(tmp_path)
        main([str(tmp_path / "src/tests/test_pay.py"), "--exclude", "tests/*"])
        out = capsys.readouterr().out
        assert "0 file(s) scanned" in out
        assert "1 file(s) excluded" in out

    def test_a_pattern_does_not_match_across_a_directory_separator(
        self, tmp_path: Path, capsys  # type: ignore[no-untyped-def]
    ) -> None:
        """The bug the first implementation shipped with, pinned.

        Under `fnmatch`, `*` spans `/`. So `--exclude '*/test_*.py'` matched
        `~/test_project/src/app/pay.py` -- an ancestor directory beginning with
        `test_` was enough to exclude a developer's entire tree, reported only
        as a count they had no reason to question.

        `PurePath.match` does not cross separators, which is why it is used
        instead. Asserted on a path whose *ancestor* carries the pattern and
        whose filename does not.
        """
        _write(tmp_path, "test_project/src/app/pay.py", MONEY)
        main([str(tmp_path), "--exclude", "*/test_*.py"])
        out = capsys.readouterr().out
        assert "1 file(s) scanned" in out
        assert "excluded" not in out

    def test_a_pattern_is_anchored_at_the_right(
        self, tmp_path: Path, capsys  # type: ignore[no-untyped-def]
    ) -> None:
        """The other half of why `fnmatch` was wrong: it anchors left, so
        `tests/*` would not match `src/tests/x.py` and the obvious pattern
        would silently exclude nothing."""
        _write(tmp_path, "a/b/c/tests/test_x.py", MONEY)
        main([str(tmp_path), "--exclude", "tests/*"])
        assert "1 file(s) excluded" in capsys.readouterr().out

    def test_several_patterns_compose(
        self, tmp_path: Path, capsys  # type: ignore[no-untyped-def]
    ) -> None:
        self._tree(tmp_path)
        _write(tmp_path, "src/app/vendor.py", MONEY)
        main([
            str(tmp_path), "--exclude", "tests/*", "--exclude", "*/vendor.py",
        ])
        out = capsys.readouterr().out
        assert "1 file(s) scanned" in out
        assert "2 file(s) excluded" in out

    def test_exclusion_does_not_change_the_exit_status_of_what_remains(
        self, tmp_path: Path
    ) -> None:
        """Excluding the noise must not also excuse the findings."""
        self._tree(tmp_path)
        assert main([str(tmp_path), "--exclude", "tests/*"]) == 1

    def test_excluding_everything_exits_clean(self, tmp_path: Path) -> None:
        """Honest, and the reason the count is printed: a developer who
        excludes their whole tree gets a green run, and the summary is what
        tells them why."""
        self._tree(tmp_path)
        assert main([str(tmp_path), "--exclude", "src/*"]) == 0
