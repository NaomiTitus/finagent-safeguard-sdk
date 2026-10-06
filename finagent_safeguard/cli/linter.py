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
import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final

__all__ = [
    "Finding",
    "FixResult",
    "RefusedTarget",
    "StaleFindings",
    "UnparseableSource",
    "UnsafeEdit",
    "apply_fix",
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
    #: Leading spaces of that line, so an inserted decorator lines up with a
    #: method inside a class.
    indent: int
    #: Which signal fired. Reported so a developer can judge a false positive.
    signal: str
    #: The category --fix would insert. Derived here, where the parameter names
    #: are in hand, rather than re-guessed from the function name alone.
    category: str
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


def _matches_tokens(name: str, tokens: frozenset[str]) -> bool:
    words = _words(name)
    if words & tokens:
        return True
    # Multi-word tokens such as "national_id" are matched against the joined form.
    joined = "_".join(sorted(words))
    return any("_" in token and token in name.lower() for token in tokens) or joined in tokens


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
        if isinstance(node, ast.ImportFrom) and node.module:
            if _module_reaches_bank(node.module):
                return True
        elif isinstance(node, ast.Import):
            if any(_module_reaches_bank(a.name) for a in node.names):
                return True
    return False


def _category_for(names: set[str]) -> str:
    """Payments unless the identifiers point at a person."""
    if any(_matches_tokens(n, PII_TOKENS) for n in names):
        return "GDPR_PII_PROCESSING"
    return "PSD2_PAYMENT_EXECUTION"


def _is_runtime_discarded(
    node: ast.FunctionDef | ast.AsyncFunctionDef, aliases: set[str]
) -> bool:
    """True for a stub whose decorator never runs.

    ``typing.overload`` discards the stub at runtime, so a decorator inserted
    there never executes -- and the file then re-scans clean, reporting
    classified while the live implementation is bare. The tool would be
    manufacturing the exact invisible false negative it exists to prevent.
    """
    return any(_decorator_name(d) == "overload" for d in node.decorator_list)


def _signal_for(
    node: ast.FunctionDef | ast.AsyncFunctionDef, *, bank: bool
) -> tuple[str, str] | None:
    """Which signal fires, and which category it implies. None if neither."""
    args = node.args
    all_args = [
        *args.posonlyargs, *args.args, *args.kwonlyargs,
        *([args.vararg] if args.vararg else []),
        *([args.kwarg] if args.kwarg else []),
    ]
    names = {node.name, *(a.arg for a in all_args)}
    if any(_matches_tokens(name, NAME_TOKENS) for name in names):
        return "name", _category_for(names)

    annotated = [a.annotation for a in all_args if a.annotation]
    if node.returns is not None:
        annotated.append(node.returns)
    for annotation in annotated:
        if _annotation_names(annotation) & RISKY_ANNOTATIONS:
            return "type", _category_for(names)

    if bank and not node.name.startswith("_"):
        return "bank_client_import", _category_for(names)
    return None


def scan_source(source: str, path: Path) -> list[Finding]:
    """Findings for one module's source. Raises on unparseable input."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise UnparseableSource(f"{path}: {exc}") from exc

    bank = _reaches_bank(tree)
    digest = hashlib.sha256(source.encode()).hexdigest()
    lines = source.splitlines()
    findings: list[Finding] = []

    aliases = _classifier_aliases(tree)

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not _is_classified(child, aliases) and not _is_runtime_discarded(
                    child, aliases
                ):
                    matched = _signal_for(child, bank=bank)
                    if matched is not None:
                        signal, category = matched
                        line = min(
                            [child.lineno, *(d.lineno for d in child.decorator_list)]
                        )
                        text = lines[line - 1]
                        findings.append(
                            Finding(
                                path=path,
                                function=f"{prefix}{child.name}",
                                insert_line=line,
                                indent=len(text) - len(text.lstrip()),
                                signal=signal,
                                category=category,
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


class UnsafeEdit(Exception):
    """The edited source no longer defines the same functions. Nothing written."""


class StaleFindings(Exception):
    """The file changed between being scanned and being fixed."""


@dataclass(frozen=True, slots=True)
class FixResult:
    inserted: int
    functions: list[str]


def _render_decorator(finding: Finding) -> str:
    return (
        f"@regulated_tool(FinancialCategory.{finding.category})"
        f"  # TODO(finagent): inserted by linter from the {finding.signal!r} "
        "signal; confirm the category"
    )


def _function_names(source: str, path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.add(node.name)
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
    bound: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound.add(alias.asname or alias.name)
    needed = []
    if _DECORATOR_MARKER not in bound:
        needed.append(DECORATOR_IMPORT)
    if "FinancialCategory" not in bound:
        needed.append(CATEGORY_IMPORT)
    return needed


def apply_fix(path: Path, findings: list[Finding]) -> FixResult:
    """Insert a classification for each finding. Writes atomically or not at all."""
    if path.is_symlink():
        raise RefusedTarget(
            f"{path} is a symlink. Rewriting it would replace the link with a "
            "regular file and leave the real source untouched; edit the target."
        )
    if not os.access(path, os.W_OK):
        raise RefusedTarget(
            f"{path} is read-only. An atomic rename only needs a writable "
            "directory, so this would have succeeded silently -- and read-only "
            "usually means do not touch."
        )

    mode = path.stat().st_mode
    # Read once, and derive the freshness check from THAT text. Reading twice
    # let a concurrent save land between them: the check re-read the new
    # content and passed, while the edit applied to the stale copy and the
    # developer's save was silently discarded. `original` was also read here
    # and never used.
    text = read_source(path)
    newline = "\r\n" if "\r\n" in text else "\n"
    fresh = scan_source(text, path)

    # A subset, in any order. Exact list equality meant a developer who
    # inspected five findings and accepted three could not express that, and
    # apply_fix(path, []) raised "changed since it was scanned" about a file
    # that had not changed.
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
        return FixResult(inserted=0, functions=[])

    lines = text.split(newline)

    # Bottom-up: an insert at line 10 shifts every line below it.
    for finding in sorted(findings, key=lambda f: f.insert_line, reverse=True):
        lines.insert(finding.insert_line - 1, " " * finding.indent + _render_decorator(finding))

    needed = _missing_imports(text)
    if needed:
        at = _import_insert_index(text, lines)
        lines[at:at] = needed

    modified = newline.join(lines)

    before = _function_names(text, path)
    try:
        after = _function_names(modified, path)
    except SyntaxError as exc:
        raise UnsafeEdit(f"{path}: edit produced unparseable source: {exc}") from exc
    if before != after:
        raise UnsafeEdit(
            f"{path}: the edit changed the set of functions "
            f"({sorted(after - before)} added, {sorted(before - after)} removed); "
            "nothing written"
        )

    # Preserve the mode: a temp-and-rename otherwise turns a 0o755 script into
    # a 0o644 file, and it stops being executable. PID in the name so concurrent
    # runs cannot collide on it.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.finagent-tmp")
    try:
        tmp.write_bytes(modified.encode())
        os.chmod(tmp, stat.S_IMODE(mode))
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)

    return FixResult(inserted=len(findings), functions=[f.function for f in findings])
