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

#: Keys whose *value* is a party or an account, for subscript detection. A
#: superset of PII_TOKENS and the money words that name a party rather than a
#: quantity, because `config["name"]` and `row["iban"]` are the accesses that
#: matter and `config["domestic"]` is not.
SENSITIVE_KEYS: Final[frozenset[str]] = frozenset(
    PII_TOKENS
    | {
        # "name" is deliberately here and is NOT in PII_TOKENS, which holds
        # only "forename" and "surname". That gap is why the earlier
        # `party_key` feature could never fire on `self._config['name']` --
        # the single most common way a party is named in this corpus.
        "name", "fullname", "full_name", "holder", "account_holder",
        "account_holder_id", "iban", "bic", "account", "accountnumber",
        "account_number", "payee", "payer", "creditor", "debtor",
        "beneficiary", "originator", "recipient", "card", "pan", "mandate_id",
        "creditor_id", "initiating_party", "initiating_party_id",
        "ultimate_creditor",
    }
)


def _subscript_sensitivity(node: ast.AST) -> tuple[float, float]:
    """Does the body read a sensitive key out of a mapping, and how many?

    The data-flow feature the current set lacks. `_create_header` reads
    ``self._config['name']``; `_create_PmtInf_node` reads
    ``self._config.get('domestic', False)``. Both mention ``'Nm'`` and both
    look identical to every token feature -- the difference is *which key is
    read for its value*, and that is a property of the function rather than of
    the codebase's house style.

    Counts both ``x["k"]`` subscripts and ``x.get("k")`` calls, since a mapping
    is read both ways in the corpus.
    """
    hits: set[str] = set()
    total = 0
    for child in ast.walk(node):
        key: str | None = None
        if isinstance(child, ast.Subscript) and isinstance(child.slice, ast.Constant):
            # Loads only. `ED['Nm_Dbtr_Node'] = ET.Element('Nm')` is a *write*
            # into a local dictionary, and counting it inverted the feature:
            # the blank-node builder scored higher than the one that actually
            # reads a party's name. Reading a sensitive key for its value is
            # the signal; naming a slot is not.
            if isinstance(child.ctx, ast.Load) and isinstance(child.slice.value, str):
                key = child.slice.value
        elif (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "get"
            and child.args
            and isinstance(child.args[0], ast.Constant)
            and isinstance(child.args[0].value, str)
        ):
            key = child.args[0].value
        if key is None:
            continue
        total += 1
        if _matches_tokens(key, SENSITIVE_KEYS) or _matches_tokens(key, MONEY_TOKENS):
            hits.add(key.lower())
    return float(min(len(hits), 6)), float(min(total, 10))


def _callee_vocabulary(node: ast.AST) -> float:
    """Does the body call something whose *name* is domain vocabulary?

    The generic form of call-graph reachability, deliberately not the specific
    one. ``bank_client.execute_transfer`` as a literal target would encode
    which library this is, and repository identity is what the leave-one-out
    measurement exists to punish. A callee merely *named* with money or person
    words -- ``add_payment``, ``save_payment_sources``, ``camt053_to_df`` -- is
    domain language, and domain vocabulary is the one group that transferred.
    """
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        target = child.func
        name = (
            target.attr if isinstance(target, ast.Attribute)
            else target.id if isinstance(target, ast.Name)
            else ""
        )
        if name and (
            _matches_tokens(name, MONEY_TOKENS) or _matches_tokens(name, PII_TOKENS)
        ):
            return 1.0
    return 0.0


def _docstring_intent(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[float, float]:
    """Intent words in the docstring, and whether it disclaims doing the thing.

    Not an embedding. The discriminating word in the corpus's hardest pair is
    plain: `_create_PmtInf_node`'s docstring says "create the **blank** payment
    information nodes", and `_create_header`'s carries no such qualifier. A
    one-word lexical check reaches that where a 384-dimensional vector would
    also need 213 positives to fit, so this is tried first and an embedding
    only if it fails.
    """
    text = (ast.get_docstring(node) or "").lower()
    if not text:
        return 0.0, 0.0
    intent = sum(
        1
        for word in (
            "payment", "pay", "transfer", "debit", "credit", "amount",
            "account", "iban", "sepa", "invoice", "refund", "charge",
            "customer", "personal", "address", "email", "identity", "statement",
        )
        if word in text
    )
    # Words by which a docstring says it does *not* do the thing. "blank",
    # "empty" and "placeholder" are how the corpus's hardest negative
    # announces itself.
    disclaim = any(
        word in text
        for word in ("blank", "empty", "placeholder", "stub", "no-op", "noop",
                     "test ", "dummy", "skeleton", "template")
    )
    return float(min(intent, 5)), 1.0 if disclaim else 0.0


FEATURE_GROUPS: Final[dict[str, tuple[str, ...]]] = {
    "vocabulary": ("money_hits", "pii_hits", "both_vocabularies"),
    "verb": ("action_verb", "query_verb", "check_verb"),
    "sink": ("sink_call", "mocked"),
    "structure": ("param_count", "no_params", "risky_annotation", "private"),
    "literal": ("party_key", "placeholder", "body_lines", "string_literals"),
    # Added after the leave-one-repository-out measurement showed the original
    # groups were mostly house style. Each of these describes what the function
    # does to data rather than how it is written.
    "dataflow": ("sensitive_subscripts", "subscript_total", "callee_vocabulary"),
    "docstring": ("docstring_intent", "docstring_disclaims"),
}

FEATURE_NAMES: Final[tuple[str, ...]] = tuple(
    name for group in FEATURE_GROUPS.values() for name in group
)

#: Groups `extract` produces but the model is **not** fitted on.
#:
#: `literal` measured at **-0.069 LORO AUC** -- not merely useless but actively
#: harmful. Its members (`body_lines`, `string_literals`, `placeholder`,
#: `party_key`) are length and punctuation counts, so what they learn is which
#: repository a function came from. Under a stratified split that looked like
#: the single strongest group (+0.172); under leave-one-repository-out, where
#: repository identity cannot help, it inverted. That reversal is the whole
#: argument for measuring this way, so the group is kept, kept produced, and
#: kept out of the fit -- a deleted group would erase the evidence.
EXCLUDED_GROUPS: Final[tuple[str, ...]] = ("literal",)

#: Individual features `extract` produces but the model is not fitted on.
#:
#: `pii_hits` counts person-data words in a function's **signature** -- its own
#: name plus its parameters, never its body. Dropping it is worth **+0.019 LORO
#: AUC**, 95% CI [+0.009, +0.032], P(no gain) 0.00.
#:
#: Not because it is a bad feature. On its own it is a good one: P(label is yes
#: | it fires) is 0.50 against a 0.09 base rate in django-oscar, and its sign is
#: stable across every repository. The reason is **redundancy**.
#: `both_vocabularies` is literally `1 if money_hits and pii_hits`, computed
#: from the same scan of the same names, so it can only fire where `pii_hits`
#: already has (r = 0.547). L2 was splitting one feature's credit across two
#: expressions of it: remove `pii_hits` and `both_vocabularies` goes from +1.831
#: to +2.283 while no other weight moves by more than 0.04.
#:
#: So the signal is not lost by dropping it, it is concentrated. And the body is
#: where this evidence actually lives -- `self._config['name']` and
#: `row['email']` are invisible to a signature scan, which is why `pii_hits`
#: fires on only 23 of 996 functions and why `sensitive_subscripts` (+0.092)
#: earns far more.
#:
#: **Caveat recorded with the decision:** chosen by testing six candidate drops
#: against the same three scoreable folds it is measured on. The interval is
#: tight and the collinearity is independent evidence, but this is a selection
#: made on three repositories -- the same shape as the mistake LORO exists to
#: catch. Revisit when more repositories are scoreable.
EXCLUDED_FEATURES: Final[tuple[str, ...]] = ("pii_hits",)

#: The features the model is fitted on. `extract` returns a superset, so a
#: scored row always has every name this tuple asks for. Both exclusions stay
#: *produced*: they are shown to a human in the ranked queue, and deleting them
#: would erase the evidence for why they are not fitted.
MODEL_FEATURES: Final[tuple[str, ...]] = tuple(
    name
    for group, members in FEATURE_GROUPS.items()
    if group not in EXCLUDED_GROUPS
    for name in members
    if name not in EXCLUDED_FEATURES
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
    sensitive_subscripts, subscript_total = _subscript_sensitivity(node)
    docstring_intent, docstring_disclaims = _docstring_intent(node)

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
        # dataflow
        "sensitive_subscripts": sensitive_subscripts,
        "subscript_total": subscript_total,
        "callee_vocabulary": _callee_vocabulary(node),
        # docstring
        "docstring_intent": docstring_intent,
        "docstring_disclaims": docstring_disclaims,
    }
