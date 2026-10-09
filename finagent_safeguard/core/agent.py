"""The inversion-of-control construction gate.

A developer hands the framework their tools; the framework refuses to build an
agent around any tool that has not been classified. In shift-left scope this is
the entire enforcement surface -- there is no runtime interception behind it --
so it fails closed at construction or it does nothing at all.

Two design choices shape this module. It matters to be precise about what each
does and does not achieve, because an earlier version of this docstring
overstated both.

1. **Enforcement is a module-level function**, called from ``__init__``. No
   method on the class performs the check, so there is no named validation hook
   to override, and ``BaseCompliantAgent`` exposes no public attributes.

   This does **not** make the gate unbypassable. The earlier claim that "a
   subclass has nothing to override" was false: ``__init__`` is itself the hook.
   A subclass defining ``__init__`` and never calling ``super().__init__``
   constructs an agent with no check at all, and so do ``object.__new__`` plus
   attribute assignment, ``pickle``, and ``copy.copy``. There is no
   ``__init_subclass__`` or ``__new__`` guard here yet. See F-004 in
   ``docs/review-log.md``.

2. **Classification is read from the registry**, keyed by ``module:qualname``,
   never from an attribute on the function object. Setting
   ``fn._regulated = True`` by hand buys nothing.

   The limit is worth stating plainly: ``__qualname__`` and ``__module__`` are
   themselves writable, and the decorator deliberately does not wrap, so a
   registration is tied to a *name* rather than to the code that executes.
   Classifying a stub and rebinding the name defeats this. See F-005.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from finagent_safeguard.core.decorators import lookup, registry_key
from finagent_safeguard.taxonomy.policies import FinancialCategory

__all__ = ["BaseCompliantAgent", "UnregulatedToolError"]


class UnregulatedToolError(Exception):
    """An agent was constructed around a tool that carries no classification.

    Deliberately not an ``ImportError``: nothing failed to import. This is a
    configuration error, and borrowing ``ImportError``'s meaning would mislead
    both the developer and any tooling that inspects the exception type.
    """


def _report_unresolved(unresolved: list[str]) -> str:
    """Message for tools carrying the linter's refusal rather than a category.

    Separate from the unclassified message because the remedy differs. An
    unclassified tool was never looked at; an unresolved one was found, flagged,
    and shipped anyway. Saying "classify this" to someone who already has the
    linter's output in front of them is unhelpful.
    """
    listed = "\n".join(f"  - {name}" for name in unresolved)
    return (
        f"{len(unresolved)} tool(s) passed to this agent carry "
        f"FinancialCategory.REVIEW_REQUIRED:\n{listed}\n\n"
        "REVIEW_REQUIRED is the linter's statement that it found a regulated "
        "function and had no basis to name the regulation. It is not a "
        "classification, and this gate will not accept it as one.\n\n"
        "Replace it with the category you have confirmed against the provision. "
        "`finagent-lint` prints the candidate provisions it considered."
    )


def _report(unclassified: Sequence[str]) -> str:
    listed = "\n".join(f"  - {name}" for name in unclassified)
    return (
        f"{len(unclassified)} tool(s) passed to this agent carry no regulatory "
        f"classification:\n{listed}\n\n"
        "Classify each one before constructing the agent:\n\n"
        "    from finagent_safeguard.core.decorators import regulated_tool\n"
        "    from finagent_safeguard.taxonomy.policies import FinancialCategory\n\n"
        "    @regulated_tool(FinancialCategory.PSD2_PAYMENT_EXECUTION)\n"
        "    def your_tool(...): ...\n\n"
        "Decorators stack, so a tool handling both monetary flow and personal "
        "data carries both categories. Run `finagent-lint --fix` to insert them."
    )


def _require_classification(
    tools: Sequence[Callable[..., Any]],
) -> frozenset[FinancialCategory]:
    """Return the union of declared categories, or refuse to proceed.

    Module-level by design: see this module's docstring, point 1.
    """
    categories: set[FinancialCategory] = set()
    unclassified: list[str] = []
    unresolved: list[str] = []

    for tool in tools:
        registration = lookup(tool)
        if registration is None:
            unclassified.append(registry_key(tool))
            continue
        # A registration is not the same as a decision. REVIEW_REQUIRED is the
        # linter saying it found a regulated function and could not name the
        # regulation; treating it as a category here would let the one state
        # that exists to demand human judgement sail through the gate that
        # exists to require it.
        if FinancialCategory.REVIEW_REQUIRED in registration.categories:
            unresolved.append(registry_key(tool))
            continue
        categories |= registration.categories

    if unclassified:
        raise UnregulatedToolError(_report(unclassified))
    if unresolved:
        raise UnregulatedToolError(_report_unresolved(unresolved))

    return frozenset(categories)


class BaseCompliantAgent:
    """An agent whose tools are all classified, or which does not exist."""

    def __init__(
        self,
        *,
        agent_name: str,
        tools: Sequence[Callable[..., Any]],
        instructions: str = "",
    ) -> None:
        self.agent_name = agent_name
        self.instructions = instructions
        self.tools: tuple[Callable[..., Any], ...] = tuple(tools)
        # Raises before the instance is usable. An agent that got past this
        # line has every tool classified.
        self.categories: frozenset[FinancialCategory] = _require_classification(self.tools)

    def __repr__(self) -> str:
        names = sorted(c.value for c in self.categories)
        return (
            f"{type(self).__name__}(agent_name={self.agent_name!r}, "
            f"tools={len(self.tools)}, categories={names})"
        )
