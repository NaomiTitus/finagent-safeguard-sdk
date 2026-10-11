"""Features for the regulated-function classifier.

Every feature here is derived from ``docs/labelling-decisions.md`` -- the rule
the human labelled by -- and not from inspecting which items a model got wrong.
That distinction is the difference between a measurement and a fit: features
chosen by looking at the errors are tuned to the evaluation set, and the number
they produce means nothing.

They are grouped so the training report can say which groups earn their place.
Four groups, each encoding one clause of the rule:

    vocabulary  the existing money and person word lists
    verb        "validating is not producing", "acting on is yes"
    sink        "materialises it" against "passes it through"
    literal     "presence, not shape"

The last is the one most likely to surprise. The rule's hardest clause is that
a well-formed but synthetic identifier handles nobody's data, and whether that
is visible in the surface text at all is an open question this is built to
answer.

Pure stdlib, like everything the SDK ships. The trained model is twenty-odd
coefficients in a JSON file, which a reviewer at a bank can read in a minute --
the same reason CodeQL ships word lists rather than a model.
"""

from __future__ import annotations

import ast
import re
from typing import Final

from finagent_safeguard.cli.linter import (
    MONEY_TOKENS,
    PII_TOKENS,
    RISKY_ANNOTATIONS,
    _matches_tokens,
    _words,
)

__all__ = ["FEATURE_NAMES", "FEATURE_GROUPS", "extract"]

#: Verbs that act. From the rule's "acting on a specific subject's money or
#: data is yes" and from the Stripe action-endpoint family -- cancel, reject,
#: pause, expire all changed the state of a named thing.
ACTION_VERBS: Final[frozenset[str]] = frozenset({
    "create", "add", "save", "store", "write", "put", "post", "send", "submit",
    "transfer", "pay", "charge", "capture", "refund", "settle", "execute",
    "cancel", "reject", "expire", "pause", "resume", "void", "debit", "credit",
    "export", "emit", "publish", "dispatch", "update", "delete", "remove",
    "initiate", "collect", "withdraw", "deposit", "process",
})

#: Verbs that read. The conduit clause: reading something unspecified is no.
QUERY_VERBS: Final[frozenset[str]] = frozenset({
    "get", "fetch", "list", "retrieve", "search", "find", "read", "load",
    "lookup", "resolve", "parse", "extract", "iter", "all", "filter", "select",
})

#: Verbs that check. "Validating is not producing" -- the clause that separated
#: a constraint test from a message builder.
CHECK_VERBS: Final[frozenset[str]] = frozenset({
    "validate", "check", "verify", "assert", "compute", "calculate", "is",
    "has", "can", "should", "test", "solve", "match", "compare", "equals",
    "clean", "normalise", "normalize", "format", "render", "repr", "str",
})

#: Calls that put data somewhere it outlives the function. The "materialises
#: it" half of the conduit distinction -- camt053_to_excel writes a bank
#: statement to disk, clean_ids substitutes bytes and returns them.
SINK_CALLS: Final[tuple[str, ...]] = (
    ".to_excel", ".to_csv", ".to_json", ".to_sql", ".write", ".writelines",
    ".save", ".dump", ".export", ".commit", ".insert", ".create", ".post",
    ".put", ".patch", ".send", ".sendmail", "open(",
)

#: Evidence the function is talking to a stand-in rather than the world. Every
#: generated-example test in the corpus was a no, and all of them are mocked.
MOCK_MARKERS: Final[tuple[str, ...]] = (
    "mock", "Mock", "stub_request", "assert_requested", "patch(", "fixture",
)

#: Quoted keys naming a party or an account. The strongest "presence" signal
#: available from text: _create_header writes self._config['name'] into a Nm
#: node, and no parameter name reveals that.
PARTY_KEYS: Final[tuple[str, ...]] = (
    '"Nm"', "'Nm'", '"name"', "'name'", '"IBAN"', "'IBAN'", '"BIC"', "'BIC'",
    '"Dbtr"', "'Dbtr'", '"Cdtr"', "'Cdtr'", '"email"', "'email'",
    '"address"', "'address'", '"phone"', "'phone'", '"creditor_id"',
    "'creditor_id'", '"mandate_id"', "'mandate_id'",
)

#: Literals that announce themselves as filler. The "presence, not shape"
#: clause made mechanical: xxx placeholders, example.com, repeated characters.
_PLACEHOLDER = re.compile(
    r"""(x{4,}|xxxxx|example\.com|foo|bar|baz|dummy|placeholder"""
    r"""|"a"\s*\*\s*\d+|'a'\s*\*\s*\d+|_test_|sk_test|tok_visa|\btest\w*123)""",
    re.IGNORECASE,
)

FEATURE_GROUPS: Final[dict[str, tuple[str, ...]]] = {
    "vocabulary": ("money_hits", "pii_hits", "both_vocabularies"),
    "verb": ("action_verb", "query_verb", "check_verb"),
    "sink": ("sink_call", "mocked"),
    "structure": ("param_count", "no_params", "risky_annotation", "private"),
    "literal": ("party_key", "placeholder", "body_lines", "string_literals"),
}

FEATURE_NAMES: Final[tuple[str, ...]] = tuple(
    name for group in FEATURE_GROUPS.values() for name in group
)


def _leading_verb(name: str) -> str:
    """The first word of a function name, which is where Python puts the verb.

    ``_cls_cancel_async`` leads with a private marker and a class prefix, so
    the first *recognised* verb is taken rather than literally the first word.
    Getting this wrong would make every private method verbless.
    """
    parts = _words(name.lstrip("_"))
    ordered = [p for p in re.split(r"[^a-z0-9]+", name.lower()) if p]
    for part in ordered:
        if part in ACTION_VERBS or part in QUERY_VERBS or part in CHECK_VERBS:
            return part
    return next(iter(ordered), "") if ordered else next(iter(parts), "")


def extract(
    node: ast.FunctionDef | ast.AsyncFunctionDef, body: str
) -> dict[str, float]:
    """Features for one function, from its signature and its source text.

    ``body`` is the function's own source. The signature alone is not enough:
    two production functions in the corpus take their data from ``self``, so
    their parameters reveal nothing, and the text is the only place the
    evidence appears.
    """
    args = node.args
    all_args = [
        *args.posonlyargs,
        *args.args,
        *args.kwonlyargs,
        *([args.vararg] if args.vararg else []),
        *([args.kwarg] if args.kwarg else []),
    ]
    arg_names = [a.arg for a in all_args if a.arg not in ("self", "cls")]
    names = {node.name, *arg_names}

    money = sum(1 for n in names if _matches_tokens(n, MONEY_TOKENS))
    pii = sum(1 for n in names if _matches_tokens(n, PII_TOKENS))

    annotations = [a.annotation for a in all_args if a.annotation]
    if node.returns is not None:
        annotations.append(node.returns)
    risky = any(
        isinstance(a, ast.Name) and a.id in RISKY_ANNOTATIONS
        or isinstance(a, ast.Attribute) and a.attr in RISKY_ANNOTATIONS
        for a in annotations
    )

    verb = _leading_verb(node.name)
    lowered = body.lower()

    return {
        # vocabulary
        "money_hits": float(money),
        "pii_hits": float(pii),
        "both_vocabularies": 1.0 if money and pii else 0.0,
        # verb
        "action_verb": 1.0 if verb in ACTION_VERBS else 0.0,
        "query_verb": 1.0 if verb in QUERY_VERBS else 0.0,
        "check_verb": 1.0 if verb in CHECK_VERBS else 0.0,
        # sink
        "sink_call": 1.0 if any(s in body for s in SINK_CALLS) else 0.0,
        "mocked": 1.0 if any(m in body for m in MOCK_MARKERS) else 0.0,
        # structure
        "param_count": float(min(len(arg_names), 8)),
        "no_params": 1.0 if not arg_names else 0.0,
        "risky_annotation": 1.0 if risky else 0.0,
        "private": 1.0 if node.name.startswith("_") else 0.0,
        # literal
        "party_key": 1.0 if any(k in body for k in PARTY_KEYS) else 0.0,
        "placeholder": 1.0 if _PLACEHOLDER.search(lowered) else 0.0,
        "body_lines": float(min(len(body.splitlines()), 40)) / 40.0,
        "string_literals": float(min(body.count('"') + body.count("'"), 40)) / 40.0,
    }
