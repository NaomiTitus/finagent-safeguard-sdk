"""Find functions that touch money or personal data and carry no classification.

Detection only. Nothing here writes to a file; see ``apply_fix`` for that. The
split is deliberate so each half can be tested without the other.

Three signals, because each covers the others' blind spot:

1. **Name tokens** in the function or parameter names. Catches the obvious
   cases and misses ``move_funds(src, dst, value)``.
2. **Type annotations** -- a money or account type in the signature. Catches
   the badly-named function the name signal cannot see.
3. **Structural**: a module that imports the bank client must classify every
   public function in it. The backstop, and the only signal that does not care
   what a function is called or how it is typed.

False negatives are the dangerous direction. The same detection code runs on
the developer's machine and in CI, so a function missed here is missed in both
places, and nothing downstream catches it.
"""

from __future__ import annotations

import ast
import difflib
import io
import hashlib
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from finagent_safeguard.regulation.candidates import candidates_for, cite
from finagent_safeguard.taxonomy.policies import FinancialCategory

__all__ = [
    "ADVISORY_SIGNALS",
    "Finding",
    "FixPlan",
    "FixResult",
    "decorators_by_qualname",
    "plan_fix",
    "RefusedTarget",
    "StaleFindings",
    "UnparseableSource",
    "UnsafeEdit",
    "apply_fix",
    "main",
    "unresolved_functions",
    "read_source",
    "scan_file",
    "scan_source",
]

#: Tokens that suggest money or an identifiable person, in a function or
#: parameter name.
MONEY_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "amount", "iban", "bic", "swift", "payee", "payer", "recipient",
        "transfer", "transfers", "payment", "payments", "balance", "account",
        "card", "pan", "charge", "debit", "credit", "refund", "fee", "price",
        "withdraw", "deposit", "wire", "funds", "settle", "remit",
        "money", "cash", "pay", "transaction", "txn", "currency", "sepa",
        "beneficiary", "originator", "ledger", "invoice", "payout",
        "settlement", "salary", "loan", "overdraft",
    }
)

#: Tokens that suggest an identifiable person. Kept separate from money so the
#: *category* written into a developer's source is derived from which vocabulary
#: matched, rather than guessed by a substring test over the function name --
#: which mapped `store_ssn` to a payments classification and `expand_balance`
#: to GDPR, because "expand" contains "pan".
PII_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "national_id", "personnummer", "fodselsnummer", "henkilotunnus", "cpr",
        "ssn", "email", "phone", "address", "dob", "date_of_birth", "customer",
        "surname", "forename", "passport",
    }
)

NAME_TOKENS: Final[frozenset[str]] = MONEY_TOKENS | PII_TOKENS

#: Types that carry money or an account reference. A bare ``str`` is not here:
#: it would flag everything and a linter that flags everything gets switched off.
RISKY_ANNOTATIONS: Final[frozenset[str]] = frozenset(
    {"Decimal", "Money", "Amount", "IBAN", "AccountRef", "AccountNumber", "Card"}
)

#: Importing any of these means the module can reach the bank.
#: Markers that make a module able to reach the bank. Matched against every
#: component of a dotted import path, not only the last, so
#: ``finagent_safeguard.bank.client`` is recognised as well as ``bank_client``.
#:
#: Known limit, stated rather than hidden: this detects a DIRECT import. A
#: module that imports a helper which itself reaches the bank is not seen,
#: because a single-file scan cannot resolve the import graph. Closing that
#: needs a project-wide pass and is not attempted here.
BANK_CLIENT_MARKERS: Final[frozenset[str]] = frozenset(
    {"fake_bank", "bank_client", "core_banking", "bank", "psp", "ledger"}
)

_DECORATOR_MARKER: Final = "regulated_tool"


class RefusedTarget(Exception):
    """The file is not something this tool may rewrite in place."""


class UnparseableSource(Exception):
    """A file did not parse.

    Raised rather than skipped: a file the linter cannot read is a function it
    cannot classify, and skipping silently produces a clean run that means
    nothing.
    """


@dataclass(frozen=True, slots=True)
class Finding:
    """One unclassified function, and where a decorator would go."""

    path: Path
    function: str
    #: 1-indexed line a decorator should be inserted *above*. This is the first
    #: existing decorator's line when there are any, not the ``def`` line, so a
    #: new decorator does not land inside an existing stack.
    insert_line: int
    #: The literal leading whitespace of that line, copied rather than counted.
    #: Storing a count and re-emitting it as spaces destroyed the difference
    #: between a tab and a space, so a tab-indented class produced
    #: "inconsistent use of tabs and spaces" and had to be refused.
    indent: str
    #: Last line of the function, so a ratchet can ask whether any of it was
    #: touched. ``insert_line`` alone would only answer "was the signature
    #: edited", and a payment body rewritten under an untouched signature is
    #: exactly the change that most needs classifying.
    end_line: int
    #: Which signal fired. Reported so a developer can judge a false positive.
    signal: str
    #: What --fix writes after ``FinancialCategory.``. Always
    #: ``PROPOSED_CATEGORY``; it stays a field rather than a constant so the
    #: write path has one place to validate and the fifth guard has something
    #: to reject.
    category: str
    #: Which token families matched -- "money", "pii", or both. Recorded where
    #: the parameter names are in hand. Not a category and not a legal claim:
    #: it is a record of which words appeared, and the input the
    #: candidate-provision mapping will read.
    vocabularies: tuple[str, ...]
    #: SHA-256 of the source this finding describes. Comparing line numbers and
    #: names is not enough: a concurrent save that preserves both -- an edit
    #: inside a function body -- would otherwise be silently overwritten.
    source_sha: str


def _words(name: str) -> set[str]:
    """Split an identifier into whole words.

    Substring matching flagged ``expand`` and ``japan_locale`` for containing
    "pan", ``discard`` for "card", and -- worse -- labelled a template helper as
    GDPR personal-data processing. A linter that does that is the one that gets
    switched off.
    """
    parts = re.split(r"[^a-z0-9]+", re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower())
    return {p for p in parts if p}


#: Module names that can legitimately provide ``overload``.
_TYPING_MODULES = frozenset({"typing", "typing_extensions"})


def _word_sequence(name: str) -> list[str]:
    """The identifier's words in reading order, for multi-word token matching."""
    split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()
    return [p for p in re.split(r"[^a-z0-9]+", split) if p]


def _matches_tokens(name: str, tokens: frozenset[str]) -> bool:
    """True if the identifier's words contain a token.

    A multi-word token must appear as a run of whole words. The previous
    substring test matched any identifier merely *containing* the characters,
    so the token "national_id" fired on "international_ideas" -- a false
    positive that would have inserted a GDPR category on unrelated code. The
    companion branch tested a sorted-and-rejoined form, which can never equal
    a token written in reading order, so it was dead.
    """
    words = _words(name)
    if words & tokens:
        return True
    sequence = _word_sequence(name)
    for token in tokens:
        parts = [p for p in token.split("_") if p]
        if len(parts) < 2:
            continue
        width = len(parts)
        if any(
            sequence[i : i + width] == parts
            for i in range(len(sequence) - width + 1)
        ):
            return True
    return False


def _annotation_names(node: ast.AST) -> set[str]:
    out: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            out.add(child.id)
        elif isinstance(child, ast.Attribute):
            out.add(child.attr)
        elif isinstance(child, ast.Constant) and isinstance(child.value, str):
            # A quoted forward reference -- common in code avoiding a runtime
            # import -- is a Constant, so the type signal never saw it.
            out.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", child.value))
    return out


def _classifier_aliases(tree: ast.AST) -> set[str]:
    """Every local name bound to the classification decorator, aliases included.

    ``from ... import regulated_tool as regulated`` was unrecognised, so --fix
    stacked a second decorator on an already-classified function. The old
    substring test over ``ast.dump`` also failed the other way: a docstring
    reading "use regulated_tool instead" made an unclassified function look
    classified, silently exempting it.
    """
    aliases = {_DECORATOR_MARKER}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == _DECORATOR_MARKER:
                    aliases.add(alias.asname or alias.name)
    return aliases


def _decorator_name(node: ast.expr) -> str | None:
    """The bare name a decorator expression resolves to, or None."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_classified(
    node: ast.FunctionDef | ast.AsyncFunctionDef, aliases: set[str]
) -> bool:
    return any(_decorator_name(d) in aliases for d in node.decorator_list)


def _module_reaches_bank(dotted: str) -> bool:
    return bool(set(dotted.split(".")) & BANK_CLIENT_MARKERS)


def _reaches_bank(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # `from finagent_safeguard import bank_client` puts the module in
            # the NAME, not in node.module -- and `from . import bank_client`
            # has node.module of None entirely. Both were invisible.
            if node.module and _module_reaches_bank(node.module):
                return True
            if any(_module_reaches_bank(a.name) for a in node.names):
                return True
        elif isinstance(node, ast.Import):
            if any(_module_reaches_bank(a.name) for a in node.names):
                return True
    return False


#: What the linter writes. Never a category.
#:
#: The tool finds the function; it does not decide which regulation applies.
#: Trained annotators reach a Krippendorff's alpha of 0.251 on that six-way
#: judgement and the best of eleven published methods on the nearest benchmark
#: scored 5.75% macro-F1, so a category written here would be confidently
#: wrong -- and a false compliance claim sitting in a developer's source is
#: worse than an admission that the tool does not know.
PROPOSED_CATEGORY: Final[str] = "REVIEW_REQUIRED"


def _vocabularies(names: set[str]) -> tuple[str, ...]:
    """Which token families the identifiers matched, in a stable order.

    This replaced ``_category_for``, which returned a single category and
    checked the person-words first, so anything matching both resolved to GDPR
    and payments never won. That tie-break fired on the most regulated
    functions precisely because they carry both -- Saleor's
    ``capture(payment, amount, customer_id)`` was filed as personal data rather
    than payment.

    A record of which words matched is not a legal conclusion, so both can be
    true at once and nothing has to be invented to break the tie. It is also
    the input the candidate-provision mapping needs: money words point at the
    payment and transfer-of-funds provisions, person words at the data
    protection ones.
    """
    found: list[str] = []
    if any(_matches_tokens(name, MONEY_TOKENS) for name in names):
        found.append("money")
    if any(_matches_tokens(name, PII_TOKENS) for name in names):
        found.append("pii")
    return tuple(found)


def _overload_names(tree: ast.AST) -> set[str]:
    """Local names that really are ``typing.overload``.

    Matching the bare name "overload" meant any unrelated decorator of that
    name suppressed the finding -- a silent exemption a developer could create
    by accident. A dotted ``@typing.overload`` is handled separately, by
    attribute, since ``import typing`` binds only the module name.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in _TYPING_MODULES:
            for alias in node.names:
                if alias.name == "overload":
                    names.add(alias.asname or "overload")
    return names


def _is_runtime_discarded(
    node: ast.FunctionDef | ast.AsyncFunctionDef, overload_names: set[str]
) -> bool:
    """True for a stub whose decorator never runs.

    ``typing.overload`` discards the stub at runtime, so a decorator inserted
    there never executes -- and the file then re-scans clean, reporting
    classified while the live implementation is bare. The tool would be
    manufacturing the exact invisible false negative it exists to prevent.
    """
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Name) and target.id in overload_names:
            return True
        if (
            isinstance(target, ast.Attribute)
            and target.attr == "overload"
            and isinstance(target.value, ast.Name)
            and target.value.id in _TYPING_MODULES
        ):
            return True
    return False


def _signal_for(
    node: ast.FunctionDef | ast.AsyncFunctionDef, *, bank: bool
) -> tuple[str, tuple[str, ...]] | None:
    """Which signal fires, and which token families matched. None if neither."""
    args = node.args
    all_args = [
        *args.posonlyargs, *args.args, *args.kwonlyargs,
        *([args.vararg] if args.vararg else []),
        *([args.kwarg] if args.kwarg else []),
    ]
    names = {node.name, *(a.arg for a in all_args)}
    if any(_matches_tokens(name, NAME_TOKENS) for name in names):
        return "name", _vocabularies(names)

    annotated = [a.annotation for a in all_args if a.annotation]
    if node.returns is not None:
        annotated.append(node.returns)
    for annotation in annotated:
        if _annotation_names(annotation) & RISKY_ANNOTATIONS:
            return "type", _vocabularies(names)

    if bank and not node.name.startswith("_"):
        return "bank_client_import", _vocabularies(names)
    return None


def scan_source(source: str, path: Path) -> list[Finding]:
    """Findings for one module's source. Raises on unparseable input."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise UnparseableSource(f"{path}: {exc}") from exc

    bank = _reaches_bank(tree)
    digest = hashlib.sha256(source.encode()).hexdigest()
    # The same line model as the write path. This used to be
    # source.splitlines(), which disagrees with ast on form feeds and line
    # separators -- so the indent was read off the wrong line and --fix
    # refused the file permanently, blaming the edit rather than the scan.
    lines = _split_source_lines(source)
    findings: list[Finding] = []

    aliases = _classifier_aliases(tree)
    overload_names = _overload_names(tree)

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not _is_classified(child, aliases) and not _is_runtime_discarded(
                    child, overload_names
                ):
                    matched = _signal_for(child, bank=bank)
                    if matched is not None:
                        signal, vocabularies = matched
                        line = min(
                            [child.lineno, *(d.lineno for d in child.decorator_list)]
                        )
                        text = lines[line - 1]
                        leading = text[: len(text) - len(text.lstrip(" \t\x0c"))]
                        findings.append(
                            Finding(
                                path=path,
                                function=f"{prefix}{child.name}",
                                insert_line=line,
                                end_line=child.end_lineno or line,
                                indent=leading,
                                signal=signal,
                                category=PROPOSED_CATEGORY,
                                vocabularies=vocabularies,
                                source_sha=digest,
                            )
                        )
                walk(child, f"{prefix}{child.name}.")
            else:
                # Descend into if / try / with / for / match bodies. Stopping at
                # class and function children left a def behind a feature flag or
                # inside try/except ImportError invisible to ALL THREE signals,
                # the structural backstop included -- ordinary code, not
                # adversarial, and the exact false negative the module docstring
                # calls the dangerous direction.
                walk(child, prefix)

    walk(tree, "")
    return findings


def read_source(path: Path) -> str:
    """Read a source file, or refuse it clearly.

    ``utf-8-sig`` so a byte-order mark is stripped rather than reaching the
    parser: a BOM-prefixed file is legal Python, and raising UnparseableSource
    on it hard-fails CI on a file that is fine. A genuinely non-UTF-8 file is
    refused by name instead of escaping as a bare UnicodeDecodeError.
    """
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RefusedTarget(
            f"{path} is not UTF-8 ({exc.reason}); this tool does not re-encode "
            "source files"
        ) from exc


def scan_file(path: Path) -> list[Finding]:
    return scan_source(read_source(path), path)


# ---------------------------------------------------------------------------
# The write path.
#
# ``ast`` finds; plain text writes. Never ``ast.unparse()``: it reformats the
# whole file and deletes every comment, which for a tool that edits a
# developer's source is a one-use mistake.
#
# Six properties make this correct rather than merely working, each with a test:
# edits applied bottom-up so earlier inserts do not shift later line numbers;
# indentation matched to the target line; the modified source re-parsed and its
# function set compared before anything is written; the file re-scanned to catch
# a change since the findings were taken; an atomic temp-and-rename write; and
# idempotency.
# ---------------------------------------------------------------------------

DECORATOR_IMPORT: Final = "from finagent_safeguard.core.decorators import regulated_tool"
CATEGORY_IMPORT: Final = (
    "from finagent_safeguard.taxonomy.policies import FinancialCategory"
)

#: Which category each signal suggests. The linter proposes; it does not decide.
#: Every inserted line carries a TODO so the developer must look at it, which is


#: Every name the tool may legally write after ``FinancialCategory.``. Read
#: from the enum rather than restated, so a member added or renamed there
#: cannot drift out of step with what the linter will emit.
_VALID_CATEGORIES: frozenset[str] = frozenset(m.name for m in FinancialCategory)


class UnsafeEdit(Exception):
    """The edited source no longer defines the same functions. Nothing written."""


class StaleFindings(Exception):
    """The file changed between being scanned and being fixed."""


@dataclass(frozen=True, slots=True)
class FixResult:
    inserted: int
    functions: list[str]


def _render_annotation(finding: Finding) -> list[str]:
    """The decorator line plus the candidate provisions, unindented.

    Several lines rather than one, because a bare category name told a
    developer nothing they could act on. It used to write a guessed category
    with "confirm the category" appended, which got the emphasis exactly wrong:
    the guess read as the answer and the confirmation as paperwork. Measured,
    that guess was inverted on real code -- a GUI focus handler filed as a
    payment, real ISO 20022 payment builders not flagged at all.

    Now it names the evidence, lists the provisions worth reading, and asks for
    the one judgement a static tool cannot make. The candidates are pinned
    text: each is committed under ``corpus/`` with a SHA-256 and audited by
    ``tools/check_entailment.py``.

    Comments sit between the decorator and the ``def``, which is legal Python
    and keeps the reason adjacent to the thing it explains. The write guards are
    unaffected: extra comment lines are still a pure insertion and add no
    functions.
    """
    matched = "+".join(finding.vocabularies) or finding.signal
    out = [
        f"@regulated_tool(FinancialCategory.{finding.category})",
        f"# finagent-lint: {matched} identifiers, {finding.signal} signal.",
    ]
    found = candidates_for(finding.vocabularies)
    if found:
        out.append("#   candidates to read before deciding:")
        for candidate in found:
            out.append(f"#     {cite(candidate.provision)} - {candidate.requires}")
    else:
        out.append(
            "#   no candidate provision: the signal is reachability alone, "
            "which evidences no particular duty."
        )
    out.append(
        f"# Replace {finding.category} with the category you have confirmed."
    )
    return out


def _import_insert_index(source: str, lines: list[str]) -> int:
    """Where new imports may legally go: after any shebang, encoding cookie,
    module docstring, and every ``__future__`` import.

    Derived from the parsed tree rather than by hunting for the substring
    ``__future__``. The previous version took the LAST line mentioning it
    anywhere -- a comment was enough -- and placed the new imports BELOW the
    decorator that uses them. The file still parsed and still defined the same
    functions, so both write-path guards reported success, and importing it
    raised NameError.
    """
    index = 0
    # A shebang must stay on line 1; a PEP 263 cookie within the first two.
    if lines and lines[0].startswith("#!"):
        index = 1
    for i in range(index, min(index + 2, len(lines))):
        if re.match(r"^#.*coding[:=]", lines[i]):
            index = i + 1

    body = ast.parse(source).body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
        and body[0].end_lineno
    ):
        index = max(index, body[0].end_lineno)
    for node in body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            index = max(index, node.end_lineno or node.lineno)
    return index

def _split_source_lines(text: str) -> list[str]:
    """Split source into lines the way Python's tokenizer does, keeping endings.

    The tokenizer treats ``\\n``, ``\\r\\n`` and ``\\r`` as line breaks and nothing
    else, which is exactly what ``io.StringIO(newline="")`` reproduces.

    ``str.splitlines()`` is NOT equivalent and is a trap here: it also splits on
    ``\\x0b \\x0c \\x1c \\x1d \\x1e \\x85 \\u2028 \\u2029``. A form feed in a banner
    comment, or a line separator inside a string literal, would make the line
    array disagree with ``ast``'s line numbers -- turning a loud, frequent bug
    into a silent, rare one. CPython hit this too: ``ast._splitlines_no_ff``
    exists for the same reason.
    """
    return io.StringIO(text, newline="").readlines() or [""]


def _terminator(line: str) -> str:
    """The line ending of one line, or empty for a file with no final newline."""
    for candidate in ("\r\n", "\n", "\r"):
        if line.endswith(candidate):
            return candidate
    return ""


def _missing_imports(source: str) -> list[str]:
    """Which import lines the module still needs, decided from the parsed tree.

    ``if line not in text`` is a substring test, and a module docstring carrying
    the worked example -- precisely what the gate's error message tells
    developers to write -- made it a hit. Neither import was inserted, the
    decorator was, the result parsed with the same function set, both write
    guards reported success, and importing the file raised NameError. Same bug
    class as the one fixed in ``_import_insert_index``, five lines below it,
    and missed.
    """
    # Only direct children of the module body. ``ast.walk`` descends into
    # ``if TYPE_CHECKING:`` blocks and function bodies, where an import is not
    # bound at runtime -- so the tool skipped the import, inserted the decorator,
    # and produced a file that parsed, defined the same functions, passed all
    # four write guards, and raised NameError on import.
    #
    # Conservative on purpose: a module-level ``try/except ImportError`` IS
    # bound at runtime and will be missed here, producing a harmless duplicate
    # import. A redundant import is a lint warning; a missing one is a broken
    # module.
    bound: set[str] = set()
    for node in ast.parse(source).body:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound.add(alias.asname or alias.name)
    needed = []
    if _DECORATOR_MARKER not in bound:
        needed.append(DECORATOR_IMPORT)
    if "FinancialCategory" not in bound:
        needed.append(CATEGORY_IMPORT)
    return needed


def decorators_by_qualname(source: str) -> dict[str, tuple[str, ...]]:
    """Map every function to the decorators it carries, keyed by dotted path.

    This is what the post-edit check compares. The previous version compared a
    *set of bare function names*, which adding a decorator never changes -- so
    it returned the same answer whatever the edit did, as long as the file still
    parsed. It could not see a decorator landing on the wrong function.

    Keyed by qualified name because a module-level ``transfer`` and a
    ``Payments.transfer`` method share a bare name, and collapsing them makes
    the check half-blind again.
    """
    out: dict[str, tuple[str, ...]] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{prefix}{child.name}"
                out[qualname] = tuple(ast.unparse(d) for d in child.decorator_list)
                walk(child, f"{qualname}.")
            else:
                walk(child, prefix)

    walk(ast.parse(source), "")
    return out


def _only_insertions(before: bytes, after: bytes) -> list[str]:
    """Report any original byte deleted or replaced, rather than merely added.

    Some corruption is invisible to the parser. A dropped byte-order mark leaves
    a file that parses, defines the same functions, and is three bytes shorter
    than the developer left it. Only a byte comparison sees that.
    """
    # Compared as LINES, not as individual bytes. SequenceMatcher over bytes has
    # ~256 distinct symbols, so its index degenerates and the cost is
    # super-quadratic: 40 functions took 1.4s and a 50 KB module would take
    # minutes, which is how a --fix flag gets abandoned. Splitting into lines
    # first is ~100x faster and detects exactly the same deletions, because any
    # lost byte changes the line that held it.
    # Strip the marker from both sides first and check it separately. An
    # insertion lands between the BOM and the first line's text, which makes
    # line 1 look "replaced" even though no byte was lost.
    BOM = b"\xef\xbb\xbf"
    problems = []
    if before.startswith(BOM) != after.startswith(BOM):
        problems.append("the byte-order mark was added or removed")
    before_lines = before.removeprefix(BOM).splitlines(keepends=True)
    after_lines = after.removeprefix(BOM).splitlines(keepends=True)
    for tag, i1, i2, _j1, _j2 in difflib.SequenceMatcher(
        None, before_lines, after_lines, autojunk=False
    ).get_opcodes():
        if tag in ("delete", "replace"):
            problems.append(f"{tag} of original bytes {b''.join(before_lines[i1:i2])!r}")
    return problems


@dataclass(frozen=True, slots=True)
class FixPlan:
    """What --fix would do, worked out without touching the file."""

    path: Path
    new_bytes: bytes
    diff: str
    functions: list[str]

    @property
    def inserted(self) -> int:
        return len(self.functions)


def plan_fix(path: Path, findings: list[Finding]) -> FixPlan:
    """Compute the edit and verify it, writing nothing.

    Separated from the write so a developer can be shown a diff and approve the
    exact bytes that will land. ``apply_fix`` writes this plan and nothing else.
    """
    if path.is_symlink():
        raise RefusedTarget(
            f"{path} is a symlink. Rewriting it would replace the link with a "
            "regular file and leave the real source untouched; edit the target."
        )

    raw = path.read_bytes()
    bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
    text = read_source(path)
    fresh = scan_source(text, path)

    digest = hashlib.sha256(text.encode()).hexdigest()
    stale = [f.function for f in findings if f.source_sha != digest]
    if stale:
        raise StaleFindings(
            f"{path} changed since it was scanned (findings for {stale} describe "
            "different content). Re-scan rather than writing edits against a file "
            "that no longer exists."
        )
    known = {(f.function, f.insert_line) for f in fresh}
    unknown = [f.function for f in findings if (f.function, f.insert_line) not in known]
    if unknown:
        raise StaleFindings(f"{path}: {unknown} are not findings for this file")

    if not findings:
        return FixPlan(path=path, new_bytes=raw, diff="", functions=[])

    # Split the way Python's own tokenizer does, and keep each line's ending.
    #
    # The previous version guessed ONE newline for the whole file and split on
    # it. On a file with mixed endings -- post-merge, codegen, a Windows-authored
    # patch -- every differing line above the target shifted the insertion point,
    # so the decorator landed on the following function while the intended one
    # stayed bare. See _split_source_lines for why str.splitlines() is not the
    # fix either.
    lines = _split_source_lines(text)
    for finding in sorted(findings, key=lambda f: f.insert_line, reverse=True):
        # Borrow the terminator of the line being pushed down, so the inserted
        # line matches its neighbours rather than a file-wide guess.
        term = _terminator(lines[finding.insert_line - 1])
        block = [
            finding.indent + line + term for line in _render_annotation(finding)
        ]
        lines[finding.insert_line - 1 : finding.insert_line - 1] = block
    needed = _missing_imports(text)
    if needed:
        at = _import_insert_index(text, lines)
        term = _terminator(lines[at]) if at < len(lines) else "\n"
        lines[at:at] = [line + term for line in needed]
    # Join with nothing. Every original byte is carried through untouched,
    # because no line was ever stripped of its ending.
    modified = "".join(lines)

    try:
        after = decorators_by_qualname(modified)
    except SyntaxError as exc:
        raise UnsafeEdit(f"{path}: edit produced unparseable source: {exc}") from exc

    before = decorators_by_qualname(text)
    if set(before) != set(after):
        raise UnsafeEdit(
            f"{path}: the set of functions changed "
            f"({sorted(set(after) - set(before))} added, "
            f"{sorted(set(before) - set(after))} removed); nothing written"
        )

    intended = sorted(f.function for f in findings)
    changed = sorted(q for q in before if before[q] != after[q])
    if changed != intended:
        raise UnsafeEdit(
            f"{path}: decorator landed on {changed or 'nothing'}, expected "
            f"exactly {intended}. Nothing written."
        )

    # Fifth guard: the decorator we are about to write must reference a real
    # enum member.
    #
    # The other four check that we did not damage the developer's file. None of
    # them checks that what we *added* works. A category not present on
    # FinancialCategory produces a file that parses, keeps every function, is a
    # pure insertion, and raises AttributeError the moment anyone imports it --
    # and all four guards reported success while it did so.
    #
    # Deliberately static. Verifying importability properly would mean executing
    # the module, which means executing arbitrary developer code inside a
    # linter, which is not a trade a linter gets to make. So the check is
    # narrowed to the one thing the tool itself emits and therefore owns.
    unknown_categories = sorted(
        {f.category for f in findings if f.category not in _VALID_CATEGORIES}
    )
    if unknown_categories:
        raise UnsafeEdit(
            f"{path}: would write FinancialCategory.{unknown_categories[0]}, which "
            f"is not a member of FinancialCategory. Nothing written."
        )

    new_bytes = bom + modified.encode()
    envelope = _only_insertions(raw, new_bytes)
    if envelope:
        raise UnsafeEdit(f"{path}: original bytes were altered: {envelope}")

    diff = "".join(
        difflib.unified_diff(
            _split_source_lines(raw.decode("utf-8-sig")),
            _split_source_lines(new_bytes.decode("utf-8-sig")),
            fromfile=f"a/{path.name}",
            tofile=f"b/{path.name}",
        )
    )
    return FixPlan(
        path=path, new_bytes=new_bytes, diff=diff,
        functions=[f.function for f in findings],
    )


def apply_fix(path: Path, findings: list[Finding]) -> FixResult:
    """Write the verified plan. Atomically, or not at all."""
    if not os.access(path, os.W_OK):
        raise RefusedTarget(
            f"{path} is read-only. An atomic rename only needs a writable "
            "directory, so this would have succeeded silently -- and read-only "
            "usually means do not touch."
        )
    plan = plan_fix(path, findings)
    if not plan.functions:
        return FixResult(inserted=0, functions=[])

    mode = path.stat().st_mode
    tmp = path.with_name(f"{path.name}.{os.getpid()}.finagent-tmp")
    try:
        tmp.write_bytes(plan.new_bytes)
        os.chmod(tmp, stat.S_IMODE(mode))
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)

    return FixResult(inserted=plan.inserted, functions=plan.functions)


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------
#: Signals whose measured precision is too poor to fail a build on by default.
#:
#: The bank-client-import backstop fires on any function in a module that can
#: reach the bank client, which is deliberately broad -- it is the net that
#: catches functions no naming convention would reveal. Measured on 155
#: top-level stdlib modules it produced 51 flags and 0 correct ones, so a build
#: that fails on it fails constantly and gets switched off. Reported always,
#: counted towards the exit status only under --strict.
ADVISORY_SIGNALS: Final[frozenset[str]] = frozenset({"bank_client_import"})

_UNRESOLVED = "REVIEW_REQUIRED"


def unresolved_functions(source: str) -> list[str]:
    """Dotted names of functions carrying the linter's refusal to classify.

    Resolved through the AST rather than by searching the text for
    "REVIEW_REQUIRED", because a substring test over source has been the cause
    of three separate defects in this file -- a docstring mentioning an import
    suppressed it, a token matched the middle of an unrelated identifier, and a
    comment counted as a decorator.
    """
    out: list[str] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in child.decorator_list:
                    if not isinstance(decorator, ast.Call):
                        continue
                    for argument in decorator.args:
                        if (
                            isinstance(argument, ast.Attribute)
                            and argument.attr == _UNRESOLVED
                        ):
                            out.append(f"{prefix}{child.name}")
                            break
                walk(child, f"{prefix}{child.name}.")

    walk(ast.parse(source), "")
    return out


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def changed_lines(base: str, paths: list[str]) -> dict[Path, set[int]] | None:
    """Line numbers touched since ``base``, per file. None if git cannot answer.

    This is what makes the gate adoptable. A linter run over a brownfield
    codebase flags every pre-existing regulated function, exits non-zero
    forever, and gets switched off -- and then it is protecting nothing. The
    measured version of that: 2,678 of 70,826 GitHub Python repositories carry
    any PEP 484 annotation, while Dropbox reached roughly four million
    annotated lines, and what they credit is raising strictness *for new code*
    rather than demanding the whole tree at once.

    Returns None rather than an empty mapping when git fails, because the two
    mean opposite things: "nothing changed" should suppress every finding,
    while "I cannot tell what changed" must suppress none.
    """
    import subprocess

    try:
        done = subprocess.run(
            ["git", "diff", "--unified=0", base, "--", *paths],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None

    touched: dict[Path, set[int]] = {}
    current: Path | None = None
    for line in done.stdout.splitlines():
        if line.startswith("+++ b/"):
            current = Path(line[6:])
            touched.setdefault(current, set())
            continue
        if current is None:
            continue
        found = _HUNK.match(line)
        if found:
            start = int(found.group(1))
            count = int(found.group(2) or 1)
            # A pure deletion reports a count of 0 at the line it was removed
            # from. Nothing there now needs classifying, so it adds no lines.
            touched[current].update(range(start, start + count))
    return touched


def _touched(finding: Finding, touched: dict[Path, set[int]]) -> bool:
    """Did the diff reach any line of this function?

    Resolved against both the path as given and its absolute form, because git
    reports paths relative to the repository root while the linter is handed
    whatever the caller typed.
    """
    for key in (finding.path, Path(*finding.path.parts[-len(finding.path.parts):])):
        lines = touched.get(key)
        if lines is None:
            for candidate, found in touched.items():
                if str(finding.path).endswith(str(candidate)):
                    lines = found
                    break
        if lines is not None:
            return any(
                line in lines
                for line in range(finding.insert_line, finding.end_line + 1)
            )
    return False


def _python_files(paths: list[str]) -> tuple[list[Path], list[str]]:
    """Expand arguments into files, and report what could not be used.

    Directories are walked; anything else is taken literally so that a
    misspelled path is an error rather than a silently empty run -- a linter
    that exits 0 because it scanned nothing is worse than one that fails.
    """
    found: list[Path] = []
    problems: list[str] = []
    for raw in paths:
        target = Path(raw)
        if target.is_dir():
            found.extend(sorted(p for p in target.rglob("*.py")))
        elif target.is_file():
            found.append(target)
        else:
            problems.append(f"{raw}: no such file or directory")
    return found, problems


def _describe(finding: Finding) -> str:
    advisory = " (advisory)" if finding.signal in ADVISORY_SIGNALS else ""
    return (
        f"  {finding.path}:{finding.insert_line}: {finding.function} "
        f"[{finding.signal}]{advisory}"
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``finagent-lint``.

    Exit status is the whole interface as far as CI is concerned:

      0  nothing to do
      1  a regulated function is unclassified, or carries REVIEW_REQUIRED
      2  the run itself failed -- bad path, unreadable or unparseable file

    2 is kept distinct from 1 because they mean opposite things to a pipeline.
    1 says the tool worked and found something; 2 says the tool did not work,
    and treating that as "clean" is how a gate silently stops gating.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="finagent-lint",
        description=(
            "Find functions handling money or personal data that can reach a "
            "bank client, and flag them for classification. The tool proposes; "
            "a developer confirms."
        ),
    )
    parser.add_argument("paths", nargs="*", default=["."], help="files or directories")
    parser.add_argument(
        "--fix", action="store_true", help="insert the flag (writes to files)"
    )
    parser.add_argument(
        "--diff-only",
        action="store_true",
        help="print the edit --fix would make, and write nothing",
    )
    parser.add_argument(
        "--since",
        metavar="REF",
        help=(
            "only count findings in code touched since REF (e.g. origin/main). "
            "Pre-existing findings are still reported, never silently dropped."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=f"also fail on advisory signals ({', '.join(sorted(ADVISORY_SIGNALS))})",
    )
    args = parser.parse_args(argv)

    if args.fix and args.diff_only:
        parser.error("--fix and --diff-only are contradictory; pick one")

    touched: dict[Path, set[int]] | None = None
    if args.since:
        touched = changed_lines(args.since, args.paths or ["."])
        if touched is None:
            # Not a warning to be skimmed past: without a diff the ratchet
            # cannot tell new code from old, and silently counting everything
            # would turn an adoption aid into a surprise wall of failures.
            print(
                f"error: cannot diff against {args.since!r}; is it a valid git "
                "ref in this repository?",
                file=sys.stderr,
            )
            return 2

    files, problems = _python_files(args.paths or ["."])
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if problems:
        return 2

    actionable = 0
    advisory = 0
    pre_existing = 0
    unresolved_total = 0
    unresolved_pre_existing = 0
    written = 0
    failures: list[str] = []

    for path in files:
        try:
            source = read_source(path)
        except (OSError, UnparseableSource) as exc:
            failures.append(f"{path}: {exc}")
            continue

        try:
            findings = scan_source(source, path)
            still_open = unresolved_functions(source)
        except (UnparseableSource, SyntaxError) as exc:
            # UnparseableSource is a plain Exception, not a SyntaxError, so
            # catching only the latter let it escape and crash the process with
            # a traceback. Python exits 1 on an uncaught exception, so the run
            # looked like "the tool worked and found something" when in fact it
            # had fallen over -- the one confusion the 1/2 split exists to
            # prevent.
            failures.append(f"{path}: {exc}")
            continue

        for name in still_open:
            # An unresolved flag outside the diff is somebody else's backlog.
            # It is printed either way; only whether it fails the build changes.
            outside = touched is not None and not any(
                _touched(f, touched) for f in findings if f.function == name
            )
            if outside:
                unresolved_pre_existing += 1
                print(f"  {path}: {name} carries {_UNRESOLVED} (pre-existing)")
            else:
                unresolved_total += 1
                print(f"  {path}: {name} carries {_UNRESOLVED}; confirm the category")

        if not findings:
            continue

        for finding in findings:
            if touched is not None and not _touched(finding, touched):
                pre_existing += 1
                print(_describe(finding) + " (pre-existing)")
                continue
            if finding.signal in ADVISORY_SIGNALS:
                advisory += 1
            else:
                actionable += 1
            print(_describe(finding))

        if args.diff_only:
            try:
                plan = plan_fix(path, findings)
            except (RefusedTarget, StaleFindings, UnsafeEdit) as exc:
                failures.append(f"{path}: {exc}")
                continue
            print(plan.diff, end="" if plan.diff.endswith("\n") else "\n")
        elif args.fix:
            try:
                result = apply_fix(path, findings)
            except (RefusedTarget, StaleFindings, UnsafeEdit) as exc:
                failures.append(f"{path}: {exc}")
                continue
            written += result.inserted
            print(f"  fixed {path}: {result.inserted} flag(s) inserted")

    for failure in failures:
        print(f"error: {failure}", file=sys.stderr)

    counted = actionable + (advisory if args.strict else 0)
    summary = (
        f"\n{len(files)} file(s) scanned. "
        f"{actionable} actionable, {advisory} advisory, "
        f"{unresolved_total} unresolved."
    )
    if args.fix:
        summary += f" {written} flag(s) written."
    if touched is not None:
        # Counted and named. A ratchet that hides the backlog is a blindfold,
        # and the number is the only honest measure of how much there is to do.
        summary += (
            f"\n{pre_existing} finding(s) and {unresolved_pre_existing} "
            f"unresolved flag(s) are outside the diff against {args.since} "
            "and do not fail this run. Drop --since to see the whole tree."
        )
    print(summary)

    if failures:
        # A file the tool could not read or parse is not a clean file. Reporting
        # 0 here would let a syntax error disable the gate for that file.
        return 2
    if args.fix:
        # After a successful --fix every inserted flag is unresolved by
        # construction, so the developer still has work to do and the build must
        # not go green on the strength of the tool having written something.
        return 1 if (written or unresolved_total or counted) else 0
    return 1 if (counted or unresolved_total) else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
