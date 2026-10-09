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
    def test_the_decorator_attaches_to_the_function(self, tmp_path: Path) -> None:
        """Asserted through the parsed tree rather than by line position.

        The annotation is now several lines -- decorator, then the candidate
        provisions -- so the line immediately above the ``def`` is a comment.
        Position was always a proxy for the property that matters, which is
        that Python binds the decorator to that function, and the tree answers
        that directly. Comments between a decorator and a ``def`` are legal.
        """
        p = tmp_path / "m.py"
        p.write_text(SIMPLE)
        _fix(p)
        body = p.read_text()
        attached = linter.decorators_by_qualname(body)
        assert any(
            d.startswith("regulated_tool(") for d in attached["transfer"]
        ), attached
        assert body.rstrip().endswith("    pass")

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
        attached = linter.decorators_by_qualname(p.read_text())
        for name in ("one", "two", "three"):
            assert any(
                d.startswith("regulated_tool(") for d in attached[name]
            ), f"{name} lost its decorator: {attached}"

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
            linter, "_render_annotation", lambda finding: ["def sabotage(): pass"]
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

    def test_a_mixed_line_ending_file_is_fixed_correctly(self, tmp_path: Path) -> None:
        """The case that used to silently label the wrong function.

        Detection numbers lines with `ast`, which treats \\n, \\r\\n and \\r as
        breaks. The writer used to split on one newline guessed for the whole
        file, so every differing line above the target shifted the insertion
        point. Now both use the same model.
        """
        p = tmp_path / "m.py"
        p.write_bytes(
            b"from decimal import Decimal\r\n"
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
        result = linter.apply_fix(p, linter.scan_file(p))
        assert result.functions == ["transfer"]

        after = linter.decorators_by_qualname(p.read_text())
        assert after["transfer"], "the intended function must carry the decorator"
        assert not after["render_template"], "no other function may be touched"

        raw = p.read_bytes()
        assert b"import os\n" in raw, "an LF-only line must stay LF"
        assert b"def transfer(amount: Decimal) -> None:\r\n" in raw, "CRLF must stay CRLF"

    def test_the_guard_still_fires_if_the_line_model_regresses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Belt and braces. If someone reintroduces a split that disagrees with
        `ast`, the qualname check must refuse rather than mislabel."""
        p = tmp_path / "m.py"
        p.write_bytes(
            b"from decimal import Decimal\r\n"
            b"import os\n"
            b"import sys\n"
            b"\r\n\r\n"
            b"def transfer(amount: Decimal) -> None:\r\n    pass\r\n"
            b"\r\n\r\n"
            b"def render_template(t: str) -> str:\r\n    return t\r\n"
        )
        before = p.read_bytes()
        monkeypatch.setattr(
            linter, "_split_source_lines", lambda text: text.split("\r\n")
        )
        with pytest.raises(linter.UnsafeEdit):
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


class TestLineModel:
    """`str.splitlines()` is not the tokenizer's line model, and the difference
    is where the silent failures live."""

    def test_does_not_split_on_a_form_feed(self) -> None:
        """ast says `b = 2` is line 2. str.splitlines() makes it line 3, so a
        decorator aimed at line 2 would land on a bare newline."""
        src = "a = 1\x0c\nb = 2\n"
        assert len(linter._split_source_lines(src)) == 2
        assert len(src.splitlines(keepends=True)) == 3

    def test_does_not_split_on_a_line_separator_in_a_string(self) -> None:
        src = 'BANNER = "x y"\nb = 2\n'
        assert len(linter._split_source_lines(src)) == 2

    @pytest.mark.parametrize(
        "src", ["a\nb\n", "a\r\nb\r\n", "a\rb\r", "a\r\nb\nc\r", "a", ""]
    )
    def test_agrees_with_what_ast_counts_as_a_line(self, src: str) -> None:
        """The oracle that matters: our line count must match the parser's."""
        import ast as _ast

        body = _ast.parse(src.replace("a", "a = 1").replace("b", "b = 2").replace("c", "c = 3")).body
        if body:
            expected = max(node.lineno for node in body)
            lines = linter._split_source_lines(
                src.replace("a", "a = 1").replace("b", "b = 2").replace("c", "c = 3")
            )
            assert len(lines) >= expected

    def test_round_trips_any_source_byte_exactly(self) -> None:
        for src in ["a\nb\n", "a\r\nb\n", "x\x0cy\n", "no trailing newline", ""]:
            assert "".join(linter._split_source_lines(src)) == src or src == ""


class TestHostileCorpus:
    """Every file shape that has broken this tool, kept as a permanent corpus.

    The rule each case asserts is the same: either fix it correctly, or refuse
    and leave the file byte-identical. Never write something wrong.
    """

    CASES: dict[str, bytes] = {
        "plain_lf": b"def transfer(amount):\n    pass\n",
        "all_crlf": b"def transfer(amount):\r\n    pass\r\n",
        "all_cr": b"def transfer(amount):\r    pass\r",
        "mixed_crlf_lf": (
            b"import os\nimport sys\n\r\n\r\n"
            b"def transfer(amount):\r\n    pass\r\n\r\n\r\n"
            b"def other(t):\r\n    return t\r\n"
        ),
        "form_feed": b"def other(t):\n    return t\n\x0c\ndef transfer(amount):\n    pass\n",
        "line_sep_in_string": b'BANNER = "x\xe2\x80\xa8y"\ndef transfer(amount):\n    pass\n',
        "utf8_bom": b"\xef\xbb\xbfdef transfer(amount):\n    pass\n",
        "tab_indent": b"class P:\n\tdef transfer(self, amount):\n\t\tpass\n",
        "no_trailing_newline": b"def transfer(amount):\n    pass",
        "non_ascii_identifier": b"r\xc3\xa4kning = 1\ndef transfer(amount):\n    pass\n",
        "docstring_names_import": (
            b'"""Doc.\n\n    from finagent_safeguard.core.decorators import regulated_tool\n"""\n'
            b"def transfer(amount):\n    pass\n"
        ),
    }

    @pytest.mark.parametrize("label", sorted(CASES))
    def test_fixes_correctly_or_refuses_without_writing(
        self, tmp_path: Path, label: str
    ) -> None:
        import ast as _ast

        p = tmp_path / "m.py"
        p.write_bytes(self.CASES[label])
        before = p.read_bytes()

        try:
            result = linter.apply_fix(p, linter.scan_file(p))
        except (linter.RefusedTarget, linter.UnsafeEdit, linter.StaleFindings):
            assert p.read_bytes() == before, "refused, but wrote anyway"
            return

        after = p.read_bytes()
        _ast.parse(after.decode("utf-8-sig"))

        decorated = {
            q for q, d in linter.decorators_by_qualname(after.decode("utf-8-sig")).items() if d
        }
        assert decorated == set(result.functions), (
            f"reported {result.functions} but {decorated} carry the decorator"
        )
        assert after.startswith(b"\xef\xbb\xbf") == before.startswith(b"\xef\xbb\xbf")

    def test_a_tab_indented_method_is_indented_with_a_tab(self, tmp_path: Path) -> None:
        p = tmp_path / "m.py"
        p.write_bytes(self.CASES["tab_indent"])
        linter.apply_fix(p, linter.scan_file(p))
        assert "\t@regulated_tool(" in p.read_text()


class TestQualnameCheckAlone:
    """The qualname check must be load-bearing on its own.

    Inserting at the wrong line preserves every original byte, so the byte
    guard cannot see it and the file still parses. Only comparing which
    function carries which decorator catches this, which is exactly the hole in
    the old bare-name-set check.
    """

    def test_an_insertion_at_the_wrong_line_is_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src = (
            "from decimal import Decimal\n\n\n"
            "def transfer(amount: Decimal) -> None:\n    pass\n\n\n"
            "def render_template(t: str) -> str:\n    return t\n"
        )
        p = tmp_path / "m.py"
        p.write_text(src)
        before = p.read_bytes()

        real = linter.scan_source

        def shifted(text: str, path: Path) -> list[linter.Finding]:
            import dataclasses

            return [
                dataclasses.replace(f, insert_line=f.insert_line + 4)
                for f in real(text, path)
            ]

        # Patch both the caller's scan and plan_fix's freshness scan, so the
        # wrong line is internally consistent and only the qualname check can
        # object.
        monkeypatch.setattr(linter, "scan_source", shifted)

        with pytest.raises(linter.UnsafeEdit, match="landed on"):
            linter.apply_fix(p, linter.scan_file(p))
        assert p.read_bytes() == before


class TestRuntimeBinding:
    """An import must be bound *at runtime*, not merely present in the tree."""

    def test_a_type_checking_import_does_not_count_as_bound(
        self, tmp_path: Path
    ) -> None:
        """`if TYPE_CHECKING:` imports exist only for type-checkers. Counting
        them meant the tool skipped the import, inserted the decorator, and
        produced a file that parsed, kept the same functions, passed all four
        write guards -- and raised NameError on import."""
        import subprocess
        import sys

        p = tmp_path / "m.py"
        p.write_text(
            "from __future__ import annotations\n"
            "from decimal import Decimal\n"
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from finagent_safeguard.taxonomy.policies import FinancialCategory\n"
            "\n\ndef transfer(amount: Decimal) -> None:\n    pass\n"
        )
        _fix(p)
        done = subprocess.run(
            [sys.executable, "-c", f"import runpy; runpy.run_path({str(p)!r})"],
            capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1],
        )
        assert done.returncode == 0, done.stderr

    def test_a_function_local_import_does_not_count_as_bound(
        self, tmp_path: Path
    ) -> None:
        import subprocess
        import sys

        p = tmp_path / "m.py"
        p.write_text(
            "from decimal import Decimal\n\n\n"
            "def helper() -> None:\n"
            "    from finagent_safeguard.core.decorators import regulated_tool\n\n\n"
            "def transfer(amount: Decimal) -> None:\n    pass\n"
        )
        _fix(p)
        done = subprocess.run(
            [sys.executable, "-c", f"import runpy; runpy.run_path({str(p)!r})"],
            capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1],
        )
        assert done.returncode == 0, done.stderr


class TestFixIsFastEnoughToUse:
    def test_forty_functions_complete_quickly(self, tmp_path: Path) -> None:
        """The byte guard compared individual bytes, which made SequenceMatcher
        degenerate: 40 functions took 1.4s and a 50 KB module would have taken
        minutes. That is how a --fix flag gets abandoned."""
        import time

        p = tmp_path / "m.py"
        p.write_text(
            "from decimal import Decimal\n\n\n"
            + "".join(
                f"def transfer_{i}(amount: Decimal) -> None:\n    pass\n\n\n"
                for i in range(40)
            )
        )
        started = time.monotonic()
        _fix(p)
        assert time.monotonic() - started < 0.5


class TestTheFifthGuard:
    """What we *add* must work, not just leave the file undamaged.

    The other four guards check that the edit did not break the developer's
    code: it parses, the function set is unchanged, the decorator landed where
    intended, no original byte moved. None of them checks that the decorator we
    inserted references something that exists.

    A category absent from FinancialCategory produced a file that satisfied all
    four and raised AttributeError on import -- and the tool reported success.
    That is the failure class this project exists to prevent, found in the
    guard built to prevent it.
    """

    def test_an_unknown_category_is_refused(self, tmp_path: Path) -> None:
        import dataclasses

        p = tmp_path / "m.py"
        p.write_text(
            "from decimal import Decimal\n\n\n"
            "def transfer(amount: Decimal, iban: str) -> None:\n    pass\n"
        )
        findings = linter.scan_file(p)
        findings[0] = dataclasses.replace(findings[0], category="TOTALLY_MADE_UP")
        with pytest.raises(linter.UnsafeEdit, match="not a member of FinancialCategory"):
            linter.apply_fix(p, findings)

    def test_nothing_is_written_when_it_refuses(self, tmp_path: Path) -> None:
        """Refusing after writing would be worse than not checking."""
        import dataclasses

        p = tmp_path / "m.py"
        original = (
            "from decimal import Decimal\n\n\n"
            "def transfer(amount: Decimal, iban: str) -> None:\n    pass\n"
        )
        p.write_text(original)
        findings = linter.scan_file(p)
        findings[0] = dataclasses.replace(findings[0], category="NOPE")
        with pytest.raises(linter.UnsafeEdit):
            linter.apply_fix(p, findings)
        assert p.read_text() == original

    def test_review_required_is_accepted_and_imports(self, tmp_path: Path) -> None:
        """The flag the linter actually writes must survive its own guard and
        produce an importable file. Before REVIEW_REQUIRED joined the enum this
        wrote a file that raised AttributeError."""
        import dataclasses
        import subprocess
        import sys

        import dataclasses

        p = tmp_path / "m.py"
        p.write_text(
            "from decimal import Decimal\n\n\n"
            "def transfer(amount: Decimal, iban: str) -> None:\n    pass\n"
        )
        findings = linter.scan_file(p)
        findings[0] = dataclasses.replace(findings[0], category="REVIEW_REQUIRED")
        linter.apply_fix(p, findings)

        assert "FinancialCategory.REVIEW_REQUIRED" in p.read_text()
        done = subprocess.run(
            [sys.executable, "-c", f"import runpy; runpy.run_path({str(p)!r})"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[1],
        )
        assert done.returncode == 0, done.stderr

    def test_the_valid_set_is_read_from_the_enum(self) -> None:
        """Restating the member names would let the guard drift out of step
        with what the linter may emit -- the guard would then reject a category
        the enum had gained, or admit one it had lost."""
        from finagent_safeguard.cli.linter import _VALID_CATEGORIES
        from finagent_safeguard.taxonomy.policies import FinancialCategory

        assert _VALID_CATEGORIES == frozenset(m.name for m in FinancialCategory)
