#!/usr/bin/env python3
"""Apply curated mutations one at a time and report which test kills each.

    python3 tools/run_mutations.py A1 A2 A3 A4 A5 D6
    python3 tools/run_mutations.py --all

A mutation nothing kills is a defect in the test suite, not in the code. Every
mutation is reverted before the next runs; the tree is verified clean at exit.
Rows correspond to docs/mutation-list.md.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

AGENT = "finagent_safeguard/core/agent.py"
DECOR = "finagent_safeguard/core/decorators.py"
REGIS = "finagent_safeguard/regulation/registry.py"
LINT = "finagent_safeguard/cli/linter.py"

# id: (file, find, replace, expected killer, note)
MUTATIONS: dict[str, tuple[str, str, str, str, str]] = {
    "A1": (
        AGENT,
        "            unclassified.append(registry_key(tool))",
        "            if not unclassified:\n                unclassified.append(registry_key(tool))",
        "test_partial_registration_still_fails",
        "report only the first unclassified tool",
    ),
    "A2": (
        AGENT,
        "class UnregulatedToolError(Exception):",
        "class UnregulatedToolError(ImportError):",
        "test_error_is_not_import_error",
        "borrow ImportError's meaning",
    ),
    "A3": (
        AGENT,
        '        "    @regulated_tool(FinancialCategory.PSD2_PAYMENT_EXECUTION)\\n"',
        '        ""',
        "test_error_names_the_remedy",
        "remove the worked decorator example from the error message",
    ),
    "A3b": (
        AGENT,
        '        "Classify each one before constructing the agent:\\n\\n"',
        '        ""',
        "(none expected)",
        "remove only the lead-in sentence, leaving the example",
    ),
    "A4": (
        AGENT,
        "        registration = lookup(tool)",
        "        try:\n            registration = lookup(tool)\n"
        "        except Exception:\n            continue",
        "test_fails_closed_when_registry_unavailable",
        "swallow lookup failures and treat the tool as classified",
    ),
    "A5": (
        AGENT,
        "        self.categories: frozenset[FinancialCategory] = _require_classification(self.tools)",
        "        self.categories: frozenset[FinancialCategory] = self._require_classification(self.tools)\n"
        "\n    _require_classification = staticmethod(_require_classification)",
        "(none expected)",
        "bind enforcement to the class, making it overridable",
    ),
    "R13": (
        REGIS,
        '    paragraph="2",\n    point="b",',
        '    paragraph="9",\n    point="z",',
        "(none expected)",
        "F-011: cite a paragraph that does not exist. TFR Art. 5(2)(b) becomes 5(9)(z); "
        "the span is article-level so the provenance check cannot tell, and the "
        "paragraph field is never validated against anything",
    ),
    # Day 3. One row per detection signal and per write-path safeguard: a
    # signal with no killing mutation is decoration, free to rot once it is
    # the thing blocking merges.
    "L1": (
        LINT,
        '    if any(_matches_tokens(name, NAME_TOKENS) for name in names):\n        return "name"',
        '    if False:\n        return "name"',
        "test_reports_the_def_line",
        "disable the name-token signal. NOTE: the obvious killer, "
        "test_flags_by_parameter_name, does NOT fail -- its fixture also carries "
        "a Decimal annotation, so the type signal covers for it",
    ),
    "L2": (
        LINT,
        '        if _annotation_names(annotation) & RISKY_ANNOTATIONS:\n            return "type"',
        '        if False:\n            return "type"',
        "test_flags_by_type_annotation_only",
        "disable the type-annotation signal",
    ),
    "L3": (
        LINT,
        '    if bank and not node.name.startswith("_"):\n        return "bank_client_import"',
        '    if False:\n        return "bank_client_import"',
        "test_flags_public_functions_in_a_module_reaching_the_bank",
        "disable the bank-client import rule",
    ),
    "L4": (
        LINT,
        '    for finding in sorted(findings, key=lambda f: f.insert_line, reverse=True):',
        '    for finding in sorted(findings, key=lambda f: f.insert_line):',
        "test_multiple_functions_all_land_correctly",
        "apply edits top-down, so earlier inserts shift later line numbers",
    ),
    "L5": (
        LINT,
        '    if changed != intended:',
        '    if False:',
        "test_an_insertion_at_the_wrong_line_is_rejected",
        "skip the qualname check, so a decorator landing on the wrong function "
        "is written and reported as success",
    ),
    "L6": (
        LINT,
        '    unknown = [f.function for f in findings if (f.function, f.insert_line) not in known]',
        '    unknown = []',
        "test_a_subset_of_findings_may_be_applied",
        "stop checking that findings belong to this file at all",
    ),
    "L7": (
        LINT,
        '        raise UnparseableSource(f"{path}: {exc}") from exc',
        '        return []',
        "test_a_file_that_does_not_parse_is_reported_not_skipped",
        "silently skip an unparseable file -- a clean run that means nothing",
    ),
    # Day 3, write path. The review found none of this logic had a killing
    # mutation, which is why the import-placement and file-identity defects
    # shipped.
    "L8": (
        LINT,
        '        at = _import_insert_index(text, lines)',
        '        at = 0',
        "test_imports_land_below_a_shebang",
        "insert imports at line 0, above any shebang or docstring",
    ),
    "L9": (
        LINT,
        '        os.chmod(tmp, stat.S_IMODE(mode))',
        '        pass',
        "test_preserves_the_executable_bit",
        "drop mode preservation, turning a 0o755 script into 0o644",
    ),
    "L10": (
        LINT,
        '    if path.is_symlink():',
        '    if False:',
        "test_refuses_a_symlink",
        "follow symlinks, replacing the link and leaving the real source stale",
    ),
    "L11": (
        LINT,
        '    if not os.access(path, os.W_OK):',
        '    if False:',
        "test_refuses_a_read_only_file",
        "rewrite a read-only file, which an atomic rename allows",
    ),
    "L12": (
        LINT,
        '        tmp.replace(path)',
        '        path.write_bytes(modified.encode())',
        "test_the_write_is_atomic_not_in_place",
        "write in place instead of atomic temp-and-rename. NOTE: "
        "test_leaves_no_temporary_file_behind does NOT catch this -- the rename "
        "consumes the temp on the success path either way",
    ),
    "L13": (
        LINT,
        '        return raw.decode("utf-8-sig")',
        '        return raw.decode("utf-8")',
        "test_a_bom_prefixed_file_scans_normally",
        "stop stripping the byte-order mark, hard-failing a legal file",
    ),
    # Second review. The category written into a developer's source, the
    # overload hazard and the import decision had no rows at all, which is
    # why those defects shipped.
    "L14": (
        LINT,
        '    needed = _missing_imports(text)',
        '    needed = [i for i in (DECORATOR_IMPORT, CATEGORY_IMPORT) if i not in text]',
        "test_a_docstring_mentioning_the_import_does_not_suppress_it",
        "decide imports by substring, so a docstring example suppresses them",
    ),
    "L15": (
        LINT,
        '        if isinstance(target, ast.Name) and target.id in overload_names:',
        '        if isinstance(target, ast.Name) and target.id == "overload":',
        "test_a_local_decorator_named_overload_does_not_exempt",
        "trust the bare name overload, so any local decorator of that name "
        "silently exempts a money-handling function",
    ),
    "L26": (
        LINT,
        '        parts = [p for p in token.split("_") if p]',
        '        parts = [token]',
        "test_substring_does_not_count_as_a_match",
        "stop splitting multi-word tokens into words, so matching falls "
        "back to a substring test",
    ),
    "L16": (
        LINT,
        '    if any(_matches_tokens(n, PII_TOKENS) for n in names):\n        return "GDPR_PII_PROCESSING"',
        '    if False:\n        return "GDPR_PII_PROCESSING"',
        "test_personal_data_gets_the_gdpr_category",
        "write a payments category onto personal-data functions",
    ),
    "L17": (
        LINT,
        '    stale = [f.function for f in findings if f.source_sha != digest]',
        '    stale = []',
        "test_a_content_change_is_detected_even_when_lines_are_unchanged",
        "drop the content digest, silently overwriting a concurrent save",
    ),
    "L18": (
        LINT,
        '    return bool(set(dotted.split(".")) & BANK_CLIENT_MARKERS)',
        '    return dotted.rsplit(".", 1)[-1] in BANK_CLIENT_MARKERS',
        "test_a_realistic_module_path_is_recognised",
        "match only the last component of a dotted import path",
    ),
    "L19": (
        LINT,
        '    envelope = _only_insertions(raw, new_bytes)',
        '    envelope = []',
        "test_a_byte_order_mark_survives",
        "skip the byte guard, so a dropped BOM is written silently",
    ),
    "L20": (
        LINT,
        r'    bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""',
        '    bom = b""',
        "test_a_byte_order_mark_survives",
        "stop re-attaching the byte-order mark on write",
    ),
    "L21": (
        LINT,
        '    if set(before) != set(after):',
        '    if False:',
        "test_aborts_when_the_reparse_changes_the_function_set",
        "skip the function-set check",
    ),
    "L22": (
        LINT,
        '    return io.StringIO(text, newline="").readlines() or [""]',
        "    return text.splitlines(keepends=True) or ['']",
        "test_does_not_split_on_a_form_feed",
        "split with str.splitlines, which breaks on form feeds and line separators the tokenizer ignores",
    ),
    "L23": (
        LINT,
        '        term = _terminator(lines[finding.insert_line - 1])',
        '        term = "\\n"',
        "test_fixes_correctly_or_refuses_without_writing[all_crlf]",
        "hard-code the inserted line ending instead of matching its neighbour",
    ),
    "L24": (
        LINT,
        '            finding.indent + _render_decorator(finding) + term,',
        '            " " * len(finding.indent) + _render_decorator(finding) + term,',
        "test_a_tab_indented_method_is_indented_with_a_tab",
        "re-emit indentation as spaces, destroying tabs",
    ),
    "L25": (
        LINT,
        '    modified = "".join(lines)',
        '    modified = "\\n".join(line.rstrip("\\r\\n") for line in lines)',
        "test_fixes_correctly_or_refuses_without_writing[mixed_crlf_lf]",
        "normalise every line ending on join",
    ),
    "D6": (
        DECOR,
        "    return TOOL_REGISTRY.get(registry_key(func))",
        "    hit = TOOL_REGISTRY.get(registry_key(func))\n"
        "    if hit is None and getattr(func, '_regulated', False):\n"
        "        return RegisteredTool(func.__module__, func.__qualname__, frozenset())\n"
        "    return hit",
        "test_attribute_spoofing_does_not_satisfy_the_gate",
        "honour a hand-set _regulated attribute",
    ),
}


# Expected status per mutation id. A mutation moving from "caught" to "not_caught" is a
# regression and fails CI. Moving the other way is a fix, and requires updating this map by
# hand in the same commit -- the same deliberate friction as the corpus golden constants.
BASELINE: dict[str, str] = {
    "A1": "not_caught",
    "A2": "caught",
    "A3": "caught",
    "A3b": "not_caught",
    "A4": "caught",
    "A5": "not_caught",
    "D6": "caught",
    # F-011 narrowed: citation structure is now validated against the span,
    # so fabricating Art. 5(9)(z) is caught. Hand-updated, which is the
    # intended friction -- a mutation becoming caught is a fix worth noticing.
    "R13": "caught",
    "L1": "caught",
    "L2": "caught",
    "L3": "caught",
    "L4": "caught",
    "L5": "caught",
    "L6": "caught",
    "L7": "caught",
    "L8": "caught",
    "L9": "caught",
    "L10": "caught",
    "L11": "caught",
    "L12": "caught",
    "L13": "caught",
    "L14": "caught",
    "L15": "caught",
    "L16": "caught",
    "L17": "caught",
    "L18": "caught",
    # L19 and L21 are defence-in-depth. Their cases are caught by a
    # stronger check first, so neither has a test that depends on it alone.
    # Recorded honestly rather than given a contrived test: a row that reads
    # "caught" because of an unrelated assertion is worse than one that admits
    # it is a backstop.
    "L19": "not_caught",
    "L20": "caught",
    "L21": "not_caught",
    "L22": "caught",
    "L23": "caught",
    "L24": "caught",
    "L25": "caught",
    "L26": "caught",
}


def _purge_bytecode(path: Path) -> None:
    """Drop cached bytecode for the package a mutation touched.

    Belt to PYTHONDONTWRITEBYTECODE's braces: caches written by an earlier run,
    or by the developer's own imports, are still on disk and still stale.
    """
    cache = path.parent / "__pycache__"
    shutil.rmtree(cache, ignore_errors=True)


def run_suite() -> list[str]:
    # PYTHONDONTWRITEBYTECODE is not optional here. A mutation that preserves
    # file size -- paragraph="9" for paragraph="2", say -- written and reverted
    # inside the same second leaves Python's (mtime, size) cache check seeing no
    # change, so the next run imports bytecode compiled from the MUTATED source.
    # That silently corrupts the verdict in either direction.
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--no-header", "-x", "--tb=no"],
        capture_output=True,
        text=True,
        env=env,
    ).stdout
    return [ln.split()[1] for ln in out.splitlines() if ln.startswith("FAILED") and len(ln.split()) > 1]


def main(ids: list[str]) -> int:
    results = []
    for mid in ids:
        path_s, find, repl, expected, note = MUTATIONS[mid]
        path = Path(path_s)
        original = path.read_text()
        if find not in original:
            print(f"{mid}: SKIP - anchor not found (code moved?)")
            continue
        path.write_text(original.replace(find, repl, 1))
        _purge_bytecode(path)
        try:
            failures = run_suite()
        finally:
            path.write_text(original)
            _purge_bytecode(path)
        caught = bool(failures)
        results.append((mid, note, expected, failures, caught))
        print(f"\n{mid}  {note}")
        print(f"  expected killer : {expected}")
        if caught:
            print(f"  RESULT          : {failures[0]} FAILED")
            print("  VERDICT         : load-bearing")
        else:
            print("  RESULT          : all tests still pass")
            print("  VERDICT         : *** NOT CAUGHT ***")

    dirty = subprocess.run(["git", "diff", "--quiet"]).returncode
    print(f"\ntree {'DIRTY - revert manually!' if dirty else 'clean, all mutations reverted'}")
    missed = [m for m, *_, c in results if not c]
    print(f"{len(results) - len(missed)}/{len(results)} caught"
          + (f"  |  NOT CAUGHT: {', '.join(missed)}" if missed else ""))
    return 1 if missed else 0


def check() -> int:
    """CI mode: every baselined mutation must match its expected status."""
    # Snapshot dirtiness up front: a tree that was already dirty is not evidence
    # that a mutation leaked. Only a *change* in dirtiness is.
    was_dirty = subprocess.run(["git", "diff", "--quiet"]).returncode != 0
    _was_dirty_at_start = {
        path_s: subprocess.run(["git", "diff", "--quiet", "--", path_s]).returncode != 0
        for path_s, *_rest in MUTATIONS.values()
    }
    drift = []

    # Iterating BASELINE alone meant a mutation added to MUTATIONS without a
    # baseline entry was never executed, while the summary still reported
    # "N mutations match baseline". Pin both directions.
    unbaselined = sorted(set(MUTATIONS) - set(BASELINE))
    unknown = sorted(set(BASELINE) - set(MUTATIONS))
    if unbaselined or unknown:
        print("baseline does not cover the mutation set:")
        for mid in unbaselined:
            print(f"  - {mid} has no baseline entry, so it is never run")
        for mid in unknown:
            print(f"  - {mid} is baselined but no longer defined")
        return 1
    for mid, expected in BASELINE.items():
        path_s, find, repl, _exp, note = MUTATIONS[mid]
        path = Path(path_s)
        original = path.read_text()
        if find not in original:
            drift.append(f"{mid}: anchor missing (code moved) - baseline is stale")
            continue
        path.write_text(original.replace(find, repl, 1))
        _purge_bytecode(path)
        try:
            actual = "caught" if run_suite() else "not_caught"
        finally:
            path.write_text(original)
            _purge_bytecode(path)
        mark = "ok" if actual == expected else "DRIFT"
        print(f"  {mid:<5} expected={expected:<11} actual={actual:<11} {mark}  {note}")
        if actual != expected:
            drift.append(f"{mid}: expected {expected}, got {actual}")

    # Comparing whole-tree dirtiness disabled the alarm on any dirty tree, which
    # is the normal state while developing. Compare only the files mutations
    # touch, so a leak is caught regardless of unrelated edits.
    touched = {Path(MUTATIONS[mid][0]) for mid in BASELINE}
    leaked = [
        str(p)
        for p in touched
        if subprocess.run(["git", "diff", "--quiet", "--", str(p)]).returncode
        and not _was_dirty_at_start.get(str(p), False)
    ]
    if leaked:
        print(f"mutation leaked into {leaked} - revert by hand")
        return 1
    if was_dirty:
        print("note: tree had unrelated uncommitted changes before this run")
    if drift:
        print("\nmutation baseline drift:")
        for d in drift:
            print(f"  - {d}")
        print("\nIf a mutation is now caught, that is a fix: update BASELINE in this file.")
        return 1
    print(f"\n{len(BASELINE)} mutations match baseline")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--check"]:
        raise SystemExit(check())
    raise SystemExit(main(sorted(MUTATIONS) if args == ["--all"] else args))
