"""Import-time registration of regulated tools.

Registration does not wrap. In shift-left scope there is no runtime
interception to install, so the decorator records the classification and
returns the function untouched -- which keeps signatures, docstrings and
identity intact, and makes stacking trivially order-independent.

The registry is keyed by ``module:qualname``, never by an attribute on the
function. That is deliberate: an attribute can be hand-set, and the agent
gate must not be foolable by ``fn._regulated = True``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from finagent_safeguard.taxonomy.policies import FinancialCategory

__all__ = ["RegisteredTool", "TOOL_REGISTRY", "lookup", "registry_key", "regulated_tool"]

F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    """What the framework knows about a classified function."""

    module: str
    qualname: str
    categories: frozenset[FinancialCategory]

    @property
    def key(self) -> str:
        return f"{self.module}:{self.qualname}"


#: Module-level, populated at import time. Rebound wholesale in tests.
TOOL_REGISTRY: dict[str, RegisteredTool] = {}


def registry_key(func: Callable[..., Any]) -> str:
    return f"{func.__module__}:{func.__qualname__}"


def regulated_tool(*categories: FinancialCategory) -> Callable[[F], F]:
    """Classify a function against one or more regulatory categories.

    Stackable. Repeated application unions the categories, so a function
    handling both monetary flow and personal data carries both.
    """
    if not categories:
        raise ValueError(
            "regulated_tool requires at least one FinancialCategory; "
            "an empty classification is not a classification"
        )
    for category in categories:
        if not isinstance(category, FinancialCategory):
            raise TypeError(
                f"expected FinancialCategory, got {type(category).__name__}: "
                f"{category!r}"
            )

    def decorate(func: F) -> F:
        key = registry_key(func)
        existing = TOOL_REGISTRY.get(key)
        merged = frozenset(categories) | (existing.categories if existing else frozenset())
        TOOL_REGISTRY[key] = RegisteredTool(
            module=func.__module__, qualname=func.__qualname__, categories=merged
        )
        return func

    return decorate


def lookup(func: Callable[..., Any]) -> RegisteredTool | None:
    """Return the registration for ``func``, or ``None`` if unclassified."""
    return TOOL_REGISTRY.get(registry_key(func))
