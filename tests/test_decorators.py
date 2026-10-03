"""RED-phase specification for the taxonomy and stacking decorators.

See docs/test-matrix.md section 2. These establish a *coverage* mechanism --
has this function been classified? -- and nothing more. The boundary matters:
no legal claim may live in an enum. Correctness lives in the registry, with a
citation and provenance (PLAN.md section 5.2).
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest

from finagent_safeguard.core import decorators as dec
from finagent_safeguard.taxonomy.policies import FinancialCategory


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dec, "TOOL_REGISTRY", {})


class TestRegistration:
    def test_single_decorator_registers_category(self) -> None:
        @dec.regulated_tool(FinancialCategory.PSD2_PAYMENT_EXECUTION)
        def pay(amount: Decimal) -> None: ...

        entry = dec.lookup(pay)
        assert entry is not None
        assert entry.categories == frozenset({FinancialCategory.PSD2_PAYMENT_EXECUTION})

    def test_stacked_decorators_register_both(self) -> None:
        @dec.regulated_tool(FinancialCategory.PSD2_PAYMENT_EXECUTION)
        @dec.regulated_tool(FinancialCategory.GDPR_PII_PROCESSING)
        def pay_and_notify(amount: Decimal, national_id: str) -> None: ...

        entry = dec.lookup(pay_and_notify)
        assert entry is not None
        assert entry.categories == frozenset(
            {FinancialCategory.PSD2_PAYMENT_EXECUTION, FinancialCategory.GDPR_PII_PROCESSING}
        )

    def test_duplicate_category_deduplicated(self) -> None:
        @dec.regulated_tool(FinancialCategory.PSD2_PAYMENT_EXECUTION)
        @dec.regulated_tool(FinancialCategory.PSD2_PAYMENT_EXECUTION)
        def pay(amount: Decimal) -> None: ...

        entry = dec.lookup(pay)
        assert entry is not None
        assert len(entry.categories) == 1

    def test_stacking_order_is_irrelevant(self) -> None:
        @dec.regulated_tool(FinancialCategory.GDPR_PII_PROCESSING)
        @dec.regulated_tool(FinancialCategory.PSD2_PAYMENT_EXECUTION)
        def reversed_order(amount: Decimal) -> None: ...

        @dec.regulated_tool(FinancialCategory.PSD2_PAYMENT_EXECUTION)
        @dec.regulated_tool(FinancialCategory.GDPR_PII_PROCESSING)
        def forward_order(amount: Decimal) -> None: ...

        first = dec.lookup(reversed_order)
        second = dec.lookup(forward_order)
        assert first is not None and second is not None
        assert first.categories == second.categories

    def test_multiple_categories_in_one_call(self) -> None:
        @dec.regulated_tool(
            FinancialCategory.PSD2_PAYMENT_EXECUTION,
            FinancialCategory.AML_TRANSACTION_MONITORING,
        )
        def pay(amount: Decimal) -> None: ...

        entry = dec.lookup(pay)
        assert entry is not None
        assert len(entry.categories) == 2


class TestMetadata:
    def test_metadata_captures_module_and_qualname(self) -> None:
        @dec.regulated_tool(FinancialCategory.PSD2_PAYMENT_EXECUTION)
        def pay(amount: Decimal) -> None: ...

        entry = dec.lookup(pay)
        assert entry is not None
        assert entry.module == __name__
        assert entry.qualname.endswith("pay")

    def test_decorator_preserves_function_identity(self) -> None:
        """Registration must not wrap: there is no runtime interception here."""

        def original(amount: Decimal) -> str:
            """Docstring preserved."""
            return f"got {amount}"

        signature_before = inspect.signature(original)
        decorated = dec.regulated_tool(FinancialCategory.PSD2_PAYMENT_EXECUTION)(original)

        assert decorated is original
        assert decorated.__doc__ == "Docstring preserved."
        assert inspect.signature(decorated) == signature_before
        assert decorated(Decimal("5")) == "got 5"


class TestRejection:
    def test_non_enum_category_rejected(self) -> None:
        with pytest.raises(TypeError, match="FinancialCategory"):
            dec.regulated_tool("PSD2_PAYMENT_EXECUTION")  # type: ignore[arg-type]

    def test_empty_classification_rejected(self) -> None:
        """An empty classification is not a classification."""
        with pytest.raises(ValueError, match="at least one"):
            dec.regulated_tool()


class TestImportTimeAndBoundary:
    def test_registry_populated_at_import_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Importing a module registers its tools; nothing need be called."""
        import importlib
        import sys

        monkeypatch.setattr(dec, "TOOL_REGISTRY", {})
        sys.modules.pop("tests.fixtures.sample_tools", None)
        importlib.import_module("tests.fixtures.sample_tools")

        qualnames = {entry.qualname for entry in dec.TOOL_REGISTRY.values()}
        assert "send_money" in qualnames
        assert "pay_and_notify" in qualnames
        assert "undecorated_transfer" not in qualnames

    def test_enum_carries_no_legal_claim(self) -> None:
        """Enums classify; they do not assert law.

        PLAN.md section 5.2: if a threshold or citation can live on a category,
        the project reproduces its original error in a tidier wrapper.
        """
        forbidden = {"threshold", "citation", "amount", "limit", "locus", "provision"}
        for member in FinancialCategory:
            assert isinstance(member.value, str)
            for name in forbidden:
                assert not hasattr(member, name), f"{member.name}.{name}"
        for name, value in vars(FinancialCategory).items():
            if not name.startswith("_"):
                assert not isinstance(value, (int, float, Decimal)), name
