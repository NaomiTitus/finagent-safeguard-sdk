"""Four documented attempts to defeat the gate, kept as permanent attacks.

See docs/test-matrix.md section 4. The risk in a guardrail framework is never
that the check computes the wrong answer -- it is that the check was routed
around. Each test here is an attack that must keep failing.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from finagent_safeguard.core import agent as agent_module
from finagent_safeguard.core import decorators as dec
from finagent_safeguard.core.agent import BaseCompliantAgent, UnregulatedToolError


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dec, "TOOL_REGISTRY", {})


def _unclassified() -> Any:
    def move_funds(amount: Decimal, iban: str) -> str:
        return f"moved {amount}"

    return move_funds


class TestSubclassOverride:
    def test_subclass_cannot_override_enforcement(self) -> None:
        """Attack: inherit, then neuter the validation hook.

        Defence: enforcement lives in a module-level function called from
        __init__, not in an overridable method.
        """

        class Sneaky(BaseCompliantAgent):
            def _validate_tools(self, tools: Any) -> None:  # tempting name
                return None

            def _enforce(self, tools: Any) -> None:
                return None

            def validate(self, tools: Any) -> None:
                return None

        with pytest.raises(UnregulatedToolError):
            Sneaky(agent_name="sneaky", tools=[_unclassified()])

    def test_class_exposes_no_overridable_validation_hook(self) -> None:
        """Structural half of the same attack: there must be nothing to override."""
        hooks = [
            name
            for name in dir(BaseCompliantAgent)
            if ("valid" in name.lower() or "enforce" in name.lower() or "check" in name.lower())
        ]
        assert hooks == [], f"overridable validation hooks present: {hooks}"


class TestAttributeSpoofing:
    def test_attribute_spoofing_does_not_satisfy_the_gate(self) -> None:
        """Attack: hand-set a marker attribute instead of classifying.

        Defence: the gate consults the registry keyed by module:qualname,
        never an attribute on the function object.
        """
        tool = _unclassified()
        tool._regulated = True  # type: ignore[attr-defined]
        tool.categories = frozenset({"psd2_payment_execution"})  # type: ignore[attr-defined]
        tool.__finagent_registered__ = True  # type: ignore[attr-defined]

        with pytest.raises(UnregulatedToolError):
            BaseCompliantAgent(agent_name="spoofed", tools=[tool])


class TestFailClosed:
    def test_fails_closed_when_registry_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Attack: break the registry and hope the gate degrades to permissive.

        An infrastructure failure must surface as itself, not be laundered
        into a compliance pass -- and must never permit construction.
        """

        def _broken(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("registry backend unavailable")

        monkeypatch.setattr(agent_module, "lookup", _broken)

        with pytest.raises(RuntimeError, match="registry backend unavailable"):
            BaseCompliantAgent(agent_name="degraded", tools=[_unclassified()])

    @pytest.mark.parametrize("failing_symbol", ["lookup", "registry_key"])
    def test_no_exception_path_permits_construction(
        self, monkeypatch: pytest.MonkeyPatch, failing_symbol: str
    ) -> None:
        """Fault-inject each step of the gate in turn; none may yield a pass."""

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError(f"{failing_symbol} exploded")

        if hasattr(agent_module, failing_symbol):
            monkeypatch.setattr(agent_module, failing_symbol, _boom)
            with pytest.raises(RuntimeError):
                BaseCompliantAgent(agent_name="faulty", tools=[_unclassified()])
