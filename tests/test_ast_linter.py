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
        assert found[0].indent == "    "


class TestSyntaxErrors:
    def test_a_file_that_does_not_parse_is_reported_not_skipped(
        self, tmp_path: Path
    ) -> None:
        """Silently skipping an unparseable file is a false negative that looks
        like a clean run."""
        with pytest.raises(linter.UnparseableSource):
            _findings(tmp_path, "def transfer(amount:\n")


class TestNestedDefinitions:
    """A def behind a feature flag or inside try/except is ordinary code.

    Stopping traversal at class and function children left all of these
    invisible to every signal, the structural backstop included.
    """

    @pytest.mark.parametrize(
        ("wrapper", "label"),
        [
            ("try:\n{body}\nexcept ImportError:\n    pass\n", "try/except"),
            ("if FEATURE_ON:\n{body}\n", "feature flag"),
            ("with open('x') as fh:\n{body}\n", "with"),
            ("for _ in range(1):\n{body}\n", "for"),
        ],
    )
    def test_finds_a_def_inside_a_statement_body(
        self, tmp_path: Path, wrapper: str, label: str
    ) -> None:
        body = "    def transfer(amount: Decimal) -> None:\n        pass"
        found = _findings(
            tmp_path, "from decimal import Decimal\n\nFEATURE_ON = True\n\n"
            + wrapper.format(body=body)
        )
        assert [f.function for f in found] == ["transfer"], label

    def test_finds_a_method_on_a_conditionally_defined_class(
        self, tmp_path: Path
    ) -> None:
        found = _findings(
            tmp_path,
            "from decimal import Decimal\n\nFEATURE_ON = True\n\n"
            "if FEATURE_ON:\n"
            "    class Payments:\n"
            "        def transfer(self, amount: Decimal) -> None:\n            pass\n",
        )
        assert [f.function for f in found] == ["Payments.transfer"]

    def test_the_bank_client_backstop_reaches_nested_defs(self, tmp_path: Path) -> None:
        """The signal advertised as not caring what a function is called also
        has to not care where it sits."""
        found = _findings(
            tmp_path,
            "import sys\n"
            "from tests.fixtures.linter.fake_bank import BankClient\n\n"
            "if sys.version_info >= (3, 11):\n"
            "    def handle_it(x: str) -> None:\n"
            "        BankClient().post(x, {})\n",
        )
        assert [f.function for f in found] == ["handle_it"]
        assert found[0].signal == "bank_client_import"


class TestDecoratorResolution:
    def test_an_aliased_decorator_counts_as_classified(self, tmp_path: Path) -> None:
        """Otherwise --fix stacks a second decorator with its own guessed
        category on a function that was already classified."""
        found = _findings(
            tmp_path,
            "from decimal import Decimal\n"
            "from finagent_safeguard.core.decorators import regulated_tool as regulated\n"
            "from finagent_safeguard.taxonomy.policies import FinancialCategory\n\n\n"
            "@regulated(FinancialCategory.PSD2_PAYMENT_EXECUTION)\n"
            "def transfer(amount: Decimal) -> None:\n    pass\n",
        )
        assert found == []

    def test_a_lookalike_decorator_does_not_count_as_classified(
        self, tmp_path: Path
    ) -> None:
        """The old substring test over ast.dump read `@deprecated("use
        regulated_tool instead")` as a classification, silently exempting a
        function that handles money."""
        found = _findings(
            tmp_path,
            "from decimal import Decimal\n\n\n"
            '@deprecated("use regulated_tool instead")\n'
            "def transfer(amount: Decimal) -> None:\n    pass\n",
        )
        assert [f.function for f in found] == ["transfer"]


class TestTokenPrecision:
    @pytest.mark.parametrize(
        "name", ["expand", "discard", "wildcard_match", "japan_locale", "company_name"]
    )
    def test_does_not_flag_a_lookalike_name(self, tmp_path: Path, name: str) -> None:
        """Substring matching flagged all of these -- and labelled `expand` as
        GDPR personal-data processing, because it contains "pan"."""
        found = _findings(tmp_path, f"def {name}(template: str) -> str:\n    return ''\n")
        assert found == [], name

    @pytest.mark.parametrize(
        "sig",
        [
            "def move(src: str, dst: str, value: Decimal, /) -> None",
            "def settle(*values: Decimal) -> None",
            "def lookup(customer_ref: str) -> Money",
        ],
    )
    def test_reads_the_whole_signature(self, tmp_path: Path, sig: str) -> None:
        found = _findings(
            tmp_path,
            "from decimal import Decimal\nfrom mytypes import Money\n\n\n"
            f"{sig}:\n    ...\n",
        )
        assert len(found) == 1, sig


class TestEncoding:
    def test_a_bom_prefixed_file_scans_normally(self, tmp_path: Path) -> None:
        """A byte-order mark is legal Python. Letting it reach the parser raised
        UnparseableSource, which is fatal by design, on a file that is fine."""
        p = tmp_path / "bom.py"
        p.write_bytes(
            b"\xef\xbb\xbffrom decimal import Decimal\n\n\n"
            b"def transfer(amount: Decimal) -> None:\n    pass\n"
        )
        assert [f.function for f in linter.scan_file(p)] == ["transfer"]


class TestRuntimeDiscardedStubs:
    def test_an_overload_stub_is_not_flagged(self, tmp_path: Path) -> None:
        """typing.overload discards the stub at runtime, so a decorator inserted
        there never executes -- and the file then re-scans clean, reporting
        classified while the live implementation is bare. The tool would be
        manufacturing the invisible false negative it exists to prevent."""
        found = _findings(
            tmp_path,
            "from decimal import Decimal\nfrom typing import overload\n\n\n"
            "@overload\n"
            "def fmt(v: Decimal) -> str: ...\n"
            "@overload\n"
            "def fmt(v: int) -> str: ...\n"
            "def fmt(v):\n    return str(v)\n",
        )
        assert [f.function for f in found] == []


class TestInsertedCategory:
    """The category written into a developer's source had no test at all, and
    no mutation row. It mapped every signal to payments, with a raw-substring
    override for personal data."""

    def test_personal_data_gets_the_gdpr_category(self, tmp_path: Path) -> None:
        found = _findings(tmp_path, "def store(ssn: str, dob: str, phone: str) -> None:\n    pass\n")
        assert found[0].category == "GDPR_PII_PROCESSING"

    def test_money_gets_the_payments_category(self, tmp_path: Path) -> None:
        found = _findings(
            tmp_path, "from decimal import Decimal\n\n\ndef settle(amount: Decimal) -> None:\n    pass\n"
        )
        assert found[0].category == "PSD2_PAYMENT_EXECUTION"

    def test_a_lookalike_does_not_get_the_gdpr_category(self, tmp_path: Path) -> None:
        """`expand_balance_window` was labelled GDPR because "expand" contains
        "pan"."""
        found = _findings(
            tmp_path,
            "from decimal import Decimal\n\n\n"
            "def expand_balance_window(amount: Decimal) -> None:\n    pass\n",
        )
        assert found[0].category == "PSD2_PAYMENT_EXECUTION"


class TestBankReachability:
    def test_a_realistic_module_path_is_recognised(self, tmp_path: Path) -> None:
        """The marker list held three basenames, one of which existed only in
        tests/fixtures/."""
        found = _findings(
            tmp_path,
            "from finagent_safeguard.bank.client import BankClient\n\n\n"
            "def innocuous_sounding_helper(x: str) -> str:\n    return x\n",
        )
        assert [f.signal for f in found] == ["bank_client_import"]


class TestQuotedAnnotations:
    def test_a_quoted_forward_reference_is_read(self, tmp_path: Path) -> None:
        found = _findings(
            tmp_path, 'def move(src: str, dst: str, value: "Decimal") -> None:\n    pass\n'
        )
        assert [f.signal for f in found] == ["type"]


class TestImportForms:
    """The structural backstop must see every ordinary way to import a module."""

    @pytest.mark.parametrize(
        "statement",
        [
            "from finagent_safeguard.bank_client import BankClient",
            "from finagent_safeguard import bank_client",
            "from finagent_safeguard import bank_client as bc",
            "from . import bank_client",
            "import finagent_safeguard.bank_client",
        ],
    )
    def test_every_import_form_reaches_the_backstop(
        self, tmp_path: Path, statement: str
    ) -> None:
        found = _findings(tmp_path, f"{statement}\n\n\ndef helper(x: str) -> str:\n    return x\n")
        assert [f.signal for f in found] == ["bank_client_import"], statement


class TestDetectionLineModel:
    def test_indent_is_read_from_the_right_line(self, tmp_path: Path) -> None:
        """Detection used str.splitlines(), which splits on characters the
        tokenizer ignores -- so the indent was read off the wrong line and
        --fix refused the file permanently, blaming the edit, not the scan."""
        found = _findings(
            tmp_path,
            'BANNER = "a\u2028b"\n\n\nclass P:\n'
            "    def transfer(self, amount):\n        pass\n",
        )
        assert found[0].indent == "    "


class TestMultiWordTokens:
    """A multi-word token must match whole words, not characters."""

    @pytest.mark.parametrize(
        "param,expected",
        [
            ("national_id", True),
            ("nationalId", True),
            ("my_national_id", True),
            ("international_ideas", False),
            ("national_ideas", False),
            ("nationality", False),
        ],
    )
    def test_substring_does_not_count_as_a_match(
        self, tmp_path: Path, param: str, expected: bool
    ) -> None:
        """The token "national_id" fired on "international_ideas", which would
        have written a GDPR category onto unrelated code."""
        from finagent_safeguard.cli.linter import _matches_tokens

        assert _matches_tokens(param, frozenset({"national_id"})) is expected


class TestOverloadResolution:
    """`@overload` exempts a stub -- but only the real one."""

    @pytest.mark.parametrize(
        "header,decorator",
        [
            ("from typing import overload\n", "@overload"),
            ("from typing_extensions import overload\n", "@overload"),
            ("import typing\n", "@typing.overload"),
            ("from typing import overload as ov\n", "@ov"),
        ],
    )
    def test_the_real_overload_exempts_the_stub(
        self, tmp_path: Path, header: str, decorator: str
    ) -> None:
        found = _findings(
            tmp_path,
            f"{header}\n{decorator}\ndef transfer(amount, iban):\n    pass\n",
        )
        assert found == []

    def test_a_local_decorator_named_overload_does_not_exempt(
        self, tmp_path: Path
    ) -> None:
        """Matching the bare name meant any unrelated decorator called
        `overload` silently exempted a money-handling function -- an invisible
        false negative a developer could create by accident."""
        found = _findings(
            tmp_path,
            "def overload(f):\n    return f\n\n\n"
            "@overload\ndef transfer(amount, iban):\n    pass\n",
        )
        assert [f.function for f in found] == ["transfer"]
