"""RED-phase specification for the linter's detection half.

See docs/test-matrix.md section 5. The criterion this answers:

    The linter must find every function that touches money or personal data in
    a module that can reach the bank client, including functions no naming
    convention would reveal.

False negatives are the dangerous direction. A function the linter misses is
missed identically on the developer's laptop and in CI, because it is the same
detection code in both places.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from finagent_safeguard.cli import linter


def _findings(tmp_path: Path, source: str, name: str = "mod.py") -> list[linter.Finding]:
    path = tmp_path / name
    path.write_text(source)
    return linter.scan_file(path)


class TestNameSignal:
    def test_flags_by_parameter_name(self, tmp_path: Path) -> None:
        found = _findings(
            tmp_path,
            "from decimal import Decimal\n\n\n"
            "def transfer(amount: Decimal, iban: str) -> str:\n"
            "    return ''\n",
        )
        assert [f.function for f in found] == ["transfer"]

    def test_reports_the_def_line(self, tmp_path: Path) -> None:
        found = _findings(tmp_path, "\n\n\ndef pay(amount):\n    pass\n")
        assert found[0].insert_line == 4

    def test_does_not_flag_an_unrelated_function(self, tmp_path: Path) -> None:
        found = _findings(
            tmp_path, "import datetime\n\n\ndef format_date(d: datetime.date) -> str:\n"
            "    return d.isoformat()\n"
        )
        assert found == []


class TestTypeSignal:
    def test_flags_by_type_annotation_only(self, tmp_path: Path) -> None:
        """No name token anywhere. Only the types reveal it.

        This is the case a name-matching linter misses, and the reason the name
        signal alone is not enough.
        """
        found = _findings(
            tmp_path,
            "from decimal import Decimal\n\n\n"
            "def move(src: str, dst: str, value: Decimal) -> None:\n"
            "    pass\n",
        )
        assert [f.function for f in found] == ["move"]
        assert found[0].signal == "type"

    def test_flags_a_project_money_type(self, tmp_path: Path) -> None:
        found = _findings(
            tmp_path, "from mytypes import Money\n\n\ndef settle(v: Money) -> None:\n"
            "    pass\n"
        )
        assert [f.function for f in found] == ["settle"]


class TestStructuralSignal:
    def test_flags_public_functions_in_a_module_reaching_the_bank(
        self, tmp_path: Path
    ) -> None:
        """The backstop. It does not care what the function is called or typed."""
        found = _findings(
            tmp_path,
            "from tests.fixtures.linter.fake_bank import BankClient\n\n\n"
            "def innocuous_sounding_helper(x: str) -> str:\n"
            "    return BankClient().post(x, {})\n",
        )
        assert [f.function for f in found] == ["innocuous_sounding_helper"]
        assert found[0].signal == "bank_client_import"

    def test_does_not_flag_private_functions(self, tmp_path: Path) -> None:
        """Documented scope: the structural rule covers the public surface."""
        found = _findings(
            tmp_path,
            "from tests.fixtures.linter.fake_bank import BankClient\n\n\n"
            "def _helper(x: str) -> str:\n    return x\n",
        )
        assert found == []


class TestAlreadyClassified:
    def test_does_not_flag_a_decorated_function(self, tmp_path: Path) -> None:
        found = _findings(
            tmp_path,
            "from decimal import Decimal\n"
            "from finagent_safeguard.core.decorators import regulated_tool\n"
            "from finagent_safeguard.taxonomy.policies import FinancialCategory\n\n\n"
            "@regulated_tool(FinancialCategory.PSD2_PAYMENT_EXECUTION)\n"
            "def transfer(amount: Decimal) -> None:\n    pass\n",
        )
        assert found == []

    def test_insert_line_is_above_existing_decorators(self, tmp_path: Path) -> None:
        """A new decorator must land above the ones already there, so the
        insertion point is the first decorator's line, not the def's."""
        found = _findings(
            tmp_path,
            "from decimal import Decimal\n\n\n"
            "@staticmethod\n"
            "def transfer(amount: Decimal) -> None:\n    pass\n",
        )
        assert found[0].insert_line == 4


class TestAsyncAndNesting:
    def test_detects_async_def(self, tmp_path: Path) -> None:
        found = _findings(
            tmp_path, "from decimal import Decimal\n\n\n"
            "async def transfer(amount: Decimal) -> None:\n    pass\n"
        )
        assert [f.function for f in found] == ["transfer"]

    def test_detects_a_method_inside_a_class(self, tmp_path: Path) -> None:
        found = _findings(
            tmp_path,
            "from decimal import Decimal\n\n\n"
            "class Payments:\n"
            "    def transfer(self, amount: Decimal) -> None:\n        pass\n",
        )
        assert [f.function for f in found] == ["Payments.transfer"]
        assert found[0].indent == 4


class TestSyntaxErrors:
    def test_a_file_that_does_not_parse_is_reported_not_skipped(
        self, tmp_path: Path
    ) -> None:
        """Silently skipping an unparseable file is a false negative that looks
        like a clean run."""
        with pytest.raises(linter.UnparseableSource):
            _findings(tmp_path, "def transfer(amount:\n")
