#!/usr/bin/env python3
"""Apply curated mutations one at a time and report which test kills each.

    python3 tools/run_mutations.py A1 A2 A3 A4 A5 D6
    python3 tools/run_mutations.py --all

A mutation nothing kills is a defect in the test suite, not in the code. Every
mutation is reverted before the next runs; the tree is verified clean at exit.
Rows correspond to docs/mutation-list.md.
"""

from __future__ import annotations

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
        '        "    @regulated_tool(FinancialCategory.PSD2_PAYMENT_EXECUTION)\n"',
        '        ""',
        "test_error_names_the_remedy",
        "remove the worked decorator example from the error message",
    ),
    "A3b": (
        AGENT,
        '        "Classify each one before constructing the agent:\n\n"',
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
        '    if any(token in name.lower() for name in names for token in NAME_TOKENS):\n        return "name"',
        '    if False:\n        return "name"',
        "test_flags_by_parameter_name",
        "disable the name-token signal",
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
        '    if before != after:',
        '    if False:',
        "test_aborts_when_the_reparse_changes_the_function_set",
        "skip the re-parse verification before writing",
    ),
    "L6": (
        LINT,
        '    if fresh != [(f.function, f.insert_line) for f in findings]:',
        '    if False:',
        "test_aborts_if_the_file_changed_since_it_was_read",
        "skip the TOCTOU check",
    ),
    "L7": (
        LINT,
        '        raise UnparseableSource(f"{path}: {exc}") from exc',
        '        return []',
        "test_a_file_that_does_not_parse_is_reported_not_skipped",
        "silently skip an unparseable file -- a clean run that means nothing",
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
}


def run_suite() -> list[str]:
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--no-header", "-x", "--tb=no"],
        capture_output=True,
        text=True,
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
        try:
            failures = run_suite()
        finally:
            path.write_text(original)
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
        try:
            actual = "caught" if run_suite() else "not_caught"
        finally:
            path.write_text(original)
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
