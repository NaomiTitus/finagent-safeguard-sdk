# Mutation list

One row per behaviour the code claims. Each is a single edit that should make at least one
test fail. A mutation nothing catches is a defect in the suite, not in the code.

This file doubles as documentation of what each test is *for*. Any new behaviour without a
row here is itself a review finding.

**Status:** `caught` (verified, killer test named) · `NOT CAUGHT` (verified hole) ·
`unrun` (listed, not yet applied). Always revert after applying; confirm with
`git diff --quiet`.

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
| **R12** | **`provisions()` returns an empty iterator** | **none — see F-001** | **NOT CAUGHT** |

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
| A1 | Report only the first unclassified tool | `test_partial_registration_still_fails` | unrun |
| A2 | `UnregulatedToolError` subclasses `ImportError` | `test_error_is_not_import_error` | unrun |
| A3 | Drop the remedy from the error message | `test_error_names_the_remedy` | unrun |
| A4 | Swallow lookup exceptions and treat as classified | `test_fails_closed_when_registry_unavailable` | unrun |
| **A5** | **Bind enforcement to the class as `_require_classification`, then override it in a subclass** | **none — see F-002** | **NOT CAUGHT** |

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
