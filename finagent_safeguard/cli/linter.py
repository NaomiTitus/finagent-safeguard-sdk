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
from dataclasses import dataclass
from pathlib import Path
from typing import Final

__all__ = [
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
    # The same line model as the write path. This used to be
    # source.splitlines(), which disagrees with ast on form feeds and line
    # separators -- so the indent was read off the wrong line and --fix
    # refused the file permanently, blaming the edit rather than the scan.
    lines = _split_source_lines(source)
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
                        leading = text[: len(text) - len(text.lstrip(" \t\x0c"))]
                        findings.append(
                            Finding(
                                path=path,
                                function=f"{prefix}{child.name}",
                                insert_line=line,
                                indent=leading,
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
        lines.insert(
            finding.insert_line - 1,
            finding.indent + _render_decorator(finding) + term,
        )
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
