# Mutation list

One row per behaviour the code claims. Each is a single edit that should make at least one
test fail. A mutation nothing catches is a defect in the suite, not in the code.

This file doubles as documentation of what each test is *for*. Any new behaviour without a
row here is itself a review finding.

**Status:** `caught` (verified, killer test named) · `NOT CAUGHT` (verified hole) ·
`unrun` (listed, not yet applied). Always revert after applying; confirm with
`git diff --quiet`.

**This file is the annotated subset, not the list.** The executable list is
`MUTATIONS` in `tools/run_mutations.py` — 93 rows, every one carrying its own
note and the name of the test that must fail, and `BASELINE` in the same file is
what CI enforces. 57 of those rows are written up here, chosen because they
carry a lesson worth more than the row: a guard that turned out to be dead
code, a test whose tolerance was wider than the effect it named, a feature that
fired backwards. The 36 not here — most of the `L` series, covering the
linter's write path — are no less enforced; they are just not more interesting
than their own note.

A discrepancy between this file and `BASELINE` is a defect in this file.
`BASELINE` is executed on every push; prose is not.

---

## `regulation/ingest.py`

| # | Mutation | Expected killer | Status |
|---|---|---|---|
| I1 | Require `class="eli-subdivision"` in the subdivision selector | `test_annex_without_eli_class_is_extracted` | unrun |
| I2 | Always `depth += 1` in the div scan (never close) | any extraction test | unrun |
| I3 | Return an empty `Subdivision` instead of raising on a missing id | `test_unknown_subdivision_raises_rather_than_guessing` | unrun |
| I4 | `date <= as_of` becomes `date < as_of` | `test_selects_greatest_consolidation_at_or_before_date[2024-04-08]` | unrun |
| I5 | `max(applicable)` becomes `min(applicable)` | same, other params | unrun |
| I6 | `_legal_value_for` returns `"authentic"` for consolidated CELEX | `test_consolidated_celex_records_no_legal_value` | unrun |
| I7 | Drop `legal_value` from the digest tuple | `test_detects_a_flipped_legal_value` | unrun |
| I8 | Add `retrieved_at` to the digest tuple | `test_ignores_retrieved_at` | unrun |
| I9 | `corpus_keys` returns unsorted keys | `test_corpus_digest_matches_golden` | unrun |
| I10 | `pin()` hashes the raw HTML rather than the extracted text | `test_hash_recorded_at_ingest_time` | unrun |
| I11 | Strip the title div but leave it in `text` | `test_chunk_extracts_only_target_article` | unrun |

## `regulation/citation.py`

| # | Mutation | Expected killer | Status |
|---|---|---|---|
| C1 | CELEX regex accepts any string | `test_base_act_celex_format` | unrun |
| C2 | Allow `celex=None` for a non-pending instrument | `test_only_pending_instruments_may_lack_a_celex` | unrun |
| C3 | `corpus_celex` prefers `celex` over `consolidated_celex` | `test_every_provision_resolves_to_a_pinned_corpus_entry` | unrun |
| C4 | `Provision.id` omits the paragraph and point | none expected — **gap?** | unrun |

## `regulation/registry.py`

| # | Mutation | Expected killer | Status |
|---|---|---|---|
| R1 | Drop the locus requirement for `regulatory_verbatim` | `test_constructing_verbatim_number_without_locus_is_rejected` | unrun |
| R2 | Rationale minimum 40 chars becomes 0 | `test_constructing_configured_number_without_rationale_is_rejected` | unrun |
| R3 | `as_cited` ignores `verbatim_form`, always renders the numeral | `test_regulatory_verbatim_number_appears_in_pinned_span` (on `art16.c`) | unrun |
| R4 | `eu_numeral` uses `,` as the thousands separator | same (on the AMLR 10 000 entries) | unrun |
| R5 | Set `TRUSTED_BENEFICIARY.effect = "creates"` | `test_every_exemption_only_relaxes` | unrun |
| R6 | Add an `amount: Decimal` field to `RiskBasedObligation` | `test_risk_based_obligation_has_no_amount_field` | unrun |
| R7 | Alter one character of any `obligation_text` | `test_obligation_text_is_a_substring_of_its_pinned_span` | unrun |
| R8 | AMLR obligations become `enforcing=True` | `test_no_enforcing_obligation_cites_a_not_yet_applicable_instrument` | unrun |
| R9 | Add a second `SdkRole` member and use it | `test_sdk_role_is_contributes_only` | unrun |
| R10 | `ReferencePoint.governs` becomes `""` | `test_reference_point_requires_governs` | unrun |
| R11 | Point both EUR 10 000 reference points at the same article | `test_the_two_amlr_ten_thousands_are_distinct_entries` | unrun |
| R13 | Cite a paragraph or point that does not exist (Art. 5(9)(z)) | `test_every_cited_paragraph_exists_in_the_span` | caught |
| R12 | `provisions()` returns an empty iterator, so every check that iterates the registry passes by having nothing to check | `test_nothing_is_cited_that_is_not_pinned` | caught |

## `core/decorators.py`

| # | Mutation | Expected killer | Status |
|---|---|---|---|
| D1 | Drop the union — `merged = frozenset(categories)` | `test_stacked_decorators_register_both` | unrun |
| D2 | Key the registry by `id(func)` | `test_stacked_decorators_register_both` | unrun |
| D3 | Return a `functools.wraps` wrapper instead of `func` | `test_decorator_preserves_function_identity` | unrun |
| D4 | Drop the `isinstance` category check | `test_non_enum_category_rejected` | unrun |
| D5 | Allow an empty category list | `test_empty_classification_rejected` | unrun |
| D6 | `lookup` falls back to a `_regulated` attribute on the function | `test_attribute_spoofing_does_not_satisfy_the_gate` | unrun |

## `core/agent.py`

| # | Mutation | Expected killer | Status |
|---|---|---|---|
| **A1** | **Report only the first unclassified tool** | `test_partial_registration_still_fails` — named, but does not fail | **NOT CAUGHT** |
| A2 | `UnregulatedToolError` subclasses `ImportError` | `test_error_is_not_import_error` | unrun |
| A3 | Drop the remedy from the error message | `test_error_names_the_remedy` | unrun |
| A4 | Swallow lookup exceptions and treat as classified | `test_fails_closed_when_registry_unavailable` | unrun |
| **A5** | **Bind enforcement to the class as `_require_classification`, then override it in a subclass** | **none — see F-002** | **NOT CAUGHT** |

The rows below carry a **B** prefix because they are *documented holes rather
than executable mutations*: a subclass that never calls `super()`, or an
`object.__new__` that skips `__init__`, is not a textual edit anything can
apply, so none has a killer test by construction. The prefix exists because two
of them were originally numbered in the A-series and those numbers were later
reused for real executable rows — the same identifier meaning a verified hole
in one file and a caught mutation in another.

Every one of these is a bypass of the runtime gate by someone *trying* to get
around it. That is outside this tool's threat model, which is **error, not
adversary**: a linter helps a developer who wants to get it right. Listed
anyway, because a gap you can name is a different thing from one you have not
noticed.
| A3b | Remove only the prose lead-in from the error message | none — decided acceptable | NOT CAUGHT (closed) |
| **B1** | **Subclass overrides `__init__` and never calls `super()`** | **none — see F-004** | **NOT CAUGHT** |
| **B2** | **`object.__new__(BaseCompliantAgent)` + attribute assignment (also `pickle`, `copy`)** | **none — see F-004** | **NOT CAUGHT** |
| **B3** | **Classify a stub, then rebind the module name to an unclassified function** | **none — see F-005** | **NOT CAUGHT** |
| **B4** | **Set `__qualname__` on an unclassified function to one already classified** | **none — see F-005** | **NOT CAUGHT** |
| **B5** | **Pass a callable object or `functools.partial`** | **none — raises AttributeError, see F-006** | **NOT CAUGHT** |
| **B6** | **Replace `TOOL_REGISTRY` with a dict whose `get` always answers** | **none — see F-007** | **NOT CAUGHT** |
| **B7** | **Reassign `agent.tools` after construction** | **none — see F-008** | **NOT CAUGHT** |

## `cli/linter.py` — path exclusion

A first run that is 80% test-file noise gets the tool switched off, which is
why `--exclude` exists. It is an **adoption aid, not a security boundary**:
everything it skips is simply not scanned, so a too-broad pattern fails
quietly. Hence X3 and X4 — the count and the warning are what make a quiet
pass visible.

| # | Mutation | Expected killer | Status |
|---|---|---|---|
| X1 | Stop matching the pattern itself, so `--exclude` works only via the bare-directory convenience | `test_a_pattern_is_anchored_at_the_right` | caught |
| **X2** | **Go back to `fnmatch`, whose `*` crosses a directory separator** | `test_a_pattern_does_not_match_across_a_directory_separator` | caught |
| X3 | Stop reporting how many files were skipped | `test_the_count_is_always_reported` | caught |
| X4 | Drop the warning for a pattern that matches nothing | `test_a_pattern_matching_nothing_warns` | caught |
| X5 | Exclude every file whenever `--exclude` is given at all | `test_without_exclude_everything_is_scanned` | caught |

**X2 is a bug this shipped with for about ten minutes.** The first
implementation used `fnmatch`, whose `*` spans `/`. So
`--exclude '*/test_*.py'` matched `~/test_project/src/app/pay.py` — an
ancestor directory beginning with `test_` was enough to exclude a developer's
entire tree, reported only as a count they had no reason to question. A test
caught it, because `pytest`'s own `tmp_path` is named after the test function
and that name began with `test_`.

`PurePath.match` replaced it: right-anchored, and `*` does not cross a
separator. Both properties are needed — `fnmatch` anchors *left*, so
`--exclude 'tests/*'` would not have matched `src/tests/x.py` and the obvious
pattern would have excluded nothing at all.

## `cli/linter.py` — the approval guard

Layer 3 of this tool is a human confirming a classification, so the bytes
approved must be the bytes written. `--diff-only` previously showed a diff and
`--fix` recomputed one; nothing tied them together.

| # | Mutation | Expected killer | Status |
|---|---|---|---|
| P1 | Ignore the approved digest, making `--expect-plan` decorative | `test_approving_a_different_edit_is_refused` | caught |
| **P2** | **Skip the on-disk re-read, so a plan computed from an older version silently overwrites a change made since — the race that separating plan from write would *widen* rather than fix** | `test_a_stale_plan_object_is_refused_at_write_time` | caught |
| P3 | Drop the approval passing through `apply_fix`, so the CLI's flag is accepted and discarded | `test_approving_a_different_edit_is_refused` | caught |
| P4 | Digest the input instead of the output, so two different edits share an approval | `test_a_plan_carries_the_digest_of_its_own_output` | caught |
| P5 | Accept a malformed digest, so a typo reads as "the file changed" | `test_a_malformed_digest_is_a_usage_error_not_a_refusal` | caught |
| P6 | Drop the `if it applies` framing, turning a paste-ready lookup into what reads as the tool's own conclusion | `test_a_suggested_decorator_is_conditional_and_not_the_one_applied` | caught |
| **P7** | **Narrow TFR Art 4(4) to one category** — a payer-information control sitting in AML/CFT practice *and* payment execution, so choosing for the developer is the assertion the module refuses to make | `test_a_provision_filed_under_two_categories_shows_both` | caught |
| P8 | Stop telling a developer that decorators stack, on exactly the functions where two apply | `test_stacking_is_mentioned_only_when_more_than_one_vocabulary_matched` | caught |
| P9 | Abbreviate the plan digest to three characters — short enough to collide between two genuinely different edits | `test_a_plan_carries_the_digest_of_its_own_output` | caught |

**P7 caught a vacuous test as it was written.** The test asserted that both
category names appeared *anywhere* in the comment block — but
`PSD2_PAYMENT_EXECUTION` is also suggested by the two Art 97 candidates, so
narrowing TFR to one category left it passing. It now reads the suggestions
that follow the TFR citation specifically. **That is the second vacuous test
this protocol found in one session**, both with accurate names and bodies that
could not fail.

**A vacuous test was found while writing these.**
`test_the_plan_is_what_gets_written` claimed *"the diff a developer approves
must be the bytes that land"* and proved nothing: it planned, then called
`apply_fix` which **re-planned**, and two plans of an unchanged file agree
whether or not the write honours the approval. It now applies the plan object,
which is the only form that can distinguish the two.

## `cli/features.py`

The feature module is **advisory research code, not a guard**: nothing in the
linter's write path reads a feature, so no mutation here can mislabel a
function. What these rows protect is the *claim each feature's name makes* —
and that claim has been wrong twice already. `sensitive_subscripts` shipped
counting subscript **writes** as sensitive reads, which made the blank-node
builder outscore the function that reads a real party name: the feature fired
backwards while every aggregate number still looked plausible. "The model still
scores about the same" is therefore not evidence that a feature works.

| # | Mutation | Expected killer | Status |
|---|---|---|---|
| F1 | Drop `transfer` from `ACTION_VERBS` | `test_an_action_verb_is_recognised` | caught |
| F2 | Drop `get` from `QUERY_VERBS` | `test_a_query_verb_is_recognised` | caught |
| F3 | Drop `validate` from `CHECK_VERBS` | `test_a_check_verb_is_recognised` | caught |
| F4 | Drop `.to_excel` from `SINK_CALLS` | `test_a_write_call_is_a_sink` | caught |
| F5 | Drop the mock markers, so test doubles read as real sinks | `test_a_mocked_transport_is_detected` | caught |
| F6 | Misspell the commonest party-key literal | `test_a_party_key_literal_is_detected` | caught |
| F7 | Remove `"name"` from `SENSITIVE_KEYS` — the exact gap in `PII_TOKENS` that made the old `party_key` unable to fire on `self._config['name']` | `test_reading_a_party_name_out_of_a_mapping_is_sensitive` | caught |
| **F8** | **Count subscript *writes* as sensitive reads — the bug the feature actually shipped with** | `test_writing_into_a_slot_named_for_a_party_is_not` | caught |
| F9 | Count repeated reads of one key as distinct keys | `test_distinct_keys_are_counted_not_repeated_reads` | caught |
| F10 | See only `x['k']` and not `x.get('k')`, measuring house style | `test_a_mapping_read_through_get_also_counts` | caught |
| F11 | Remove the count caps, letting one outlier dominate | `test_the_counts_are_capped` | caught |
| F12 | Fire `callee_vocabulary` on any call at all | `test_a_callee_named_generically_does_not` | caught |
| F13 | Read an absent docstring as a disclaimer — absent and denied are different states | `test_a_missing_docstring_scores_zero_rather_than_disclaiming` | caught |
| F14 | Drop `blank`, the word by which the hardest negative announces itself | `test_a_disclaimer_is_detected` | caught |
| F15 | Take literally the first word as the verb | `test_a_private_class_prefixed_verb_is_still_found` | caught |
| F16 | Produce a feature under a name the model does not know | `test_extract_returns_the_declared_set` | caught |
| F17 | Declare one fewer feature than `extract` produces | `test_extract_returns_the_declared_set` | caught |
| F18 | Pin `FEATURE_NAMES` to one group, so adding a group has no effect | `test_extract_returns_the_declared_set` | caught |
| **F19** | **Refit the `literal` group, which measured −0.069 LORO AUC** — strongest group under a stratified split (+0.172), worst under leave-one-repository-out | `test_the_harmful_group_is_not_fitted` | caught |
| F20 | Fit on everything `extract` produces, ignoring `EXCLUDED_GROUPS` entirely | `test_the_harmful_group_is_not_fitted` | caught |
| F21 | Refit `pii_hits` alongside `both_vocabularies`, which is derived from it — worth −0.019 LORO AUC | `test_the_redundant_signature_feature_is_not_fitted` | caught |
| F22 | Ignore `EXCLUDED_FEATURES` when building the fitted set, so declaring an exclusion has no effect | `test_the_redundant_signature_feature_is_not_fitted` | caught |

## `cli/classifier.py`

Same standing: the classifier **ranks a review queue, it does not gate**. A
wrong score misorders a queue; it cannot mislabel a function. These rows cover
the three ways a stored model can be silently wrong, and the one way the fit
can stop being a ranker at all.

| # | Mutation | Expected killer | Status |
|---|---|---|---|
| M1 | Collapse the sigmoid's sign branch to the naive form | `test_a_confident_margin_does_not_overflow` | caught |
| M2 | Default a missing feature to zero, scoring an incomplete row | `test_a_missing_feature_is_refused_not_read_as_zero` | caught |
| M3 | Accept a model fitted on a different feature set | `test_a_model_fitted_on_other_features_is_refused` | caught |
| M4 | Drop provenance from the serialised model | `test_json_is_readable_rather_than_a_pickle` | caught |
| M5 | Regularise the intercept, shrinking the base-rate estimate towards zero | `test_the_intercept_learns_the_base_rate` | caught |
| **M6** | **Let a diverged fit return NaN weights. Every comparison against NaN is False, so the model does not score *badly* — it scores unorderably, ranking the queue at random while returning a float for every row** | `test_a_diverged_fit_is_refused_rather_than_returning_nan` | caught |
| M7 | Divide by a zero spread — routine in a LORO fold, not exotic | `test_a_constant_feature_still_yields_a_finite_score` | caught |

### Three of these rows were wrong on their first run, and each was informative

The first pass reported 25/25 caught. It was not: M1, M5 and M6 had been
"killed" only by an unrelated meta-suite failure elsewhere in the same run — a
test of mine whose *name* promised a rejection while its body asserted a
positive property. With the suite green the three survived, and each survival
was a different kind of finding.

**M1 — a dead guard.** `sigmoid` clamped z to ±500 inside each arm of a sign
branch, and the mutation removing the clamps survived because *the branch alone
prevents the overflow*: each arm only ever exponentiates a non-positive number.
The clamps were unreachable. They have been removed, because a dead guard that
looks like the safety mechanism is worse than no guard — the next reader trusts
it and removes the branch. M1 now mutates the branch, and is caught.

**M5 — a tolerance looser than its own claim.** The test asserted the fit
recovers a 0.10 base rate to `abs=0.03`. Regularising the intercept moves it to
0.1199, which that tolerance admits. With uninformative features the
maximum-likelihood intercept is exactly `log(p / (1 - p))`, so the fit should
reach the base rate to within numerical convergence; the tolerance is now
`abs=0.005` and the row is caught. **A passing test with a tolerance wider than
the effect it names is indistinguishable from no test.**

**M6 — a malformed mutation, not a hole.** The row read
`if False and not all(...) or not math.isfinite(intercept)`, which by operator
precedence reduces to `if not math.isfinite(intercept)` — so the guard still
fired and the mutation never disabled anything. The guard was restructured to a
single named condition so a mutation can actually turn it off.

**M6's underlying bug was found by writing these rows, not by the training
runs.** `fit` accepts an `l2` argument and, for a large enough value at the
fixed learning rate, diverged and returned NaN weights with no complaint. It
now refuses.

## Planned (no code yet)

| # | Mutation | Lands |
|---|---|---|
| P1 | Art. 16 `(b) OR (c)` becomes `(b) AND (c)` | Day 4 |
| P2 | `decide()` returns ALLOW when no exemption matches | Day 4 |
| P3 | Art. 13 gains an amount ceiling | Day 4 |
| P4 | Danish CPR validator hard-rejects on mod-11 failure | Day 4 |
| P5 | Finnish validator accepts only `-`, `+`, `A` | Day 4 |
| P6 | `--fix` writes without re-parsing | Day 3 |
| P7 | A metric label carries an amount | Day 5 |
| P8 | Entailment accepts a verdict whose quote is not a substring | Day 6 |
