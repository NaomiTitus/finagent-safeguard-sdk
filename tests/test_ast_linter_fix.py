"""RED-phase specification for the linter's write path.

The criterion this answers:

    --fix must never leave a source file in a state the developer did not
    inspect.

Every test here asserts on BYTES, not on a parsed tree. A tool that silently
reformats a developer's file is used once.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from finagent_safeguard.cli import linter

SIMPLE = (
    "from decimal import Decimal\n"
    "\n"
    "\n"
    "def transfer(amount: Decimal) -> None:\n"
    "    pass\n"
)


def _fix(path: Path) -> linter.FixResult:
    return linter.apply_fix(path, linter.scan_file(path))


class TestInsertion:
    def test_inserts_the_decorator_above_the_def(self, tmp_path: Path) -> None:
        p = tmp_path / "m.py"
        p.write_text(SIMPLE)
        _fix(p)
        lines = p.read_text().splitlines()
        assert lines[-3].startswith("@regulated_tool(")
        assert lines[-2].startswith("def transfer")
        assert lines[-1] == "    pass"

    def test_inserts_above_existing_decorators(self, tmp_path: Path) -> None:
        p = tmp_path / "m.py"
        p.write_text(SIMPLE.replace("def transfer", "@staticmethod\ndef transfer"))
        _fix(p)
        body = p.read_text()
        assert body.index("@regulated_tool(") < body.index("@staticmethod")

    def test_matches_indentation_inside_a_class(self, tmp_path: Path) -> None:
        p = tmp_path / "m.py"
        p.write_text(
            "from decimal import Decimal\n\n\nclass P:\n"
            "    def transfer(self, amount: Decimal) -> None:\n        pass\n"
        )
        _fix(p)
        inserted = [ln for ln in p.read_text().splitlines() if "@regulated_tool(" in ln]
        assert inserted[0].startswith("    @"), inserted[0]

    def test_handles_async_def(self, tmp_path: Path) -> None:
        p = tmp_path / "m.py"
        p.write_text(SIMPLE.replace("def transfer", "async def transfer"))
        _fix(p)
        assert "@regulated_tool(" in p.read_text()

    def test_multiple_functions_all_land_correctly(self, tmp_path: Path) -> None:
        """Edits must be applied bottom-up; a top-down insert shifts every line
        below it and the second decorator lands in the wrong place."""
        p = tmp_path / "m.py"
        p.write_text(
            "from decimal import Decimal\n\n\n"
            "def one(amount: Decimal) -> None:\n    pass\n\n\n"
            "def two(iban: str) -> None:\n    pass\n\n\n"
            "def three(payee: str) -> None:\n    pass\n"
        )
        _fix(p)
        out = p.read_text().splitlines()
        for i, line in enumerate(out):
            if line.startswith("def "):
                assert out[i - 1].startswith("@regulated_tool("), line

    def test_adds_the_imports_once(self, tmp_path: Path) -> None:
        p = tmp_path / "m.py"
        p.write_text(SIMPLE)
        _fix(p)
        body = p.read_text()
        assert body.count("from finagent_safeguard.core.decorators import") == 1
        assert body.count("from finagent_safeguard.taxonomy.policies import") == 1


class TestPreservation:
    def test_preserves_comments(self, tmp_path: Path) -> None:
        p = tmp_path / "m.py"
        p.write_text(
            '"""Module docstring."""\n'
            "# a standalone comment\n"
            "from decimal import Decimal\n\n\n"
            "def transfer(amount: Decimal) -> None:  # trailing comment\n"
            "    pass\n"
        )
        _fix(p)
        body = p.read_text()
        assert '"""Module docstring."""' in body
        assert "# a standalone comment" in body
        assert "# trailing comment" in body

    def test_preserves_crlf_line_endings(self, tmp_path: Path) -> None:
        p = tmp_path / "m.py"
        p.write_bytes(SIMPLE.replace("\n", "\r\n").encode())
        _fix(p)
        raw = p.read_bytes()
        assert b"\r\n" in raw
        assert b"\n" not in raw.replace(b"\r\n", b"")

    def test_preserves_blank_line_structure(self, tmp_path: Path) -> None:
        p = tmp_path / "m.py"
        p.write_text(SIMPLE)
        _fix(p)
        assert "\n\n\n@regulated_tool(" in p.read_text()


class TestSafety:
    def test_is_idempotent(self, tmp_path: Path) -> None:
        p = tmp_path / "m.py"
        p.write_text(SIMPLE)
        _fix(p)
        once = p.read_bytes()
        _fix(p)
        assert p.read_bytes() == once

    def test_aborts_when_the_reparse_changes_the_function_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the edited source no longer defines the same functions, the edit
        corrupted something. Abandon rather than write."""
        p = tmp_path / "m.py"
        p.write_text(SIMPLE)
        before = p.read_bytes()
        monkeypatch.setattr(
            linter, "_render_decorator", lambda finding: "def sabotage(): pass"
        )
        with pytest.raises(linter.UnsafeEdit):
            _fix(p)
        assert p.read_bytes() == before

    def test_aborts_if_the_file_changed_since_it_was_read(self, tmp_path: Path) -> None:
        """Time-of-check to time-of-use: the findings describe a file that no
        longer exists."""
        p = tmp_path / "m.py"
        p.write_text(SIMPLE)
        findings = linter.scan_file(p)
        p.write_text(SIMPLE + "\n\ndef added_later(amount): pass\n")
        with pytest.raises(linter.StaleFindings):
            linter.apply_fix(p, findings)

    def test_leaves_no_temporary_file_behind(self, tmp_path: Path) -> None:
        p = tmp_path / "m.py"
        p.write_text(SIMPLE)
        _fix(p)
        assert [q.name for q in tmp_path.iterdir()] == ["m.py"]

    def test_reports_what_it_changed(self, tmp_path: Path) -> None:
        p = tmp_path / "m.py"
        p.write_text(SIMPLE)
        result = _fix(p)
        assert result.inserted == 1
        assert result.functions == ["transfer"]


class TestPassiveMode:
    def test_scanning_never_writes(self, tmp_path: Path) -> None:
        p = tmp_path / "m.py"
        p.write_text(SIMPLE)
        before = p.read_bytes()
        linter.scan_file(p)
        assert p.read_bytes() == before


class TestImportPlacement:
    """Imports must land above the decorator that uses them.

    The previous implementation hunted for the substring ``__future__`` and took
    the last line mentioning it -- a comment sufficed -- putting the imports
    below the decorator. The file parsed, defined the same functions, and both
    guards reported success. Importing it raised NameError.
    """

    def test_a_later_mention_of_future_does_not_move_the_imports(
        self, tmp_path: Path
    ) -> None:
        p = tmp_path / "m.py"
        p.write_text(
            "from decimal import Decimal\n\n\n"
            "def transfer(amount: Decimal) -> None:\n"
            "    pass  # kept for parity with the old __future__-annotated module\n"
        )
        _fix(p)
        body = p.read_text()
        assert body.index("import regulated_tool") < body.index("@regulated_tool(")

    def test_imports_land_below_a_shebang(self, tmp_path: Path) -> None:
        p = tmp_path / "m.py"
        p.write_text(
            "#!/usr/bin/env python3\n"
            "from decimal import Decimal\n\n\n"
            "def transfer(amount: Decimal) -> None:\n    pass\n"
        )
        _fix(p)
        assert p.read_text().splitlines()[0] == "#!/usr/bin/env python3"

    def test_imports_land_below_a_real_future_import(self, tmp_path: Path) -> None:
        p = tmp_path / "m.py"
        p.write_text(
            "from __future__ import annotations\n"
            "from decimal import Decimal\n\n\n"
            "def transfer(amount: Decimal) -> None:\n    pass\n"
        )
        _fix(p)
        body = p.read_text()
        assert body.index("__future__") < body.index("import regulated_tool")

    def test_the_result_actually_imports(self, tmp_path: Path) -> None:
        """The guards check that the file parses and defines the same functions.
        Neither notices an import placed below its use. This does."""
        import subprocess
        import sys

        p = tmp_path / "m.py"
        p.write_text(
            "from decimal import Decimal\n\n\n"
            "def transfer(amount: Decimal) -> None:\n"
            "    pass  # a comment mentioning __future__\n"
        )
        _fix(p)
        done = subprocess.run(
            [sys.executable, "-c", f"import runpy; runpy.run_path({str(p)!r})"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[1],
        )
        assert done.returncode == 0, done.stderr


class TestFileIdentity:
    def test_refuses_a_read_only_file(self, tmp_path: Path) -> None:
        """An atomic rename only needs a writable directory, so this previously
        succeeded silently. Read-only usually means do not touch."""
        p = tmp_path / "m.py"
        p.write_text(SIMPLE)
        p.chmod(0o444)
        try:
            with pytest.raises(linter.RefusedTarget, match="read-only"):
                _fix(p)
            assert p.read_text() == SIMPLE
        finally:
            p.chmod(0o644)

    def test_refuses_a_symlink(self, tmp_path: Path) -> None:
        """Rewriting it replaced the link with a regular file and left the real
        source untouched."""
        real = tmp_path / "real.py"
        real.write_text(SIMPLE)
        link = tmp_path / "link.py"
        link.symlink_to(real)
        with pytest.raises(linter.RefusedTarget, match="symlink"):
            _fix(link)
        assert link.is_symlink()
        assert real.read_text() == SIMPLE

    def test_preserves_the_executable_bit(self, tmp_path: Path) -> None:
        """A temp-and-rename otherwise turns a 0o755 script into a 0o644 file."""
        import stat as _stat

        p = tmp_path / "m.py"
        p.write_text("#!/usr/bin/env python3\n" + SIMPLE)
        p.chmod(0o755)
        _fix(p)
        assert _stat.S_IMODE(p.stat().st_mode) == 0o755

    def test_refuses_a_non_utf8_file(self, tmp_path: Path) -> None:
        p = tmp_path / "m.py"
        p.write_bytes(b"# -*- coding: latin-1 -*-\n# caf\xe9\n" + SIMPLE.encode())
        with pytest.raises(linter.RefusedTarget, match="not UTF-8"):
            _fix(p)

    def test_the_write_is_atomic_not_in_place(self, tmp_path: Path) -> None:
        """Replacing the file must swap it, not overwrite it in place.

        The difference is observable as an inode change, and it is the whole
        point: a reader during the write sees either the old file or the new
        one, never a half-written source file. Asserting that no temp file is
        left behind does not test this -- the rename consumes the temp on the
        success path, so that assertion passes just as happily when the write
        is done in place.
        """
        p = tmp_path / "m.py"
        p.write_text(SIMPLE)
        before = p.stat().st_ino
        _fix(p)
        assert p.stat().st_ino != before, (
            "the file was modified in place; a reader could observe a "
            "half-written source file"
        )


class TestImportDetection:
    def test_a_docstring_mentioning_the_import_does_not_suppress_it(
        self, tmp_path: Path
    ) -> None:
        """`if line not in text` is a substring test, and the gate's own error
        message tells developers to write exactly this example. Neither import
        was inserted; the decorator was; the file parsed with the same function
        set and raised NameError on import."""
        p = tmp_path / "m.py"
        p.write_text(
            '"""Payments helpers.\n\n'
            "    from finagent_safeguard.core.decorators import regulated_tool\n"
            "    from finagent_safeguard.taxonomy.policies import FinancialCategory\n"
            '"""\n'
            "from decimal import Decimal\n\n\n"
            "def transfer(amount: Decimal) -> None:\n    pass\n"
        )
        _fix(p)
        import subprocess
        import sys

        done = subprocess.run(
            [sys.executable, "-c", f"import runpy; runpy.run_path({str(p)!r})"],
            capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1],
        )
        assert done.returncode == 0, done.stderr

    def test_an_aliased_existing_import_is_not_duplicated(self, tmp_path: Path) -> None:
        p = tmp_path / "m.py"
        p.write_text(
            "from finagent_safeguard.core.decorators import regulated_tool\n"
            "from finagent_safeguard.taxonomy.policies import FinancialCategory\n"
            "from decimal import Decimal\n\n\n"
            "def transfer(amount: Decimal) -> None:\n    pass\n"
        )
        _fix(p)
        assert p.read_text().count("import regulated_tool") == 1


class TestPartialFindings:
    def test_a_subset_of_findings_may_be_applied(self, tmp_path: Path) -> None:
        """Exact list equality meant a developer who inspected three findings
        and accepted two could not express that."""
        p = tmp_path / "m.py"
        p.write_text(
            "from decimal import Decimal\n\n\n"
            "def one(amount: Decimal) -> None:\n    pass\n\n\n"
            "def two(iban: str) -> None:\n    pass\n"
        )
        findings = linter.scan_file(p)
        result = linter.apply_fix(p, [findings[1]])
        assert result.functions == ["two"]
        assert p.read_text().count("@regulated_tool(") == 1

    def test_an_empty_list_is_a_no_op_not_an_error(self, tmp_path: Path) -> None:
        p = tmp_path / "m.py"
        p.write_text(SIMPLE)
        assert linter.apply_fix(p, []).inserted == 0
        assert p.read_text() == SIMPLE

    def test_a_content_change_is_detected_even_when_lines_are_unchanged(
        self, tmp_path: Path
    ) -> None:
        """Comparing names and line numbers cannot see an edit inside a function
        body, so a concurrent save was silently overwritten."""
        p = tmp_path / "m.py"
        p.write_text(SIMPLE)
        findings = linter.scan_file(p)
        p.write_text(SIMPLE.replace("    pass", "    audit()"))
        with pytest.raises(linter.StaleFindings):
            linter.apply_fix(p, findings)
        assert "audit()" in p.read_text()

    def test_a_finding_from_another_file_is_rejected(self, tmp_path: Path) -> None:
        """The digest check catches a changed file; this catches a finding that
        was never about this file at all -- a caller mixing up two scans."""
        import dataclasses

        p = tmp_path / "m.py"
        p.write_text(SIMPLE)
        real = linter.scan_file(p)[0]
        impostor = dataclasses.replace(real, function="not_in_this_file", insert_line=99)
        with pytest.raises(linter.StaleFindings, match="not findings for this file"):
            linter.apply_fix(p, [impostor])
        assert p.read_text() == SIMPLE


class TestVerificationIsNotVacuous:
    """The old check compared a set of bare function names before and after.

    Adding a decorator never changes a function's name, so that check returned
    the same answer whatever the edit did, as long as the file still parsed. It
    could not see a decorator landing on the wrong function -- which is exactly
    what happens on a file with mixed line endings.
    """

    def test_a_decorator_on_the_wrong_function_is_rejected(
        self, tmp_path: Path
    ) -> None:
        """Reproduces the real trigger, not a simulation of it.

        Detection numbers lines with `ast`, which treats \\n, \\r\\n and \\r as
        breaks. The writer splits on one newline guessed for the whole file. On
        a CRLF file containing an LF-only line, every line below that point is
        off by one, and the decorator lands on the following function while the
        intended one stays bare.
        """
        p = tmp_path / "m.py"
        p.write_bytes(
            b"from decimal import Decimal\r\n"
            # Two LF-only lines. One shifts far enough to break syntax, which the
            # parse check already caught; two produces a misplacement that is
            # perfectly valid Python -- the case the old name-set check waved
            # through, and the only one that mattered.
            b"import os\n"
            b"import sys\n"
            b"\r\n"
            b"\r\n"
            b"def transfer(amount: Decimal) -> None:\r\n"
            b"    pass\r\n"
            b"\r\n"
            b"\r\n"
            b"def render_template(t: str) -> str:\r\n"
            b"    return t\r\n"
        )
        before = p.read_bytes()
        with pytest.raises(linter.UnsafeEdit, match="landed on"):
            linter.apply_fix(p, linter.scan_file(p))
        assert p.read_bytes() == before, "nothing may be written when the check fails"

    def test_the_report_names_the_function_that_was_actually_decorated(
        self, tmp_path: Path
    ) -> None:
        p = tmp_path / "m.py"
        p.write_text(SIMPLE)
        result = linter.apply_fix(p, linter.scan_file(p))
        assert result.functions == ["transfer"]
        after = linter.decorators_by_qualname(p.read_text())
        assert after["transfer"], "the reported function must be the decorated one"

    def test_a_method_and_a_function_of_the_same_name_stay_distinct(
        self, tmp_path: Path
    ) -> None:
        """Bare names collapse `transfer` and `Payments.transfer` into one
        entry, which makes the check half-blind again."""
        src = (
            "from decimal import Decimal\n\n\n"
            "def transfer(amount: Decimal) -> None:\n    pass\n\n\n"
            "class Payments:\n"
            "    def transfer(self, amount: Decimal) -> None:\n        pass\n"
        )
        qualnames = set(linter.decorators_by_qualname(src))
        assert qualnames == {"transfer", "Payments.transfer"}


class TestByteEnvelope:
    """Some corruption is invisible to the parser. A dropped byte-order mark
    produces a file that parses fine, defines the same functions, and is three
    bytes shorter than the developer left it."""

    def test_a_byte_order_mark_survives(self, tmp_path: Path) -> None:
        p = tmp_path / "m.py"
        p.write_bytes(b"\xef\xbb\xbf" + SIMPLE.encode())
        _fix(p)
        assert p.read_bytes().startswith(b"\xef\xbb\xbf")

    def test_nothing_outside_the_inserted_lines_changes(self, tmp_path: Path) -> None:
        p = tmp_path / "m.py"
        p.write_text(SIMPLE)
        before = p.read_bytes()
        _fix(p)
        after = p.read_bytes()
        # Every original byte must still be present, in order.
        import difflib

        ops = difflib.SequenceMatcher(None, before, after).get_opcodes()
        assert all(tag in ("equal", "insert") for tag, *_ in ops), (
            f"bytes were deleted or replaced, not just inserted: {ops}"
        )


class TestPlanBeforeWrite:
    """--fix should be able to show its work before touching anything."""

    def test_plan_returns_a_diff_and_writes_nothing(self, tmp_path: Path) -> None:
        p = tmp_path / "m.py"
        p.write_text(SIMPLE)
        before = p.read_bytes()
        plan = linter.plan_fix(p, linter.scan_file(p))
        assert "@regulated_tool(" in plan.diff
        assert plan.diff.startswith("--- ")
        assert p.read_bytes() == before, "planning must not write"

    def test_the_plan_is_what_gets_written(self, tmp_path: Path) -> None:
        """The diff a developer approves must be the bytes that land."""
        p = tmp_path / "m.py"
        p.write_text(SIMPLE)
        plan = linter.plan_fix(p, linter.scan_file(p))
        linter.apply_fix(p, linter.scan_file(p))
        assert p.read_bytes() == plan.new_bytes
