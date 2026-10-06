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
