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
import re
import subprocess
import sys
from pathlib import Path

AGENT = "finagent_safeguard/core/agent.py"
DECOR = "finagent_safeguard/core/decorators.py"
REGIS = "finagent_safeguard/regulation/registry.py"
LINT = "finagent_safeguard/cli/linter.py"
CAND = "finagent_safeguard/regulation/candidates.py"
FEAT = "finagent_safeguard/cli/features.py"
CLAS = "finagent_safeguard/cli/classifier.py"

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
    # Day 4. REVIEW_REQUIRED and the fifth write guard had no rows at all, and
    # layer 1c of the review protocol caught that before they shipped: a guard
    # nothing can prove is working is not protected.
    "L27": (
        LINT,
        '        {f.category for f in findings if f.category not in _VALID_CATEGORIES}',
        '        set()',
        "test_an_unknown_category_is_refused",
        "drop the fifth guard, letting --fix write a category that is not an "
        "enum member and produce a file that AttributeErrors on import",
    ),
    "L28": (
        LINT,
        '_VALID_CATEGORIES: frozenset[str] = frozenset(m.name for m in FinancialCategory)',
        '_VALID_CATEGORIES: frozenset[str] = frozenset({"PSD2_PAYMENT_EXECUTION"})',
        "test_the_valid_set_is_read_from_the_enum",
        "restate the member names instead of reading the enum, so the guard "
        "drifts out of step with what the linter may emit",
    ),
    "A6": (
        AGENT,
        '        if FinancialCategory.REVIEW_REQUIRED in registration.categories:',
        '        if False:',
        "test_a_tool_carrying_it_is_refused",
        "let the runtime gate accept REVIEW_REQUIRED as a classification, so "
        "the one state that exists to demand human judgement passes the gate "
        "that exists to require it",
    ),
    # Day 4, the CLI. Layer 1c flagged these as unprotected before they
    # shipped, for the second time in one day.
    "C1": (
        LINT,
        'ADVISORY_SIGNALS: Final[frozenset[str]] = frozenset({"bank_client_import"})',
        'ADVISORY_SIGNALS: Final[frozenset[str]] = frozenset()',
        "test_an_advisory_only_finding_does_not_fail_by_default",
        "empty the advisory set, making the weakest signal build-breaking by "
        "default -- 51 flags and 0 correct ones over 155 stdlib modules",
    ),
    "C2": (
        LINT,
        '        except (UnparseableSource, SyntaxError) as exc:',
        '        except SyntaxError as exc:',
        "test_an_unparseable_file_exits_two",
        "let UnparseableSource escape, crashing the process so that Python's "
        "exit 1 makes a failed run look like a successful one that found "
        "something",
    ),
    "C3": (
        LINT,
        '                            and argument.attr == _UNRESOLVED',
        '                            and argument.attr == "NOT_A_CATEGORY"',
        "test_an_unresolved_flag_fails_the_run",
        "stop recognising REVIEW_REQUIRED in a decorator, so a flagged "
        "function passes the gate as classified",
    ),
    "L29": (
        LINT,
        'PROPOSED_CATEGORY: Final[str] = "REVIEW_REQUIRED"',
        'PROPOSED_CATEGORY: Final[str] = "PSD2_PAYMENT_EXECUTION"',
        "test_only_the_refusal_is_ever_written",
        "let the tool assert a category again, writing a legal conclusion it "
        "has no basis for into a developer's source",
    ),
    "L30": (
        LINT,
        '        found.append("money")',
        '        return ("money",)',
        "test_both_are_recorded_when_both_match",
        "return on the first vocabulary match, reinstating the arbitrary "
        "tie-break that fired on the most regulated functions precisely "
        "because they carry both",
    ),
    "C4": (
        LINT,
        '            problems.append(f"{raw}: no such file or directory")',
        '            pass',
        "test_a_missing_path_exits_two",
        "swallow a path that does not exist, so a typo makes the linter scan "
        "nothing and exit clean -- silently disabling the gate",
    ),
    "A7": (
        AGENT,
        '        "classification, and this gate will not accept it as one.\\n\\n"',
        '        "category.\\n\\n"',
        "test_the_message_names_the_remedy_not_the_diagnosis",
        "replace the message that tells a developer what to do with one that "
        "only says something is wrong",
    ),
    # Day 5. The candidate mapping. Layer 1c found the whole module invisible
    # to it first -- git diff does not show a file git has never seen -- which
    # was the battery's own blind spot, of the class it exists to catch.
    "L31": (
        LINT,
        '        out.append("#   candidates to read before deciding:")',
        '        pass',
        "test_the_inserted_comment_names_the_evidence_not_a_conclusion",
        "stop writing the candidate provisions, leaving a developer a flag "
        "with nothing to act on",
    ),
    "D1": (
        CAND,
        '            if candidate not in out:',
        '            if True:',
        "test_no_duplicates",
        "stop collapsing duplicates, so a provision reached by two "
        "vocabularies reads as emphasis nobody intended",
    ),
    "D2": (
        CAND,
        '        for candidate in CANDIDATES.get(vocabulary, ()):',
        '        for candidate in next(iter(CANDIDATES.values()), ()):',
        "test_pii_offers_the_data_protection_provisions",
        "ignore which vocabulary matched and always offer the first group, so "
        "a personal-data function is pointed at the payment provisions",
    ),
    "D3": (
        CAND,
        '    reference = f"{provision.instrument.short_name} Art {provision.article}"',
        '    reference = f"Art {provision.article}"',
        "test_a_point_level_provision_reads_as_a_lawyer_would_write_it",
        "drop the instrument from a citation, so Art 97 could be any of six "
        "regulations",
    ),
    "D4": (
        CAND,
        '    if provision.point:',
        '    if False:',
        "test_a_point_level_provision_reads_as_a_lawyer_would_write_it",
        "drop the point from a citation, so 97(1)(b) payment initiation is "
        "indistinguishable from 97(1)(a) account access -- which attracts a "
        "different duty under 97(2)",
    ),
    "D5": (
        CAND,
        '    "money": (',
        '    "money_disabled": (',
        "test_money_offers_the_payment_provisions",
        "rename the money vocabulary so a payment function is offered no "
        "provision at all, silently",
    ),
    "D7": (
        CAND,
        '    GDPR_ART_25.id: (',
        '    "unused_key_25": (',
        "test_mapped_or_excused_with_a_reason",
        "drop a provision from the excused list, so a pinned duty is neither "
        "offered nor accounted for and nobody can tell it was forgotten",
    ),
    # Day 6, the diff ratchet.
    "C5": (
        LINT,
        '    if done.returncode != 0:\n        return None',
        '    if done.returncode != 0:\n        return {}',
        "test_an_unusable_ref_is_two_not_a_silent_pass",
        "treat 'I cannot tell what changed' as 'nothing changed', suppressing "
        "every finding and reporting success",
    ),
    "C6": (
        LINT,
        '                for line in range(finding.insert_line, finding.end_line + 1)',
        '                for line in [finding.insert_line]',
        "test_editing_a_regulated_function_fails",
        "ratchet on the signature line alone, waving through a payment body "
        "rewritten under an untouched signature",
    ),
    "C7": (
        LINT,
        '            count = int(found.group(2) or 1)',
        '            count = int(found.group(2) or 0)',
        "test_editing_a_regulated_function_fails",
        "treat a hunk with no explicit count as zero lines. A single-line edit "
        "is written '@@ -5 +5 @@' with no count, so every one-line change to a "
        "regulated function would pass the ratchet unnoticed",
    ),
    "L16": (
        LINT,
        '    if any(_matches_tokens(name, PII_TOKENS) for name in names):\n        found.append("pii")',
        '    if False:\n        found.append("pii")',
        "test_person_words_are_recorded_as_pii",
        "stop recording the personal-data vocabulary, so a function handling "
        "both money and people looks purely financial",
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
        '            finding.indent + line + term for line in _render_annotation(finding)',
        '            " " * len(finding.indent) + line + term for line in _render_annotation(finding)',
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

    # ---- finagent_safeguard/cli/features.py -----------------------------
    # The feature module is advisory research code rather than a guard: no
    # mutation here can mislabel a function, because nothing in the write path
    # reads a feature. What these rows protect is the *claim each feature's
    # name makes*. The project has already shipped a feature that fired
    # backwards (F8) and one whose name promised data flow while it measured
    # length, and both looked fine in aggregate -- so "the model still scores
    # about the same" is not evidence a feature works.
    "F1": (
        FEAT,
        '"transfer", "pay", "charge", "capture", "refund", "settle", "execute",',
        '"pay", "charge", "capture", "refund", "settle", "execute",',
        "test_an_action_verb_is_recognised",
        "drop the clearest action verb from the action list",
    ),
    "F2": (
        FEAT,
        '    "get", "fetch", "list", "retrieve", "search", "find", "read", "load",',
        '    "fetch", "list", "retrieve", "search", "find", "read", "load",',
        "test_a_query_verb_is_recognised",
        "drop the commonest read verb, making every getter verbless",
    ),
    "F3": (
        FEAT,
        '    "validate", "check", "verify", "assert", "compute", "calculate", "is",',
        '    "check", "verify", "assert", "compute", "calculate", "is",',
        "test_a_check_verb_is_recognised",
        "drop 'validate', the verb behind the validating-is-not-producing rule",
    ),
    "F4": (
        FEAT,
        '    ".to_excel", ".to_csv", ".to_json", ".to_sql", ".write", ".writelines",',
        '    ".to_csv", ".to_json", ".to_sql", ".write", ".writelines",',
        "test_a_write_call_is_a_sink",
        "stop treating a spreadsheet write as a sink",
    ),
    "F5": (
        FEAT,
        '    "mock", "Mock", "stub_request", "assert_requested", "patch(", "fixture",',
        '    "stub_request", "patch(", "fixture",',
        "test_a_mocked_transport_is_detected",
        "stop recognising a mocked transport, so test doubles read as real sinks",
    ),
    "F6": (
        FEAT,
        "'\"name\"', \"'name'\"",
        "'\"nome\"', \"'nome'\"",
        "test_a_party_key_literal_is_detected",
        "misspell the commonest party-key literal",
    ),
    "F7": (
        FEAT,
        '        "name", "fullname", "full_name", "holder", "account_holder",',
        '        "fullname", "full_name", "holder", "account_holder",',
        "test_reading_a_party_name_out_of_a_mapping_is_sensitive",
        "remove 'name' from SENSITIVE_KEYS -- the exact gap in PII_TOKENS that "
        "made the old party_key feature unable to fire on self._config['name']",
    ),
    "F8": (
        FEAT,
        "            if isinstance(child.ctx, ast.Load) and isinstance(child.slice.value, str):",
        "            if isinstance(child.slice.value, str):",
        "test_writing_into_a_slot_named_for_a_party_is_not",
        "count subscript *writes* as sensitive reads. This is the bug the "
        "feature actually shipped with: it made the blank-node builder outscore "
        "the function that reads a real party name, i.e. the feature fired "
        "backwards while the aggregate numbers still looked plausible",
    ),
    "F9": (
        FEAT,
        "            hits.add(key.lower())",
        "            hits.add(f\"{key.lower()}{total}\")",
        "test_distinct_keys_are_counted_not_repeated_reads",
        "count repeated reads of one key as distinct keys, so a loop over a "
        "single field outranks a body touching three",
    ),
    "F10": (
        FEAT,
        '            and child.func.attr == "get"',
        '            and child.func.attr == "__never__"',
        "test_a_mapping_read_through_get_also_counts",
        "see only x['k'] and not x.get('k'), which measures house style rather "
        "than what the function reads",
    ),
    "F11": (
        FEAT,
        "    return float(min(len(hits), 6)), float(min(total, 10))",
        "    return float(len(hits)), float(total)",
        "test_the_counts_are_capped",
        "remove the caps, letting one outlier dominate a standardised feature",
    ),
    "F12": (
        FEAT,
        "        if name and (\n"
        "            _matches_tokens(name, MONEY_TOKENS) or _matches_tokens(name, PII_TOKENS)\n"
        "        ):",
        "        if name:",
        "test_a_callee_named_generically_does_not",
        "fire on any call at all, turning a vocabulary check into a 'has calls' "
        "feature that every non-trivial function satisfies",
    ),
    "F13": (
        FEAT,
        "    if not text:\n        return 0.0, 0.0",
        "    if not text:\n        return 0.0, 1.0",
        "test_a_missing_docstring_scores_zero_rather_than_disclaiming",
        "read an absent docstring as a disclaimer, marking most of the corpus "
        "safe by default. Absent and denied are different states",
    ),
    "F14": (
        FEAT,
        'for word in ("blank", "empty", "placeholder", "stub", "no-op", "noop",',
        'for word in ("empty", "placeholder", "stub", "no-op", "noop",',
        "test_a_disclaimer_is_detected",
        "drop 'blank' -- the single word by which the corpus's hardest negative "
        "announces that it does not do the thing",
    ),
    "F15": (
        FEAT,
        "    for part in ordered:",
        "    for part in ordered[:1]:",
        "test_a_private_class_prefixed_verb_is_still_found",
        "take literally the first word, so every _cls_-prefixed method is verbless",
    ),
    "F16": (
        FEAT,
        '        "sensitive_subscripts": sensitive_subscripts,',
        '        "sensitive_subscripts_": sensitive_subscripts,',
        "test_extract_returns_the_declared_set",
        "produce a feature under a name the model does not know, which reads as "
        "a missing key at scan time",
    ),
    "F17": (
        FEAT,
        '    "dataflow": ("sensitive_subscripts", "subscript_total", "callee_vocabulary"),',
        '    "dataflow": ("subscript_total", "callee_vocabulary"),',
        "test_extract_returns_the_declared_set",
        "declare one fewer feature than extract produces, so the model silently "
        "ignores the group's strongest signal",
    ),
    "F18": (
        FEAT,
        "FEATURE_NAMES: Final[tuple[str, ...]] = tuple(\n"
        "    name for group in FEATURE_GROUPS.values() for name in group\n"
        ")",
        'FEATURE_NAMES: Final[tuple[str, ...]] = FEATURE_GROUPS["vocabulary"]',
        "test_extract_returns_the_declared_set",
        "pin the declared set to one group, so adding a feature group has no effect",
    ),
    "F19": (
        FEAT,
        'EXCLUDED_GROUPS: Final[tuple[str, ...]] = ("literal",)',
        "EXCLUDED_GROUPS: Final[tuple[str, ...]] = ()",
        "test_the_harmful_group_is_not_fitted",
        "refit the literal group, which measured -0.069 LORO AUC. It was the "
        "strongest group under a stratified split (+0.172) and the worst under "
        "leave-one-repository-out, so refitting it silently undoes the finding "
        "the whole exercise exists to establish",
    ),
    "F20": (
        FEAT,
        # Anchored on the one clause, not the whole comprehension: the
        # previous form broke the moment a line was added to it, which is a
        # stale baseline rather than a finding.
        "    if group not in EXCLUDED_GROUPS\n",
        "",
        "test_the_harmful_group_is_not_fitted",
        "fit on everything extract produces, ignoring the exclusion entirely",
    ),
    "F21": (
        FEAT,
        'FEATURES: Final[tuple[str, ...]] = ("pii_hits"',
        'FEATURES: Final[tuple[str, ...]] = (""',
        "test_the_redundant_signature_feature_is_not_fitted",
        "refit pii_hits alongside both_vocabularies, which is derived from it. "
        "Worth -0.019 LORO AUC: L2 splits one feature's credit across two "
        "expressions of the same scan",
    ),
    "F23": (
        FEAT,
        # Anchored on this row's own element only. Anchoring on the whole
        # tuple made F21 and F23 break each other: adding a second exclusion
        # invalidated the first row's anchor, which reads as a stale baseline
        # rather than as a finding.
        ', "risky_annotation")',
        ")",
        "test_the_harmful_annotation_feature_is_not_fitted",
        "refit risky_annotation, the one feature measured as actively harmful: "
        "it fires on one row in 996 and takes a weight of -5.875, the largest "
        "magnitude in the model, estimated from a single observation",
    ),
    "F22": (
        FEAT,
        "    for name in members\n    if name not in EXCLUDED_FEATURES\n)",
        "    for name in members\n)",
        "test_the_redundant_signature_feature_is_not_fitted",
        "ignore EXCLUDED_FEATURES when building the fitted set, so declaring an "
        "exclusion has no effect",
    ),
    # ---- finagent_safeguard/cli/classifier.py ---------------------------
    # Same standing: the classifier ranks a review queue, it does not gate.
    # These rows protect the three ways a stored model can be silently wrong
    # and the one way the fit can silently stop being a ranker at all (C6).
    "M1": (
        CLAS,
        "    if z >= 0.0:\n        return 1.0 / (1.0 + math.exp(-z))\n"
        "    e = math.exp(z)\n    return e / (1.0 + e)",
        "    return 1.0 / (1.0 + math.exp(-z))",
        "test_a_confident_margin_does_not_overflow",
        "collapse the sign branch to the naive form. The first version of this "
        "row mutated the +/-500 clamps instead and SURVIVED, which is how the "
        "clamps were found to be dead code: the branch was always the mechanism",
    ),
    "M2": (
        CLAS,
        "        missing = set(self.feature_names) - set(features)\n"
        "        if missing:\n"
        "            raise ValueError(f\"missing features: {sorted(missing)}\")",
        "        features = {**{n: 0.0 for n in self.feature_names}, **features}",
        "test_a_missing_feature_is_refused_not_read_as_zero",
        "default a missing feature to zero, producing a plausible score from an "
        "incomplete row",
    ),
    "M3": (
        CLAS,
        "        if names != MODEL_FEATURES:",
        "        if False:",
        "test_a_model_fitted_on_other_features_is_refused",
        "accept a model fitted on a different feature set. Every score is still "
        "produced, just wrongly, because a renamed feature reads as an absent one",
    ),
    "M4": (
        CLAS,
        '                "provenance": self.provenance,',
        "",
        "test_provenance_travels_with_the_artefact",
        "drop provenance from the serialised model, so the artefact outlives the "
        "record of how it was fitted",
    ),
    "M5": (
        CLAS,
        "        b -= rate * bias_gradient / n",
        "        b -= rate * (bias_gradient / n + l2 * b / n)",
        "test_the_intercept_learns_the_base_rate",
        "regularise the intercept, shrinking the model's estimate of the base "
        "rate towards zero when the base rate is a real quantity",
    ),
    "M6": (
        CLAS,
        "    if not finite:",
        "    if False:",
        "test_a_diverged_fit_is_refused_rather_than_returning_nan",
        "let a diverged fit return NaN weights. Every comparison against NaN is "
        "False, so the model does not score badly -- it scores unorderably, "
        "ranking the queue at random while returning a float for every row",
    ),
    "M7": (
        CLAS,
        "        spreads[k] = math.sqrt(variance) or 1.0",
        "        spreads[k] = math.sqrt(variance)",
        "test_a_constant_feature_still_yields_a_finite_score",
        "divide by a zero spread. A feature that never varies is routine in a "
        "leave-one-repository-out fold, not exotic",
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
    "D7": "caught",
    "C5": "caught",
    "C6": "caught",
    "C7": "caught",
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
    "L27": "caught",
    "L28": "caught",
    "A6": "caught",
    "C1": "caught",
    "C2": "caught",
    "C3": "caught",
    "L29": "caught",
    "L30": "caught",
    "C4": "caught",
    "A7": "caught",
    "L31": "caught",
    "D1": "caught",
    "D2": "caught",
    "D3": "caught",
    "D4": "caught",
    "D5": "caught",
    "D6": "caught",
    # features.py and classifier.py, added with the dataflow and docstring
    # groups. All verified caught by the test each row names; see
    # docs/mutation-list.md for the three that were not, on first run, and
    # what each survival turned out to mean.
    "F1": "caught",
    "F2": "caught",
    "F3": "caught",
    "F4": "caught",
    "F5": "caught",
    "F6": "caught",
    "F7": "caught",
    "F8": "caught",
    "F9": "caught",
    "F10": "caught",
    "F11": "caught",
    "F12": "caught",
    "F13": "caught",
    "F14": "caught",
    "F15": "caught",
    "F16": "caught",
    "F17": "caught",
    "F18": "caught",
    "F19": "caught",
    "F20": "caught",
    "M1": "caught",
    "M2": "caught",
    "M3": "caught",
    "M4": "caught",
    "M5": "caught",
    "M6": "caught",
    "M7": "caught",
    "F21": "caught",
    "F22": "caught",
    "F23": "caught",
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


def _duplicate_keys() -> list[str]:
    """Row ids written more than once in the literal below.

    A dict literal with a repeated key keeps the last value and discards the
    rest without a word, so a new row can be written, committed, and never run.
    This was not hypothetical: row "D7" was first written as "D6", which
    already existed, and it vanished. The baseline coverage check could not see
    it either, because both keys were present -- only the row's absence from
    the run gave it away, and only because somebody was reading the output.
    """
    source = Path(__file__).read_text()
    written = re.findall(r'^    "([A-Za-z0-9_]+)": \(', source, re.M)
    seen: dict[str, int] = {}
    for key in written:
        seen[key] = seen.get(key, 0) + 1
    return sorted(k for k, n in seen.items() if n > 1)


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
    if duplicates := _duplicate_keys():
        print("mutation ids written more than once; a dict literal discards all")
        print("but the last, so these rows are not all running:")
        for key in duplicates:
            print(f"  - {key}")
        return 1

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
