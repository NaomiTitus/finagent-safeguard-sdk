# Test Matrix — RED-phase specification

**Companion to:** `PLAN.md` (design intent). This file owns the test inventory; PLAN.md §7 owns the day structure.
**Date:** 2026-10-03
**Status:** scaffolding. Boundary values here are destined for `examples[]` on registry entries (Day 1 / Day 4), after which pytest parametrises from the registry and this file becomes a planning record rather than an authority. Do not let it become a second source of truth.

**How to read:** every row is one RED-phase test. `Cites` names the pinned provision whose verbatim span must be quoted in the test docstring — a paraphrase means the test is not done (PLAN.md §3). `May assume` names the fixtures the test is permitted to rely on; anything not listed must be constructed in the test.

---

## 0. Shared fixture inventory

Build these first; every deliverable depends on them.

| Fixture | Provides |
|---|---|
| `corpus` | Loads `corpus/<celex>/<subdivision>.json`; exposes `.text`, `.sha256`, `.legal_value`. Read-only. |
| `registry` | The imported typed registry. Session-scoped. |
| `poisoned_registry` | A registry containing one deliberately wrong entry: `threshold=30`, `applies_when=AMOUNT_ABOVE`, pinned to the genuine Art. 16 span. Used only by the entailment tests. |
| `recording_llm` | Fake local-inference endpoint. Records calls; configurable to return a payload, raise `TimeoutError`, or return malformed JSON. Asserts call count. |
| `fake_bank_client` | Records every invocation. Used for the structural import rule. |
| `tmp_source_file` | Writes a source string to a temp file, returns path + original bytes for byte-comparison. Parametrisable line endings (LF / CRLF). |
| `mock_pushgateway` | HTTP server capturing method, path, headers and raw body. |
| `frozen_date` | Pins `date.today()` for staleness and applicability tests. |
| `synthetic_ids` | Generators for checksum-valid NO / SE / FI identifiers and format-valid DK CPR, both mod-11-passing and failing. |

**Hard constraint on every row below:** no network, no API key. Any model interaction goes through `recording_llm`.

---

## 1. Registry and provenance — `tests/test_registry_integrity.py`

These are meta-tests over the registry. They are the mechanism that makes the EUR 30 / EUR 500 error class hard to commit.

| Test | Cites | Inputs / boundary | Expected | May assume |
|---|---|---|---|---|
| `test_every_numeric_parameter_declares_provenance` | — | Every numeric field on every registry object | Each has `provenance` in `{regulatory_verbatim, operator_configured, vendor_default}` | `registry` |
| `test_regulatory_verbatim_requires_locus` | — | Entries with `provenance=regulatory_verbatim` | `locus` present with instrument + article | `registry` |
| `test_regulatory_verbatim_number_appears_in_pinned_span` | RTS Art. 16; RTS Art. 18 Annex; AMLR Art. 19, 80 | For each such number, its string form | Number occurs in the pinned span text. Normalise `30` / `EUR 30` / `30 000` separators before matching. | `registry`, `corpus` |
| `test_operator_configured_requires_rationale` | — | Entries with `provenance=operator_configured` | `rationale` present, >= 40 characters (blocks "because") | `registry` |
| `test_base_act_celex_format` | — | Every base-act citation | Matches `^3\d{4}[LRD]\d{4}$` | `registry` |
| `test_consolidated_celex_format` | — | Every consolidated citation | Matches `^0\d{4}[LRD]\d{4}-\d{8}$` | `registry` |
| `test_no_enforcing_obligation_cites_pending_instrument` | PSD3/PSR (`status=pending`, no CELEX) | All enforcing obligations | None references a `pending` or `superseded` instrument | `registry` |
| `test_future_applies_from_requires_predecessor` | AMLR Art. 90 (`2027-07-10`) | Entries whose `applies_from` > today | Each carries `predecessor_in_force` naming AMLD4/5 + national law | `registry`, `frozen_date` |
| `test_risk_based_obligation_has_no_amount_field` | AMLR Art. 26(1), Art. 69(1)(a) | `dataclasses.fields(RiskBasedObligation)` | No field of type `Decimal` or named `amount`/`threshold`. This is the structural guard against reinventing EUR 500. | — |
| `test_reference_point_requires_governs` | AMLR 19(1)(b), 19(4), 80; TFR 5(3) | Every `ReferencePoint` | Non-empty `governs` string, so a number never travels without its meaning | `registry` |
| `test_the_two_amlr_ten_thousands_are_distinct_entries` | AMLR Art. 19(1)(b) vs Art. 80 | Both EUR 10 000 figures | Different `locus`; one `governs` a CDD trigger, the other a payment prohibition | `registry` |
| `test_every_obligation_declares_addressee` | — | All obligations | `addressee` in `{payment_service_provider, obliged_entity, controller, deployer}` | `registry` |
| `test_sdk_role_is_contributes_only` | — | All obligations | `sdk_role == "contributes_only"`; no entry claims the SDK is the addressee | `registry` |
| `test_exemption_requiring_institutional_fact_demands_assertion` | RTS Art. 18(1) | TRA exemption | `deployer_assertion_required` set; unasserted means unavailable | `registry` |
| `test_pinned_span_hash_matches_recorded` | all pinned | Each corpus file | Recomputed SHA-256 equals stored value | `corpus` |
| `test_pinned_span_records_legal_value` | all pinned | Each corpus file | `legal_value` present; consolidated spans marked `consolidated_no_legal_value` | `corpus` |
| `test_every_instrument_has_review_by` | — | Manifest | Every instrument has a `review_by` date | `registry` |
| `test_no_instrument_review_is_overdue` | — | `review_by` vs today | No overdue instrument. **The drift alarm** — goes red with no code change. Runs in the nightly CI job. | `registry` |


**Added during implementation (Day 1).** Six rows beyond the original 18, each prompted by the
code rather than by planning:

| Test | Why it was needed |
|---|---|
| `test_obligation_text_is_a_substring_of_its_pinned_span` | Enforces "quote, don't paraphrase" on the registry itself, not just on test docstrings. The strongest single meta-test in the file, and the precursor to Day 6's entailment check. |
| `test_constructing_verbatim_number_without_locus_is_rejected` | Proves the provenance rule fails at construction, not merely at audit |
| `test_constructing_configured_number_without_rationale_is_rejected` | As above, for the operator-configured branch |
| `test_only_pending_instruments_may_lack_a_celex` | PSD3/PSR has no CELEX; the type must permit that for pending instruments only |
| `test_no_enforcing_obligation_cites_a_not_yet_applicable_instrument` | Non-trivial where the original pending-status test was vacuous: AMLR is in force but applies from 10 July 2027, so its obligations must be non-enforcing today |
| `test_every_exemption_only_relaxes` | Structural guard on polarity -- an exemption can never create a duty |
| `test_no_reference_point_is_enforced` | Keeps cited figures cited: the place a correct number goes so nobody invents one |

Two planned rows were dropped as duplicates of section 10's corpus checks
(`test_pinned_span_hash_matches_recorded`, `test_pinned_span_records_legal_value`).

---

## 2. Taxonomy and stacking decorators — `tests/test_decorators.py`

| Test | Inputs / boundary | Expected | May assume |
|---|---|---|---|
| `test_single_decorator_registers_category` | `@regulated_tool(FinancialCategory.PSD2_PAYMENT_EXECUTION)` on a stub | Registry entry exists with that one category | — |
| `test_stacked_decorators_register_both` | PSD2 + GDPR stacked | Flattened category set == both | — |
| `test_duplicate_category_deduplicated` | Same category applied twice | Set has one member | — |
| `test_stacking_order_is_irrelevant` | (PSD2, GDPR) vs (GDPR, PSD2) | Identical flattened sets | — |
| `test_registry_populated_at_import_time` | Import a fixture module, call nothing | Registry non-empty immediately after import | — |
| `test_metadata_captures_module_and_qualname` | Decorated method inside a class | Entry records `module` and dotted `qualname` | — |
| `test_decorator_preserves_function_identity` | Decorated function | `__name__`, `__doc__`, `inspect.signature` unchanged; return value unchanged | — |
| `test_non_enum_category_rejected` | `@regulated_tool("PSD2")` (string) | `TypeError` at decoration time, not at call time | — |
| `test_enum_carries_no_legal_claim` | Introspect `FinancialCategory` members | No member carries a threshold, amount, or citation attribute. Enforces the PLAN.md §5.2 coverage/correctness boundary. | — |

---

## 3. Agent gate — `tests/test_agent_gate.py`

| Test | Inputs / boundary | Expected | May assume |
|---|---|---|---|
| `test_constructs_when_all_tools_registered` | 3 decorated tools | Construction succeeds | `registry` |
| `test_undecorated_tool_raises` | 1 undecorated callable | `UnregulatedToolError` | `registry` |
| `test_partial_registration_still_fails` | 3 decorated + 1 undecorated | Raises; does not silently drop the offender | `registry` |
| `test_error_names_the_offending_callable` | As above | Message contains the callable's qualname | — |
| `test_error_names_the_remedy` | As above | Message names the decorator and an example | — |
| `test_error_is_not_import_error` | As above | `not isinstance(exc, ImportError)`. Explicit regression guard against reverting to the wrong exception type (PLAN.md §2.5). | — |
| `test_empty_tool_list_is_permitted` | `tools=[]` | Constructs. Documented decision: an agent with no tools has nothing to classify. | — |

---

## 4. Bypass suite — `tests/bypass/`

Four documented attempts to defeat the framework, kept as permanently failing attacks. Note that in shift-left scope the direct-call and TOCTOU attacks take different forms than they would at runtime — direct-call becomes a static structural rule (§5), and TOCTOU lands on the linter's write path.

| Test | Attack | Expected | May assume |
|---|---|---|---|
| `test_subclass_cannot_override_enforcement` | Subclass the tool base and override the validation method to return `True` | Gate still rejects. Requires enforcement to live in the calling wrapper, not an overridable method. | `registry` |
| `test_attribute_spoofing_does_not_satisfy_the_gate` | Hand-set `fn._regulated = True` without decorating | Gate still rejects — it consults the registry, not a function attribute | `registry` |
| `test_fails_closed_when_registry_unavailable` | Make registry import/lookup raise | Construction raises; never passes by default | — |
| `test_fail_open_is_not_reachable_in_any_shield` | Inject an exception into each validation path in turn | Every path yields reject, not allow. Parametrised over all validators. | — |

---

## 5. AST linter — detection — `tests/test_ast_linter.py`

| Test | Inputs / boundary | Expected | May assume |
|---|---|---|---|
| `test_flags_by_parameter_name` | `def transfer(amount: Decimal, iban: str)` | Flagged; reported line == `def` line | `tmp_source_file` |
| `test_flags_by_type_annotation_only` | `def move(src: AccountRef, dst: AccountRef, v: Money)` — no name tokens | Flagged. Guards the false-negative direction. | `tmp_source_file` |
| `test_flags_unclassified_public_function_in_module_importing_bank_client` | Module imports `fake_bank_client`; one undecorated public function | Flagged. This is the concrete definition of "taxonomy drift". | `tmp_source_file`, `fake_bank_client` |
| `test_does_not_flag_private_function` | `def _helper(amount)` in the same module | Not flagged. Documented scope: structural rule covers public functions. | `tmp_source_file` |
| `test_does_not_flag_already_decorated` | Decorated financial function | Not flagged (idempotency precondition) | `tmp_source_file` |
| `test_no_false_positive_on_unrelated_function` | `def format_date(d: date) -> str` | Not flagged | `tmp_source_file` |
| `test_reports_decorator_line_not_def_line_when_decorated` | Function with one existing unrelated decorator | Insertion point == `decorator_list[0].lineno`, not `node.lineno` | `tmp_source_file` |
| `test_detects_async_def` | `async def transfer(amount: Decimal)` | Flagged | `tmp_source_file` |

## 5b. AST linter — `--fix` write path

Every row asserts on **bytes**, not on a parsed tree.

| Test | Inputs / boundary | Expected | May assume |
|---|---|---|---|
| `test_inserts_above_existing_decorators` | Function with `@staticmethod` already present | New decorator lands above it | `tmp_source_file` |
| `test_preserves_comments` | File with a module docstring, a block comment, and a trailing inline comment | All comment bytes identical after fix | `tmp_source_file` |
| `test_preserves_line_endings` | Same file as LF and as CRLF | Ending style unchanged in both runs | `tmp_source_file` (parametrised) |
| `test_preserves_blank_line_structure` | Two blank lines between defs | Unchanged | `tmp_source_file` |
| `test_matches_indentation_inside_class` | Method indented 4 spaces | Decorator inserted at 4 spaces | `tmp_source_file` |
| `test_handles_multiline_signature` | Signature spanning 4 lines | Correct insertion; signature untouched | `tmp_source_file` |
| `test_handles_async_def` | `async def` | Correct insertion | `tmp_source_file` |
| `test_multiple_insertions_one_file` | 3 target functions at lines 10, 25, 40 | All 3 correct — proves descending-order edits | `tmp_source_file` |
| `test_inserts_import_after_future_and_docstring` | File with docstring + `from __future__ import annotations` | Import lands after both, before other imports | `tmp_source_file` |
| `test_does_not_duplicate_existing_import` | Import already present | No second import | `tmp_source_file` |
| `test_fix_is_idempotent` | Run `--fix` twice | Second run writes nothing; bytes identical to first result | `tmp_source_file` |
| `test_aborts_when_reparse_changes_function_set` | Sabotage hook corrupting the modified source before verification | No write; original bytes intact; non-zero exit | `tmp_source_file` |
| `test_aborts_if_file_changed_since_read` | Mutate the file between read and write | No write; non-zero exit. **TOCTOU on the write path.** | `tmp_source_file` |
| `test_writes_atomically` | Simulate failure during write | No partial file, no leftover temp artifacts in the directory | `tmp_source_file` |
| `test_llm_timeout_leaves_file_byte_identical` | `recording_llm` raises `TimeoutError` | Zero bytes changed; non-zero exit; developer not blocked from other work | `tmp_source_file`, `recording_llm` |
| `test_llm_malformed_response_leaves_file_byte_identical` | `recording_llm` returns invalid JSON | As above | `tmp_source_file`, `recording_llm` |
| `test_llm_is_never_called_for_unambiguous_target` | Clear PSD2 payment function | `recording_llm.call_count == 0`. Enforces PLAN.md §2.2. | `tmp_source_file`, `recording_llm` |
| `test_passive_run_never_writes` | No `--fix` flag | Bytes identical; findings reported | `tmp_source_file` |

---

## 6. SCA obligation — `tests/spec/test_spec_psd2_sca.py`

Helper: `remote(amount, counters=FRESH, **kw)` builds a remote-electronic EUR context. `FRESH = (Decimal("0"), 0)`, `EXHAUSTED = (Decimal("100.01"), 6)`.

### 6a. Default duty

| Test | Cites | Inputs | Expected |
|---|---|---|---|
| `test_sca_is_default_at_every_amount` | PSD2 Art. 97(1)(b) | `EXHAUSTED` counters, no token; amounts `0.01, 29.99, 30.00, 30.01, 1_000_000.00` | All BLOCK. No de minimis floor. |
| `test_token_present_allows_with_sca_basis` | PSD2 Art. 97(1)(b) | Any amount + valid step-up token | `ALLOW_SCA_APPLIED`, basis = Art. 97(1)(b) |
| `test_no_allow_without_cited_basis` | PSD2 Art. 97(1)(b) | Any ALLOW outcome | `basis` populated and `obligation_text` non-empty |
| `test_dynamic_linking_scoped_to_remote` | PSD2 Art. 97(2) | Non-remote electronic transaction | Dynamic-linking requirement not asserted. Guards the 97(2) scope precision. |

### 6b. Art. 16 low-value remote exemption

| Test | Inputs | Expected |
|---|---|---|
| `test_limb_a_boundary` | `30.00` / `30.01`, FRESH | exempt / BLOCK |
| `test_limb_b_boundary` | amount `10.00`, cumulative `100.00` / `100.01`, count `99` | exempt / BLOCK |
| `test_limb_c_boundary` | amount `10.00`, cumulative `5000.00`, count `5` / `6` | exempt / BLOCK |
| `test_limbs_b_and_c_are_disjunctive` | amount `10.00`, cumulative `999.00`, count `3` | exempt — b fails, c carries it. **The most commonly mis-stated point.** |
| `test_limb_a_conjunctive_with_bc_disjunction` | amount `40.00`, FRESH | BLOCK |
| `test_not_applicable_outside_remote_electronic` | `contactless_pos`, amount `10.00` | Art. 16 not applied (Art. 11 governs) |
| `test_not_applicable_to_non_eur_without_conversion_policy` | amount `250 NOK` | Explicit conversion policy applied or BLOCK — never silent EUR comparison. Cites EBA Q&A 2018_4040; conversion is the SDK's declared choice, not law. |

### 6c. Art. 11 contactless POS

| Test | Inputs | Expected |
|---|---|---|
| `test_art11_limb_boundaries` | `50.00` / `50.01`; cumulative `150.00` / `150.01`; count `5` / `6` | exempt / BLOCK at each |
| `test_art11_counter_is_per_instrument_not_per_payer` | Two instruments, same payer, each at count 4 | Both exempt — counters do not pool |

### 6d. Art. 13 trusted beneficiaries

| Test | Inputs | Expected |
|---|---|---|
| `test_trusted_beneficiary_has_no_amount_cap` | Listed beneficiary, amount `1_000_000.00` | exempt. **Regression guard against inventing a ceiling.** |
| `test_sca_required_to_create_or_amend_list` | List mutation operation | BLOCK absent token |

### 6e. Art. 18 transaction risk analysis

| Test | Inputs (amount, rate, type) | Expected |
|---|---|---|
| `test_annex_bands_card` | `(500, 0.0001)`, `(500, 0.0002)`, `(250, 0.0006)`, `(250, 0.0007)`, `(100, 0.0013)`, `(100, 0.0014)` | exempt / BLOCK alternating |
| `test_annex_bands_credit_transfer` | `(500, 0.00005)`, `(500, 0.00006)`, `(250, 0.0001)`, `(100, 0.00015)` | exempt / BLOCK / exempt / exempt |
| `test_amount_above_band_blocks_despite_rate` | `(100.01, 0.0013)` | BLOCK |
| `test_unavailable_without_attestation` | `(100.00, no attestation)` | BLOCK — the SDK cannot self-certify a PSP fraud rate |

### 6f. Properties — `tests/properties/test_properties_sca.py`

| Test | Assertion |
|---|---|
| `test_verdict_is_not_a_function_of_amount_alone` | **Keystone.** For all `amount <= 30`: FRESH => exempt, EXHAUSTED => BLOCK. Fails on every input under a threshold implementation. |
| `test_exemption_monotone_decreasing_in_amount` | If `hi` is exempt under state `c`, then any `lo <= hi` is exempt under the same `c` |
| `test_every_allow_names_exemption_from_closed_union` | Any `ALLOW_EXEMPT` has `exempt_under in EXEMPTIONS` and `basis is exempt_under.provision` |
| `test_art16_limb_a_is_necessary` | No session state exempts above EUR 30 under Art. 16 |
| `test_no_exemption_without_required_facts` | TRA never exempts absent an attestation, for all amounts and rates |

### 6g. Money representation

| Test | Assertion |
|---|---|
| `test_amount_rejects_float_input` | Constructing a context with a `float` amount raises |
| `test_decimal_boundary_is_exact` | `Decimal("29.99") + Decimal("0.01") == Decimal("30.00")` exactly, and that amount is exempt when FRESH |
| `test_float_arithmetic_would_have_slipped_the_limit` | Demonstrates a value that crosses the boundary under binary float but not under `Decimal`. Documents why `Decimal` is mandatory. |

---

## 7. Nordic identifiers — `tests/test_identifiers.py`

| Test | Cites | Inputs / boundary | Expected |
|---|---|---|---|
| `test_no_generator_produces_valid_checksums` | — | 1000 generated fodselsnummer | All pass both mod-11 passes |
| `test_no_generator_redraws_on_control_digit_ten` | — | Force a candidate whose `k1` computes to 10 | Candidate rejected and redrawn; never emitted |
| `test_no_weights_are_exact` | — | Known-valid reference number | Validates with `[3,7,6,1,8,9,4,5,2]` / `[5,4,3,2,7,6,5,4,3,2]` |
| `test_no_remainder_one_maps_to_eleven_then_zero` | — | Candidate with `sum mod 11 == 0` | Control digit 0, not 11 |
| `test_no_d_number_recognised` | — | First digit + 4 | Recognised as a D-number variant |
| `test_se_luhn_over_ten_digit_form` | — | Valid 10-digit personnummer | Validates |
| `test_se_twelve_digit_normalised_before_luhn` | — | Same number in 12-digit century form | Validates — normalised, not rejected |
| `test_se_samordningsnummer_accepted` | — | Day + 60 | Recognised |
| `test_fi_check_character_mod31` | — | Known-valid HETU | Validates against `0123456789ABCDEFHJKLMNPRSTUVWXY` |
| `test_fi_omitted_letters_rejected_as_check_char` | — | Check chars `G`, `I`, `O`, `Q`, `Z` | Rejected — alphabet is 31, not 36 |
| `test_fi_accepts_all_century_signs` | — | `+`, `-`, `A`, and `Y X W V U B C D E F` | All accepted. A validator taking only `-`/`+`/`A` rejects numbers issued since 19 Dec 2023. |
| `test_dk_does_not_hard_reject_on_mod11_failure` | — | Format-valid CPR failing mod-11 | **Accepted as valid.** mod-11 has not been guaranteed since 1 Oct 2007. |
| `test_dk_pre_2007_birthdate_also_may_fail_mod11` | — | CPR with a pre-2007 birth date failing mod-11 | Accepted. 1 January sequences went first — the default date for immigrants with unknown birth dates. Named in the test docstring as a **disparate-impact** regression guard. |
| `test_iban_mod97` | ISO 13616 | Valid and invalid IBANs | Correct verdicts |
| `test_lookalikes_do_not_yield_high_confidence_hits` | — | 11-digit order number, `20260103`, `+4712345678`, a 16-digit card number | No high-confidence identifier hit. **Precision test** — over-redaction destroys agent utility. |
| `test_detection_recall_on_synthetic_corpus` | GDPR Art. 5(1)(c) | 500 synthetic IDs embedded in prose | Recall reported; threshold gated in CI |
| `test_no_real_personal_data_in_repository` | GDPR Art. 5(1)(c) | Scan `tests/fixtures/` | All identifiers are generator-produced; no committed real data |

---

## 8. Telemetry — `tests/test_telemetry.py`

| Test | Inputs | Expected |
|---|---|---|
| `test_posts_valid_prometheus_text_format` | Completed mock run | POST body parses as Prometheus text exposition |
| `test_grouping_keys_include_job_branch_commit` | Two runs, different commits | Distinct grouping keys; neither overwrites the other |
| `test_per_build_values_are_gauges_not_counters` | Two sequential pushes | Values do not silently accumulate or clobber. **Regression guard on Pushgateway semantics** (PLAN.md §5.5). |
| `test_label_values_drawn_from_registry` | All emittable labels | Every provision label exists in the registry — bounded cardinality |
| `test_no_amount_in_labels` | Run producing a blocked EUR 9000 action | No amount appears in any label |
| `test_no_identifier_reaches_the_gateway` | Payload containing a synthetic fodselsnummer | That string appears nowhere in the pushed body |
| `test_push_failure_warns_and_does_not_fail_build` | Gateway unreachable | Warning; exit 0. Documented decision: a lost metric is not a compliance breach. |
| `test_evidence_report_failure_does_fail_build` | `compliance_report.md` cannot be written | Build fails. Evidence is the deliverable; telemetry is convenience. |

---

## 9. Entailment and the self-regression eval — `tests/test_entailment.py`

Day 6. The flagship section.

| Test | Inputs | Expected |
|---|---|---|
| `test_text_drifted_detected` | Mutate one byte of a pinned span | `TEXT_DRIFTED` |
| `test_claim_unsupported_detected` | `poisoned_registry`: `threshold=30`, `applies_when=AMOUNT_ABOVE`, pinned to the genuine Art. 16 text | `CLAIM_UNSUPPORTED`. **The failure mode a byte-diff cannot see** — a correct citation beside a wrong interpretation. |
| `test_source_unreachable_is_distinct_and_not_fail_open` | Network fixture raising | `SOURCE_UNREACHABLE`, distinct from the other two; does not report success |
| `test_entailment_never_proposes_a_number` | Any failure mode | Output contains no suggested replacement value. A bot changing a threshold from retrieval is the laundering failure. |
| `test_rejects_sca_above_thirty_claim` | Claim: "PSD2 requires SCA above EUR 30" vs pinned Art. 16 | Rejected. **Project's own original error, permanently.** |
| `test_rejects_invented_aml_reporting_threshold` | Claim: "EUR 500 AML reporting threshold" vs pinned AMLR Art. 69(1)(a) | Rejected, citing "regardless of the amount involved" |
| `test_rejects_limited_cdd_mischaracterisation` | Claim: "EUR 3 000 limited CDD" vs pinned AMLR Art. 19(4) | Rejected — it is a full CDD trigger |
| `test_rejects_amlr_article_nine_for_monitoring` | Claim: monitoring duty is Art. 9 vs pinned Art. 26(1) | Rejected. Captures the discrepancy that three research briefs disagreed on. |
| `test_no_forbidden_phrase_in_repository` | Grep every tracked file for the PLAN.md §4.6 list | Zero matches. Repo-wide string regression against the must-not-publish list. |

---

## 10. Generated artifacts — `tests/test_generated_docs.py`

| Test | Expected |
|---|---|
| `test_obligations_md_in_sync` | `render(registry) == docs/obligations.md`. Code -> doc, never doc -> code. |
| `test_every_registry_entry_appears_in_register` | No silent omissions |
| `test_register_shows_verification_status` | Each row carries `OK` / `RV` / `UNV` so a reader sees the project's own confidence |

---

## 11. Corpus ingestion — `tests/test_ingestion.py`

Day 0. Previously assumed rather than validated: if chunking is wrong, every later PIN step pins the wrong text and the whole discipline silently inverts.

| Test | Inputs / boundary | Expected | May assume |
|---|---|---|---|
| `test_chunk_extracts_only_target_article` | Consolidated RTS expression, request `art_16` | Text contains Art. 16 only — no bleed from Art. 15 or Art. 17 | recorded CELLAR response fixture |
| `test_chunk_handles_inserted_article` | Request `art_10a` | Extracted distinctly from `art_10`; neither contaminates the other | fixture |
| `test_chunk_captures_article_title` | `art_16` | Includes the `eli-title` subdivision ("Low-value transactions") | fixture |
| `test_annex_without_eli_class_is_extracted` | `anx_1`, a bare `<div>` with no class | All six Art. 18 fraud-rate figures present. **Replaces** an earlier row premised on initial acts lacking `eli-subdivision`; base acts do carry it (verified 2026-10-03). | fixture |
| `test_unknown_subdivision_raises_rather_than_guessing` | Request `art_999` | `SubdivisionNotFound` — silently returning empty or nearest-match text is the dangerous failure | fixture |
| `test_consolidated_version_selection` | Target date `2024-01-01` against versions `20151223`, `20240408`, `20250117` | Selects `20151223` — greatest consolidation <= date | fixture |
| `test_hash_recorded_at_ingest_time` | Any chunk | `sha256` written alongside the text | — |
| `test_legal_value_recorded` | Consolidated chunk | Marked `consolidated_no_legal_value` | — |
| `test_notice_file_present_and_complete` | `corpus/NOTICE` | Exists; names Decision 2011/833/EU, source acknowledgement, non-distortion, non-liability | — |
| `test_corpus_load_makes_no_network_call` | Load committed corpus with network fixture that fails on any call | Zero calls. Proves the corpus is pinned, not fetched. | network fixture |

---

## 12. CI self-validation — `tests/test_workflow.py`

Day 5. **The most important gap in the previous draft.** Everything else tests what the gate runs; nothing tested the gate. A workflow with a swallowed exit code is a gate that does not gate, and it would silently invalidate every other claim in the repo.

| Test | Inputs / boundary | Expected | May assume |
|---|---|---|---|
| `test_failing_spec_test_fails_the_run` | Inject a deliberately failing test into a scratch invocation | Non-zero exit propagates | — |
| `test_no_continue_on_error_on_gate_steps` | Parse `security-audit.yml` | No gate step carries `continue-on-error: true` | PyYAML or stdlib parsing |
| `test_fast_gate_references_no_secrets` | Parse the fast-gate job | No `secrets.` reference anywhere in it. Structural proof of the no-API-key claim. | — |
| `test_fast_gate_passes_with_credentials_scrubbed` | Run the fast gate with all `*_API_KEY` env vars removed | Green | — |
| `test_compliance_report_is_generated` | Completed run | `compliance_report.md` exists and is non-empty | — |
| `test_compliance_report_written_to_step_summary` | Completed run | Content written to `$GITHUB_STEP_SUMMARY` | env fixture |
| `test_entailment_gate_is_a_separate_job` | Parse workflow | Entailment runs as its own job with its own exit status, so a drift failure is distinguishable from a logic failure | — |
| `test_nightly_job_includes_staleness_check` | Parse workflow | The cron job runs `test_no_instrument_review_is_overdue` | — |

---

## 13. Example bot — `tests/test_example.py`

Day 5–6. The file a reviewer actually runs first. A broken example is the worst available first impression.

| Test | Inputs / boundary | Expected | May assume |
|---|---|---|---|
| `test_example_runs_to_completion` | `python examples/sample_banking_bot.py` via subprocess | Exit 0 | — |
| `test_example_needs_no_api_key` | Same, credentials scrubbed | Exit 0 | — |
| `test_example_demonstrates_a_rejection` | Same | Output or report shows at least one rejected action with its cited basis | — |
| `test_example_uses_only_the_public_api` | Static scan of its imports | No private module paths. Proves the documented surface is actually sufficient. | — |
| `test_readme_quickstart_matches_example_file` | README code block vs file content | Identical. Prevents the most common form of doc rot. | — |

---

## 14. Packaging and hygiene — `tests/test_packaging.py`

| Test | Expected |
|---|---|
| `test_package_installs_editable` | `pip install -e .` succeeds (requires `pyproject.toml` — add to the layout) |
| `test_documented_public_api_is_importable` | Every name in the README quickstart imports from the top-level package |
| `test_requirements_contains_no_stdlib_modules` | No `ast`, `decimal`, `json`, `hashlib`, `re`. Explicit guard against the error in the original spec. |
| `test_mypy_strict_is_clean` | `mypy --strict` exits 0 |
| `test_suite_is_deterministic` | Two consecutive runs produce identical pass/fail sets |
| `test_no_test_depends_on_execution_order` | Suite passes under randomised ordering |

---

## 15. Mutation testing — optional, highest-signal

Not required for the sprint; the strongest single answer to "how do you know your tests are any good?"

Run `mutmut` (or `cosmic-ray`) against `regulation/psd2_sca.py` and require **zero surviving mutants** in the limb logic. Flip `and` to `or` in the Art. 16 conjunction, invert a comparison, change `30` to `31` — each mutation must kill at least one test.

This matters here more than on an ordinary project, because the failure mode that started it was precisely *a passing test protecting a wrong implementation*. Mutation testing is the tool that detects that class directly. A README line reading "0 surviving mutants in the SCA limb logic" is worth more than three extra shields.

---

## 16. Counts and sequencing

| Deliverable | Tests | Day |
|---|---|---|
| Corpus ingestion | 18 (all green) | 0 |
| Corpus identity / golden membership | 13 (all green) | 1b |
| Registry + provenance | 24 (all green) | 1 |
| Taxonomy + decorators | 11 (all green) | 1 |
| Agent gate | 8 (all green) | 2 |
| Bypass suite | 6 (all green) | 2 |
| Linter detection | 8 | 3 |
| Linter write path | 18 | 3 |
| SCA obligation | 22 | 4 |
| SCA properties | 5 | 4 |
| Money representation | 3 | 4 |
| Identifiers | 17 | 4 |
| Telemetry | 8 | 5 |
| CI self-validation | 8 | 5 |
| Example bot | 5 | 5–6 |
| Packaging + hygiene | 6 | 5 |
| Entailment + eval | 9 | 6 |
| Generated docs | 3 | 6 |
| **Total** | **186** | |

Day 4 remains the heaviest at 47 tests and carries the keystone property. If the week slips, cut Art. 11 and Art. 13 coverage before cutting anything in §6a, §6b or §6f — the default duty, the Art. 16 limb structure and the keystone property are the project's correctness claim. Cut §15 first of all; it is explicitly optional.
