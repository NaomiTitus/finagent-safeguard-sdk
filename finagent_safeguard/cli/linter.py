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
from dataclasses import dataclass
from pathlib import Path
from typing import Final

__all__ = ["Finding", "UnparseableSource", "scan_file", "scan_source"]

#: Tokens that suggest money or an identifiable person, in a function or
#: parameter name.
NAME_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "amount", "iban", "bic", "swift", "payee", "payer", "recipient",
        "transfer", "payment", "balance", "account", "card", "pan",
        "national_id", "personnummer", "fodselsnummer", "henkilotunnus", "cpr",
    }
)

#: Types that carry money or an account reference. A bare ``str`` is not here:
#: it would flag everything and a linter that flags everything gets switched off.
RISKY_ANNOTATIONS: Final[frozenset[str]] = frozenset(
    {"Decimal", "Money", "Amount", "IBAN", "AccountRef", "AccountNumber", "Card"}
)

#: Importing any of these means the module can reach the bank.
BANK_CLIENT_MODULES: Final[frozenset[str]] = frozenset(
    {"fake_bank", "bank_client", "core_banking"}
)

_DECORATOR_MARKER: Final = "regulated_tool"


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


def _annotation_names(node: ast.AST) -> set[str]:
    out: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            out.add(child.id)
        elif isinstance(child, ast.Attribute):
            out.add(child.attr)
    return out


def _is_classified(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in node.decorator_list:
        if _DECORATOR_MARKER in ast.dump(decorator):
            return True
    return False


def _reaches_bank(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.rsplit(".", 1)[-1] in BANK_CLIENT_MODULES:
                return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.rsplit(".", 1)[-1] in BANK_CLIENT_MODULES:
                    return True
    return False


def _signal_for(
    node: ast.FunctionDef | ast.AsyncFunctionDef, *, bank: bool
) -> str | None:
    """Which signal fires for this function, if any. Order is reporting order."""
    names = {node.name, *(a.arg for a in node.args.args), *(a.arg for a in node.args.kwonlyargs)}
    if any(token in name.lower() for name in names for token in NAME_TOKENS):
        return "name"

    annotated = [a.annotation for a in node.args.args + node.args.kwonlyargs if a.annotation]
    for annotation in annotated:
        if _annotation_names(annotation) & RISKY_ANNOTATIONS:
            return "type"

    if bank and not node.name.startswith("_"):
        return "bank_client_import"
    return None


def scan_source(source: str, path: Path) -> list[Finding]:
    """Findings for one module's source. Raises on unparseable input."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise UnparseableSource(f"{path}: {exc}") from exc

    bank = _reaches_bank(tree)
    lines = source.splitlines()
    findings: list[Finding] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not _is_classified(child):
                    signal = _signal_for(child, bank=bank)
                    if signal is not None:
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
                            )
                        )
                walk(child, f"{prefix}{child.name}.")

    walk(tree, "")
    return findings


def scan_file(path: Path) -> list[Finding]:
    return scan_source(path.read_text(), path)


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
#: what keeps "never leave a file in a state the developer did not inspect" true.
_SIGNAL_CATEGORY: Final[dict[str, str]] = {
    "name": "PSD2_PAYMENT_EXECUTION",
    "type": "PSD2_PAYMENT_EXECUTION",
    "bank_client_import": "PSD2_PAYMENT_EXECUTION",
}

_PII_TOKENS: Final = frozenset(
    {"national_id", "personnummer", "fodselsnummer", "henkilotunnus", "cpr", "pan"}
)


class UnsafeEdit(Exception):
    """The edited source no longer defines the same functions. Nothing written."""


class StaleFindings(Exception):
    """The file changed between being scanned and being fixed."""


@dataclass(frozen=True, slots=True)
class FixResult:
    inserted: int
    functions: list[str]


def _render_decorator(finding: Finding) -> str:
    category = _SIGNAL_CATEGORY[finding.signal]
    if any(token in finding.function.lower() for token in _PII_TOKENS):
        category = "GDPR_PII_PROCESSING"
    return (
        f"@regulated_tool(FinancialCategory.{category})"
        f"  # TODO(finagent): inserted by linter from the {finding.signal!r} "
        "signal; confirm the category"
    )


def _function_names(source: str, path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.add(node.name)
    return out


def _import_insert_index(lines: list[str]) -> int:
    """After the module docstring and any __future__ import, before the rest."""
    index = 0
    if lines and lines[0].lstrip().startswith(('"""', "'''")):
        quote = lines[0].lstrip()[:3]
        if lines[0].count(quote) >= 2:
            index = 1
        else:
            for i in range(1, len(lines)):
                index = i + 1
                if quote in lines[i]:
                    break
    for i in range(index, len(lines)):
        if "__future__" in lines[i]:
            index = i + 1
    return index


def apply_fix(path: Path, findings: list[Finding]) -> FixResult:
    """Insert a classification for each finding. Writes atomically or not at all."""
    original = path.read_bytes()
    text = original.decode()
    newline = "\r\n" if "\r\n" in text else "\n"

    fresh = [(f.function, f.insert_line) for f in scan_file(path)]
    if fresh != [(f.function, f.insert_line) for f in findings]:
        raise StaleFindings(
            f"{path} changed since it was scanned; re-run rather than writing "
            "edits that describe a file that no longer exists"
        )
    if not findings:
        return FixResult(inserted=0, functions=[])

    lines = text.split(newline)

    # Bottom-up: an insert at line 10 shifts every line below it.
    for finding in sorted(findings, key=lambda f: f.insert_line, reverse=True):
        lines.insert(finding.insert_line - 1, " " * finding.indent + _render_decorator(finding))

    needed = [i for i in (DECORATOR_IMPORT, CATEGORY_IMPORT) if i not in text]
    if needed:
        at = _import_insert_index(lines)
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

    tmp = path.with_name(path.name + ".finagent-tmp")
    try:
        tmp.write_bytes(modified.encode())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)

    return FixResult(inserted=len(findings), functions=[f.function for f in findings])
