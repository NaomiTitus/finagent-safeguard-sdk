"""RED-phase specification for the inversion-of-control agent gate.

See docs/test-matrix.md section 3. Constructing an agent with an unclassified
tool must fail immediately and loudly: in shift-left scope this is the whole
enforcement surface, so it fails closed or it does nothing.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from finagent_safeguard.core import decorators as dec
from finagent_safeguard.core.agent import BaseCompliantAgent, UnregulatedToolError
from finagent_safeguard.taxonomy.policies import FinancialCategory


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dec, "TOOL_REGISTRY", {})


@pytest.fixture
def classified_tool() -> object:
    @dec.regulated_tool(FinancialCategory.PSD2_PAYMENT_EXECUTION)
    def send_money(amount: Decimal, iban: str) -> str:
        return f"sent {amount}"

    return send_money


@pytest.fixture
def unclassified_tool() -> object:
    def quietly_move_funds(amount: Decimal, iban: str) -> str:
        return f"moved {amount}"

    return quietly_move_funds


class TestConstruction:
    def test_constructs_when_all_tools_registered(self, classified_tool: object) -> None:
        agent = BaseCompliantAgent(agent_name="treasury", tools=[classified_tool])
        assert agent.agent_name == "treasury"
        assert FinancialCategory.PSD2_PAYMENT_EXECUTION in agent.categories

    def test_undecorated_tool_raises(self, unclassified_tool: object) -> None:
        with pytest.raises(UnregulatedToolError):
            BaseCompliantAgent(agent_name="treasury", tools=[unclassified_tool])

    def test_partial_registration_still_fails(
        self, classified_tool: object, unclassified_tool: object
    ) -> None:
        """Three good tools must not launder the fourth."""
        with pytest.raises(UnregulatedToolError):
            BaseCompliantAgent(
                agent_name="treasury",
                tools=[classified_tool, classified_tool, unclassified_tool],
            )

    def test_empty_tool_list_is_permitted(self) -> None:
        """Documented decision: an agent with no tools has nothing to classify."""
        agent = BaseCompliantAgent(agent_name="chat_only", tools=[])
        assert agent.categories == frozenset()

    def test_aggregates_categories_across_tools(self) -> None:
        @dec.regulated_tool(FinancialCategory.PSD2_PAYMENT_EXECUTION)
        def pay(amount: Decimal) -> None: ...

        @dec.regulated_tool(FinancialCategory.GDPR_PII_PROCESSING)
        def notify(national_id: str) -> None: ...

        agent = BaseCompliantAgent(agent_name="both", tools=[pay, notify])
        assert agent.categories == frozenset(
            {FinancialCategory.PSD2_PAYMENT_EXECUTION, FinancialCategory.GDPR_PII_PROCESSING}
        )


class TestErrorQuality:
    def test_error_names_the_offending_callable(self, unclassified_tool: object) -> None:
        with pytest.raises(UnregulatedToolError) as excinfo:
            BaseCompliantAgent(agent_name="treasury", tools=[unclassified_tool])
        assert "quietly_move_funds" in str(excinfo.value)

    def test_error_names_the_remedy(self, unclassified_tool: object) -> None:
        """A gate that blocks without saying how to proceed does not get adopted."""
        with pytest.raises(UnregulatedToolError) as excinfo:
            BaseCompliantAgent(agent_name="treasury", tools=[unclassified_tool])
        message = str(excinfo.value)
        assert "@regulated_tool" in message
        assert "FinancialCategory" in message

    def test_error_is_not_import_error(self, unclassified_tool: object) -> None:
        """Regression guard. ImportError means a module failed to import; this is
        a configuration error and must not borrow that meaning."""
        with pytest.raises(UnregulatedToolError) as excinfo:
            BaseCompliantAgent(agent_name="treasury", tools=[unclassified_tool])
        assert not isinstance(excinfo.value, ImportError)


class TestReviewRequiredIsNotAClassification:
    """`REVIEW_REQUIRED` must not satisfy the gate that demands classification.

    It is a member of FinancialCategory for one mechanical reason -- so the
    decorator the linter writes imports. Treating it as a category here would
    let the one state that exists to demand human judgement pass the gate that
    exists to require it. That is the only place human review cannot help: the
    linter flagged it, CI flagged it, and someone shipped anyway.
    """

    @staticmethod
    def _build(tools: list[object]) -> object:
        return BaseCompliantAgent(agent_name="a", tools=tools)  # type: ignore[arg-type]

    def test_a_tool_carrying_it_is_refused(self) -> None:
        @dec.regulated_tool(FinancialCategory.REVIEW_REQUIRED)
        def unresolved(amount: Decimal) -> None: ...

        with pytest.raises(UnregulatedToolError, match="REVIEW_REQUIRED"):
            self._build([unresolved])

    def test_one_unresolved_tool_blocks_a_whole_agent(self) -> None:
        """No partial credit: an agent is as classified as its least
        classified tool."""

        @dec.regulated_tool(FinancialCategory.PSD2_PAYMENT_EXECUTION)
        def resolved(amount: Decimal) -> None: ...

        @dec.regulated_tool(FinancialCategory.REVIEW_REQUIRED)
        def unresolved(amount: Decimal) -> None: ...

        with pytest.raises(UnregulatedToolError):
            self._build([resolved, unresolved])

    def test_stacking_it_with_a_real_category_still_refuses(self) -> None:
        """A developer part-way through confirming a multi-category function
        has not finished. Accepting the resolved half would report the agent as
        classified while a regulatory concern is still unnamed."""

        @dec.regulated_tool(
            FinancialCategory.PSD2_PAYMENT_EXECUTION,
            FinancialCategory.REVIEW_REQUIRED,
        )
        def half_done(amount: Decimal, surname: str) -> None: ...

        with pytest.raises(UnregulatedToolError, match="REVIEW_REQUIRED"):
            self._build([half_done])

    def test_the_message_names_the_remedy_not_the_diagnosis(self) -> None:
        """The developer already has the linter's output in front of them.
        Telling them to classify the tool is unhelpful; telling them to replace
        the flag, and that candidates were offered, is not."""

        @dec.regulated_tool(FinancialCategory.REVIEW_REQUIRED)
        def unresolved(amount: Decimal) -> None: ...

        with pytest.raises(UnregulatedToolError) as caught:
            self._build([unresolved])
        message = str(caught.value)
        assert "is not a classification" in message
        assert "candidate provisions" in message

    def test_a_resolved_tool_still_passes(self) -> None:
        @dec.regulated_tool(FinancialCategory.PSD2_PAYMENT_EXECUTION)
        def resolved(amount: Decimal) -> None: ...

        built = self._build([resolved])
        assert FinancialCategory.PSD2_PAYMENT_EXECUTION in built.categories  # type: ignore[attr-defined]
