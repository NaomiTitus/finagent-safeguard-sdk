# FinAgent-SafeGuard SDK — Implementation Plan

**Status:** design locked, pre-implementation
**Date:** 2026-10-03
**Scope:** shift-left (CI / import-time / authoring-time) only. Runtime enforcement is deferred to Future Work (§13).

---

## 1. Mission

A type-safe Python framework that lets engineering teams build AI agents for EU/Nordic banking and prove, in CI, that their agent code is classified, bounded and evidenced against GDPR, PSD2 and AML obligations — before it reaches a production branch.

The product is **developer governance**, not a transaction gate. The deliverable a bank's second line would recognise is the per-build evidence artifact, not a blocked payment.

### 1.1 In scope

- Static analysis of agent/tool code (AST detection), with an opt-in `--fix` that inserts missing decorators.
- Import-time registration and a fail-closed agent construction gate.
- A typed regulatory registry with pinned verbatim provisions and machine-checked provenance.
- Property-based boundary verification (Hypothesis).
- CI gating, with an evidence report attached to the GitHub Step Summary.
- Pre-production quality observability (Pushgateway → Grafana).

### 1.2 Out of scope, and why

| Excluded | Reason |
|---|---|
| Live transaction proxies | Not a payment rail; the SDK is not a PSP. |
| Production network interceptors | Would place the SDK in the request path, which the shift-left framing exists to avoid. |
| Runtime log-string redaction as a product feature | Belongs to the runtime extension (§13); unverifiable in CI. |
| LLM or RAG anywhere in an enforcement decision | Non-determinism in a compliance verdict is not auditable. |
| Any claim of regulatory compliance or certification | The SDK is not the addressee of any obligation (§4.3). |

**Latency claim, stated honestly:** zero production request-path network latency, because enforcement is at authoring time, import time and in CI. Import-time registration has a measurable cost; publish it. Do not claim "0.00 ms" — it is falsifiable and invites the wrong question.

---

## 2. Non-negotiable corrections

Read before writing any code. These override anything in earlier specifications.

### 2.1 The SCA property test must not be amount-only

A test asserting only *"amount > EUR 30 without a token blocks"* is true but **insufficient**, and under minimum-implementation TDD insufficiency becomes incorrectness. The minimal passing implementation is:

```python
if amount > 30 and not token:
    return BLOCK
return ALLOW          # WRONG: allows EUR 25 with exhausted counters
```

Under PSD2 Art. 97(1), SCA is the **default at every amount**. RTS Art. 16 is a *permission not to apply* it, structured `(a) amount <= EUR 30 AND ((b) cumulative since last SCA <= EUR 100 OR (c) <= 5 consecutive)`.

The keystone property, which fails on every input under a threshold implementation:

```python
@given(amount=amounts)
def test_verdict_is_not_a_function_of_amount_alone(amount):
    assume(amount <= Decimal("30"))
    fresh     = SessionCounters(Decimal("0"),      0)
    exhausted = SessionCounters(Decimal("100.01"), 6)
    assert decide(remote(amount, fresh)).outcome     is Outcome.ALLOW_EXEMPT
    assert decide(remote(amount, exhausted)).outcome is Outcome.BLOCK_SCA_REQUIRED
```

Negative or zero amounts are **input validation failures**, not compliance verdicts. Keep them in a separate error class, metric and audit path.

### 2.2 No LLM in a file-write path

The decorator to insert is determined by the taxonomy enum and the detected concern — a lookup, not a generation task. Local inference (Ollama, `localhost:11434`) is retained for one job only: **suggesting a category when the heuristic is ambiguous**, surfaced to the developer. It never decides what bytes land on disk.

### 2.3 Two different fail-closed semantics

| Context | Fail-closed means |
|---|---|
| CI gate | Exception or timeout => build FAILS. |
| Developer `--fix` | Exception or timeout => **make no change**, exit non-zero with a clear message. Never block the developer's work. |

### 2.4 `ast` cannot round-trip source

`ast` discards comments and `ast.unparse()` reformats everything. Use `ast` for detection; use line-based text insertion for the fix (§5.1).

### 2.5 Exception types

Passing an unregistered tool raises a custom `UnregulatedToolError`, not `ImportError`. `ImportError` means a module failed to import; misusing it misleads tooling and reads as a Python-semantics error.

### 2.6 `requirements.txt`

`ast` is standard library. Do not list it. Baseline: `pytest`, `hypothesis`, `prometheus-client`, plus `httpx` or stdlib `urllib.request` for the Pushgateway POST.

---

## 3. Methodology: PIN -> RED -> GREEN -> REFACTOR

Standard TDD with one step in front, because in this project the specification is the Official Journal, not the ticket.

| Step | Action |
|---|---|
| **PIN** | Fetch the governing provision from CELLAR, commit the verbatim span to `corpus/` with its SHA-256, and write the test docstring as a quote from it. |
| **RED** | Write the test. Run pytest. Confirm it fails for the intended reason. |
| **GREEN** | Minimum production code to pass. |
| **REFACTOR** | Types, structure, naming. Suite stays green. |

**Rule:** no test asserting a legal consequence may be written before its provision is pinned. A test whose docstring contains a paraphrase instead of a quote is not done.

---

## 4. The regulatory substrate

### 4.1 Verified provision register

Status: `OK` = verified against primary or official reproduction; `RV` = re-verify via CELLAR before publishing; `UNV` = unverified, must be hedged in prose.

| Provision | Content | Status |
|---|---|---|
| PSD2 (32015L2366) Art. 97(1)(a)-(c) | SCA default duty: account access, payment initiation, remote action implying fraud risk | OK (pinned 2026-10-03) |
| PSD2 Art. 97(2) | Dynamic linking — **electronic remote transactions only**, not all of 97(1)(b) | OK (pinned 2026-10-03) |
| RTS on SCA (32018R0389) Art. 16 | "shall be allowed not to apply"; `(a) <= EUR 30 AND ((b) cum <= EUR 100 OR (c) <= 5 consecutive)` | OK (fetched from CELLAR) |
| RTS Art. 18 + Annex | TRA exemption. ETV/fraud-rate: EUR 500 @ 0.01%/0.005%; EUR 250 @ 0.06%/0.01%; EUR 100 @ 0.13%/0.015% (card / credit transfer). Requires a PSP fraud-rate attestation the SDK cannot self-certify. | OK |
| RTS Art. 11 | Contactless POS: EUR 50 / EUR 150 / 5. Counter is per **payment instrument**, not per payer. | OK |
| RTS Art. 13 | Trusted beneficiaries: **no amount cap, no count cap.** SCA attaches to creating/amending the list; list held at the ASPSP. Do not invent a ceiling. | OK |
| GDPR (32016R0679) Art. 5(1)(c), 25, 32 | Minimisation, by-design, security of processing | OK |
| GDPR Art. 9(1) | Exhaustive special-category list. A national ID number is **not** on it. | OK |
| GDPR Art. 87 | Member States may set conditions for national identification numbers | OK |
| GDPR Ch. V (44-49) | Calling a non-EEA model endpoint is a transfer; adequacy vs SCCs is a live question | OK (pinned 2026-10-03) |
| AMLR (32024R1624) Art. 19(1)(b) | CDD trigger, occasional transaction >= EUR 10 000 | OK |
| AMLR Art. 19(4) | **Full** CDD trigger for occasional transactions **in cash** >= EUR 3 000. NOT "limited CDD". | OK |
| AMLR Art. 19(2), 19(3), 19(5) | EUR 1 000 transfers of funds; EUR 1 000 crypto; EUR 2 000 gambling | OK |
| AMLR Art. 26(1) | **Ongoing monitoring of business relationships and transactions.** (Not Art. 9, not Art. 20.) Art. 26(2): refresh max 1 yr higher-risk / 5 yr otherwise. | OK |
| AMLR Art. 69(1)(a) | Report suspicion to FIU "regardless of the amount involved" — **no monetary threshold** | OK |
| AMLR Art. 80 | Substantive **prohibition**: cash payments in trade capped at EUR 10 000 incl. linked operations. Excludes private individuals outside professional capacity and deposits at credit institutions/EMIs/PSPs. | OK |
| AMLR Art. 90 | Applies from **10 July 2027** (2029 for football clubs/agents) | OK |
| TFR (32023R1113) Art. 4(4) | Verification-of-accuracy duty | OK |
| TFR Art. 5(3) with 5(2)(b) | EUR 1 000 derogation **from verification**, not reporting. Intra-EU payer side. Art. 6(2) third-country; Art. 7(3)-(4) payee side. | OK |
| DORA (32022R2554) Art. 28(3) | Register of information: maintain/update at entity + sub-consolidated + consolidated; annual aggregate report; advance notification for critical-or-important functions. Three distinct obligations. | OK |
| DORA Arts. 17-23 | Incident management, classification, reporting. **Art. 23** extends to operational/security **payment-related** incidents for credit institutions, PIs, AISPs, EMIs. | OK |
| DORA Art. 64 | Applies from **17 January 2025** | OK |
| AI Act (32024R1689) Art. 12, 14, 26(6) | Automatic logging; human oversight incl. automation-bias awareness and stop capability; deployer log retention **>= 6 months** for logs under their control | OK |
| AI Act Annex III 5(b) | Creditworthiness/credit scoring of natural persons **in**; financial-fraud detection **carved out** | OK |
| AI Act Art. 6(3) | Not-high-risk derogation **unavailable** where the system performs profiling of natural persons | OK |
| Reg. (EU) 2026/1744 (Digital Omnibus on AI) | Dated 8 July 2026; OJ 24 July 2026; in force 27 July 2026. Annex III: 2 Aug 2026 -> **2 Dec 2027**. Annex I: 2 Aug **2027** -> 2 Aug 2028. Art. 50 **unchanged** at 2 Aug 2026; only Art. 50(2) marking -> 2 Dec 2026. | OK |
| EBA Q&A 2018_4040 | Non-euro thresholds: average ECB reference rate; rounding only where unlikely to breach. **Non-binding Level 3 soft law.** | OK |
| EBA/GL/2025/02 | Published 11 Feb 2025, applies 20 May 2025. Narrows EBA/GL/2019/04 to DORA-covered PSPs and to payment-service-user relationship management only. | OK |
| NO personopplysningsloven s. 12 | Two-limb cumulative: objective need for secure identification AND the method is necessary | OK (lovdata) |
| SE Lag (2018:218) 3 kap. 10 | "Clearly justified" weighing test, applying absent consent | OK (riksdagen) |
| FI Tietosuojalaki 1050/2018 s. 29 | Enumerated-purpose list; **expressly includes credit institution and payment service activity** | RV (finlex 404'd; secondary sources only) |
| PSD3 / PSR | Politically agreed 27 Nov 2025; final texts 23 Apr 2026; ECON approved 5 May 2026. **No CELEX, not in the OJ, not applicable.** Latest procedural step disputed among sources. | UNV |

### 4.2 Provenance rule (enforced by meta-test)

Every numeric parameter in the registry declares `provenance`:

| Value | Additional requirement |
|---|---|
| `regulatory_verbatim` | **requires** `locus` (instrument + article + paragraph), and the number must appear in the pinned span |
| `operator_configured` | **requires** a non-empty written `rationale` |
| `vendor_default` | requires `rationale` |

This is the mechanism that makes the EUR 30 / EUR 500 error class hard to commit: writing the locus sends the author to the article.

**Finding (2026-10-03):** EU legal texts spell small cardinals as words. RTS Art. 16(c) reads *"five consecutive individual remote electronic payment transactions"*, not "5", so a rendered numeral is not always a quotation. `NumericParameter` therefore carries a `verbatim_form` field holding the numeral exactly as the Official Journal writes it, and the provenance test checks that string. This makes the discipline stronger, not weaker: you cannot fill the field without reading the wording.

### 4.3 Addressee fields

Every obligation carries `addressee` (`payment_service_provider` | `obliged_entity` | `controller` | `deployer`) and `sdk_role: contributes_only`. Exemptions requiring institutional facts carry `deployer_assertion_required`; unasserted means unavailable, never granted.

### 4.4 Jurisdiction and dates

Per-jurisdiction `applies_from` plus `eea_status`. Verified examples to encode:

- **DORA:** EU 17 Jan 2025; EEA Joint Committee 20 Feb 2025 (decision number single-sourced — verify on efta.int); Norwegian law in force 1 July 2025. A ~5.5-month window where DORA applied in the EU but not Norway.
- **AI Act:** **not applicable in Norway as of Oct 2026.** EEA incorporation pending; draft KI-lov slipped to a bill expected spring 2027. Norwegian applicability is `TBD`, not a date.

### 4.5 Non-euro thresholds (open design question)

Nothing in PSD2 or the RTS addresses conversion. EBA Q&A 2018_4040 specifies the average ECB reference rate and permits rounding only where unlikely to breach — but gives **no fixing date, no averaging window, no revision cadence**, and is non-binding. NOK and SEK float.

**SDK default, declared as the SDK's own choice and not as law:** convert at the ECB reference rate, round **downward** (stricter), never allow the converted threshold to exceed the EUR figure at the prevailing rate. Document the gap in the README.

### 4.6 Must not be published as stated

1. "SCA is required above EUR 30." Art. 16 is permissive.
2. "EUR 3 000 limited CDD." It is a full CDD trigger for cash occasional transactions.
3. "AMLR Art. 9 / Art. 20 imposes ongoing monitoring." It is Art. 26(1).
4. "AI Act Annex I moved from 2 Aug 2026." Baseline was 2 Aug 2027.
5. "The Omnibus changed the Art. 50 application date." It did not; only Art. 50(2) marking moved.
6. "TFR EUR 1 000 is in Art. 5(2)(a)/7(3)." It is Art. 5(3) with 5(2)(b), derogating from Art. 4(4).
7. Any Danish CPR validator that hard-rejects on mod-11 failure (§4.7).
8. Any Finnish HETU validator accepting only `-`, `+`, `A`.
9. Any statement that the AI Act currently applies in Norway.
10. Any PSD3/PSR application date.
11. Any trusted-beneficiary amount ceiling.

### 4.7 Identifier algorithms and their traps

| Identifier | Algorithm | Trap |
|---|---|---|
| NO fodselsnummer | 11 digits. k1 over d1-d9, weights `[3,7,6,1,8,9,4,5,2]`; k2 over d1-d10, weights `[5,4,3,2,7,6,5,4,3,2]`. `k = 11 - (sum mod 11)`. | **`11 -> 0`; `10 -> number invalid`** (generators must redraw; ~1 in 11 candidates unusable). New format planned from 2032 — do not encode as permanent. Third individual digit encodes sex. |
| SE personnummer | Luhn mod-10 | Runs over the **10-digit** form (YYMMDD + 3 birth digits), not the 12-digit century form. Handle `samordningsnummer` (day + 60). |
| FI henkilotunnus | 9-digit value mod 31, indexed into `0123456789ABCDEFHJKLMNPRSTUVWXY` (G, I, O, Q, Z omitted) | **Century signs reformed 2023:** 1900s = `-`, `Y`, `X`, `W`, `V`, `U`; 2000s = `A`, `B`, `C`, `D`, `E`, `F`; 1800s = `+`. Issued since 19 Dec 2023; existing numbers unchanged. |
| DK CPR | 10 digits, mod-11 historically | **mod-11 not guaranteed since 1 Oct 2007.** A pre-2007 birth date does not guarantee validity either — 1 January sequences went first, and that is the default date for immigrants with unknown birth dates. Hard-rejecting fails disproportionately on recent arrivals: a **disparate-impact bug**. Validate format + date plausibility only; generate both passing and failing checksums in fixtures. |
| IBAN | ISO 13616 mod-97 | Pan-European, no jurisdiction axis needed. |

### 4.8 Corpus provenance

Source: CELLAR (`publications.europa.eu`) — SPARQL and REST, unauthenticated. **`eur-lex.europa.eu` returns HTTP 202 with an empty body to programmatic fetch; do not build against it.**

- Articles are marked up as `<div class="eli-subdivision" id="art_16">`. **Correction to an earlier research finding:** base acts served by CELLAR carry these anchors too (verified 2026-10-03 on AMLR, TFR and DORA), so there is no separate initial-act parser path. Prefer the consolidated expression where one exists, because it reflects amendments.
- **The Annex is a bare `<div id="anx_1">` with no class attribute.** The Art. 18 reference fraud rates live there, so the selector must match on the id alone; requiring `class="eli-subdivision"` silently loses the whole table.
- Inserted articles (`art_10a`) are distinct subdivisions and must not contaminate their neighbours (`art_10`).
- "Version in force on date D": enumerate dated consolidated CELEX (`0YYYYLNNNN-YYYYMMDD`), take the greatest <= D.
- Rate limits undocumented. Pin once, commit, never call CELLAR from a request path.
- Each chunk records `celex`, `eli_subdivision`, `text`, `sha256`, `retrieved_at`, `source_url`, `legal_value`.
- `legal_value`: consolidated texts are editorial and have **no legal value**; only the OJ is authentic.
- Known anomaly: CELLAR returned two conflicting end-of-validity dates for PSD2 (`9999-12-31` and `2026-06-18`) with no repealing act. Unexplained. **Do not encode.**
- **Corpus identity.** Per-file hashes are self-consistency only: re-running the pin step
  rewrites text and hash together, so any check that recomputes a file's hash from its own text
  passes through an arbitrary corpus substitution. Identity therefore comes from a **set-level
  digest over `(celex, subdivision_id, sha256, legal_value)`** with the expectation committed
  **in test code, not in `corpus/MANIFEST.json`** -- a manifest compared against the files it was
  generated from is self-consistency again. `legal_value` is in the tuple because flipping a
  consolidated span to `authentic` is a claim about legal authenticity that nothing else would
  catch. `retrieved_at` and `source_url` are excluded, so a re-pin yielding identical text does
  not go red and the test does not become noise. No `--update-golden` affordance exists: updating
  the constant is a hand edit in the same commit as the corpus change. Short form (12 hex) is the
  Prometheus label; the full digest goes in `compliance_report.md`.
- `NOTICE` file required (reuse under Commission Decision 2011/833/EU: acknowledge source, do not distort meaning, Commission non-liability; EU emblem not reproduced).

---

## 5. Deliverables

### 5.1 Auto-remediating code modifier — `cli/linter.py`

**Detect** with `ast`; **write** with line-based text insertion. Stdlib only.

Detection signals (combined, not string-matching alone):

1. Parameter or function name tokens: `amount`, `iban`, `bic`, `recipient`, `payee`, `transfer`, `payment`.
2. Type annotations: `Decimal`, and project newtypes (`Money`, `IBAN`, `AccountRef`).
3. Structural rule: **any module importing the bank client must have every public function classified.** This is the concrete definition of "taxonomy drift".

Insertion correctness requirements:

- Target line is `node.decorator_list[0].lineno` when decorators exist, else `node.lineno` — insert **above existing decorators**.
- Match the indentation of the target line (methods inside classes).
- Handle `async def` and multi-line signatures.
- Idempotent: detect an already-present decorator and skip.
- Insert the import after `__future__` imports and the module docstring, only if absent.
- Apply edits in **descending line order** so earlier inserts do not shift later line numbers.
- Operate on `splitlines(keepends=True)` to preserve line endings.
- **Verify before writing:** re-parse the modified source with `ast.parse`; assert the set of function qualified names is unchanged. Any discrepancy => no write.
- Write atomically: temp file + `os.replace`.
- Local LLM (`localhost:11434`) only for ambiguous-category suggestion, surfaced to the developer, never in the write path. Strict timeout; on failure make no change.

### 5.2 Taxonomy registry — `taxonomy/policies.py`, `core/decorators.py`

Enums (`FinancialCategory.PSD2_PAYMENT_EXECUTION`, `GDPR_PII_PROCESSING`, `AML_TRANSACTION_MONITORING`) with stacking decorators and flattened, deduplicated category lists at import time.

**Boundary:** enums are a **coverage** mechanism — has this function been classified? They are **not** a correctness mechanism. No legal claim lives in an enum; legal content lives in the typed registry with a citation and provenance. Keep these two layers separate or the project reproduces its original error in a tidier wrapper.

### 5.3 IoC agent gate — `core/agent.py`

`BaseCompliantAgent.__init__` walks supplied tools against the import-time registry. Any unregistered tool raises `UnregulatedToolError` immediately. Fail closed.

### 5.4 CI gatekeeper — `.github/workflows/security-audit.yml`

- **Fast gate:** AST coverage, registry integrity meta-tests, Hypothesis properties, identifier validators.
- **Entailment gate:** for every registry entry, re-fetch the pinned provision and report `TEXT_DRIFTED` / `CLAIM_UNSUPPORTED` / `SOURCE_UNREACHABLE` as distinct failures. **Never auto-propose a number** — a bot changing a threshold from retrieval is the laundering failure this project exists to prevent.
- **Evidence:** `compliance_report.md` attached to the GitHub Step Summary.
- **Hard requirement:** the full fast gate passes on a fresh clone with **no API key**. Any model interaction is recorded as a fixture. A separate opt-in job runs live.
- Nightly cron: fail when any instrument's `review_by` date has passed.

### 5.5 Observability — `telemetry/metrics.py`, `docker-compose.yml`

CI run pushes to a Prometheus Pushgateway; local Grafana container reads it.

**Pushgateway caveat:** pushed series persist until deleted and overwrite by grouping key, so `_total` counters do not accumulate across builds. Key by `job` / `branch` / `commit` and treat per-build values as gauges, or trend from a committed file artifact.

Metrics: `ci_compliance_build_failures_total` (by rule type), `registered_compliant_capabilities_total` (adoption velocity), `registry_entailment_failures_total`, `ast_unclassified_functions_total`, import-time registration duration.

Bounded cardinality: provision ID as a label, never verbatim text, never payloads, never amounts as labels.

---

## 6. Repository layout

```text
finagent-safeguard-sdk/
|
+-- .github/workflows/
|   +-- security-audit.yml
|
+-- corpus/                      # pinned verbatim provisions + hashes
|   +-- NOTICE
|   +-- 02018R0389-20230725/art_16.json
|   +-- ...
|
+-- config/
|   +-- prometheus-config.yml
|
+-- docs/
|   +-- obligations.md           # GENERATED from the registry; CI-diffed
|   +-- design.md
|
+-- examples/
|   +-- sample_banking_bot.py
|
+-- finagent_safeguard/
|   +-- __init__.py
|   +-- cli/
|   |   +-- linter.py
|   +-- core/
|   |   +-- agent.py
|   |   +-- decorators.py
|   +-- regulation/
|   |   +-- citation.py          # Instrument, Provision, CELEX validation
|   |   +-- manifest.py          # pinned instruments: status, dates, eea_status
|   |   +-- psd2_sca.py          # Art. 97 duty + closed exemption union
|   |   +-- aml.py               # RiskBasedObligation + ReferencePoints
|   |   +-- gdpr.py
|   |   +-- registry.py
|   |   +-- render.py            # registry -> docs/obligations.md
|   +-- identifiers/
|   |   +-- nordic.py            # validators + synthetic generators
|   +-- taxonomy/
|   |   +-- policies.py
|   +-- telemetry/
|       +-- metrics.py
|
+-- tests/
|   +-- spec/                    # normative: one test per provision limb
|   +-- properties/              # Hypothesis
|   +-- bypass/                  # override, direct-call, TOCTOU, fail-open
|   +-- test_registry_integrity.py
|   +-- test_ast_linter.py
|   +-- test_agent_gate.py
|   +-- test_telemetry.py
|
+-- docker-compose.yml
+-- pyproject.toml              # editable install; public API surface
+-- requirements.txt
+-- PLAN.md
+-- README.md
```

---

## 7. Day-by-day

### Day 0 (half day) — Corpus and provenance

Moved to the front because every later test's PIN step depends on it, and because the ingestion pipeline is also the tool that re-verifies the `RV` rows in §4.1.

- CELLAR fetch via `urllib.request`; chunk on `div.eli-subdivision`.
- Pin ~12 provisions: PSD2 97(1)(b), 97(2); RTS 11, 13, 16, 18+Annex; GDPR 5(1)(c), 25, 32, 87; AMLR 19, 26(1), 69, 80; TFR 4(4), 5(3); DORA 28(3), 23.
- Write `corpus/NOTICE`.
- Re-verify every `RV` row; downgrade to prose-with-hedge anything that stays unverified.

**Exit:** `corpus/` committed with hashes; §4.1 has no unaddressed `RV` rows.

### Day 1 — Registry, then taxonomy

- **PIN:** provisions above.
- **RED** `test_registry_integrity.py`: every numeric parameter has `provenance`; `regulatory_verbatim` has a `locus` and its number appears in the pinned span; every citation CELEX matches `^3\d{4}[LRD]\d{4}$` or a consolidated form; no enforcing obligation cites a `pending` or `superseded` instrument; a future `applies_from` requires `predecessor_in_force`; `RiskBasedObligation` carries no amount field.
- **RED** `test_decorators.py`: stacking registers both categories; duplicates deduplicate; metadata extraction; registry is import-time populated.
- **GREEN:** `citation.py`, `manifest.py`, `policies.py`, `decorators.py`.
- **REFACTOR:** lookups, type hints, `mypy --strict`.

**Exit:** a number cannot enter the registry without provenance.

### Day 2 — Agent gate and bypass suite

- **RED** `test_agent_gate.py`: decorated tools construct; one undecorated tool raises `UnregulatedToolError`; the message names the offending callable and the fix.
- **RED** `tests/bypass/`: subclass override cannot neuter the check (enforcement lives in the wrapper, not an overridable method); a tool cannot reach the bank client without passing the gate; TOCTOU — arguments validated and executed must be the same object/snapshot; fault injection in each shield fails closed.
- **GREEN:** `core/agent.py`.
- **REFACTOR:** developer-facing error formatting.

**Exit:** four bypass attempts documented as permanently failing tests. This is the strongest interview material in the repo.

### Day 3 — AST linter and `--fix`

- **RED** `test_ast_linter.py`: flags an unprotected financial function at the correct line; does not flag an already-decorated one (idempotency); `--fix` inserts above existing decorators; preserves comments and blank lines; correct indentation inside a class; handles `async def` and multi-line signatures; multiple insertions in one file land correctly (descending-order edits); a file whose re-parse changes the function set is **not** written; LLM timeout leaves the file byte-identical.
- **GREEN:** `cli/linter.py`.
- **REFACTOR:** prompt templates, timeout handling, atomic write.

**Exit:** `--fix` is provably non-destructive on a file with comments.

### Day 4 — SCA obligation and identifiers

- **PIN:** RTS 11, 13, 16, 18; PSD2 97.
- **RED** `tests/spec/test_spec_psd2_sca.py`: SCA is the default at every amount; limb (a) boundary at 30.00 / 30.01; limb (b) at 100.00 / 100.01; limb (c) at 5 / 6; **b and c are disjunctive**; (a) is conjunctive with the (b OR c) disjunction; exemption unavailable outside the remote-electronic channel; Art. 11 uses 50/150/5 with a per-instrument counter; **Art. 13 has no amount cap**; Art. 18 requires an attestation and matches the Annex table at all six band/rate pairs; no ALLOW without a cited basis.
- **RED** `tests/properties/`: the §2.1 keystone property; monotonicity in amount; every ALLOW names an exemption from the closed union; limb (a) is necessary.
- **RED** identifier tests: synthetic generators produce checksum-valid NO/SE/FI identifiers (NO redrawing on control digit 10); FI accepts all 2023 century signs; **DK does not hard-reject on mod-11**; lookalike negatives (order numbers, dates, phone numbers) do not produce high-confidence hits.
- **GREEN:** `psd2_sca.py`, `identifiers/nordic.py`.
- **REFACTOR:** `Decimal` boundaries; no float anywhere near money, with a test that would fail under float.

**Exit:** the keystone property passes; a threshold implementation would fail it.

### Day 5 — Telemetry and CI

- **RED** `test_telemetry.py`: a completed run compiles metric flags and POSTs valid Prometheus text format to a mocked gateway; grouping keys set; label cardinality bounded to registry provisions; no payload or amount reaches a label.
- **GREEN:** `telemetry/metrics.py`, `docker-compose.yml`, provisioned Grafana dashboard.
- **REFACTOR:** `security-audit.yml` chaining fast gate -> entailment gate -> evidence report into the Step Summary.

**Exit:** `docker compose up` works; full fast gate green on a fresh clone with no API key.

### Day 6 (buffer) — Credibility layer

- Entailment check wired into CI with three distinct failure modes.
- `render.py` -> `docs/obligations.md`, plus the CI sync test (code -> doc, never doc -> code).
- Eval set whose negative cases are this project's own two original errors: the system must reject "SCA required above EUR 30" and "EUR 500 AML reporting threshold" against the pinned text, or CI fails.
- README: architecture, the §4.5 non-euro open question, the §4.6 must-not-publish discipline, "out of scope and why", why there is no vector database, and the limits section (not legally reviewed; self-authored eval corpus; not a certified transaction-monitoring system).

**Do not cut Day 6.** It is where the seniority signal lives.

---

## 8. Validation strategy

| Layer | Method | Target |
|---|---|---|
| Deterministic rules | Exhaustive unit + Hypothesis properties | Boundary exactness, fail-closed invariants |
| Bypass resistance | Adversarial tests | Override, direct-call, TOCTOU, fail-open |
| Identifier detection | Synthetic generators + lookalike negatives | Recall on valid IDs; **precision**, because over-redaction destroys utility |
| Registry honesty | Meta-tests | Provenance, citation resolution, status, staleness |
| Claim correctness | CI entailment check | Quote supports the typed claim |
| Linter safety | Round-trip tests | Comments preserved; no write on parse mismatch |
| Telemetry | Mocked gateway | Format valid, cardinality bounded |

Report a confusion matrix for any probabilistic component, leading with **false-positive rate at fixed recall**, and state which error is costlier here and why.

---

## 9. Open questions

1. Non-euro conversion fixing date / averaging window (§4.5) — no authoritative answer exists.
2. PSD3/PSR precise procedural state (§4.1, `UNV`).
3. Finnish Tietosuojalaki s. 29 verbatim text — finlex.fi unreachable; secondary sources only.
4. DORA EEA Joint Committee decision number — single-sourced.
5. CELLAR's conflicting PSD2 end-of-validity dates (§4.8).
6. CELLAR rate limits — undocumented.

---

## 10. Definition of done

- `pytest` green on a fresh clone with no API key.
- `mypy --strict` clean.
- `docker compose up` yields a populated Grafana dashboard.
- Every registry number has provenance; every `regulatory_verbatim` number appears in its pinned span.
- Four bypass tests present and passing.
- `docs/obligations.md` generated and in sync.
- README carries the limits and out-of-scope sections.
- No item from §4.6 appears anywhere in the repo.

---

## 11. Engineering decisions worth stating in the README

- **Stdlib over LibCST** for code modification: the job is inserting a decorator and an import, which line-based insertion does correctly. LibCST would earn its dependency only for expression rewriting or reformatting.
- **No vector database:** a dict plus BM25 over fewer than 200 committed chunks is faster, auditable, and has no index-drift failure mode. Declining the tool on reasoned grounds beats including one that is not needed.
- **No LLM in any enforcement decision:** non-determinism in a compliance verdict is not auditable.
- **Local inference only** (`localhost:11434`): no bank data leaves the building, even at authoring time.

---

## 12. Known risks

| Risk | Mitigation |
|---|---|
| TDD codifies a wrong legal reading | PIN step; provenance meta-test; entailment check |
| Catalogue becomes documentation theatre | Every registry entry must resolve to code and to a test that emitted evidence during its own run |
| `--fix` destroys developer files | Re-parse verification + atomic write + comment-preservation tests |
| Detection false negatives | Three combined signals, including the structural bank-client-import rule |
| Scope creep back into runtime | §13 is the parking lot, and it stays parked |
| Overclaiming compliance | `addressee` / `sdk_role` fields; README limits section |

---

## 13. Future work (explicitly not in this version)

1. **Runtime in-process shields.** Deterministic, sub-millisecond, pure Python, in the agent's own process — no network hop. Would let the SDK inspect the agent's reasoning trace at execution time, which is the project's distinctive claim and the one thing shift-left cannot reach: a CI gate catches unclassified *code*, never a live prompt injection or a live structuring sequence.
2. **Advisory AML behavioural judge.** LLM over a rolling session window, emitting a review flag — never a block, never an autonomous report. Fed *cited text* establishing that the reporting duty is suspicion-based with no amount, because handing a model bare scalars (10 000 / 3 000 / 1 000) invites it to synthesise exactly the invented threshold this project exists to prevent.
3. **Hash-chained audit log** with `exemptions_evaluated` per-limb booleans — the record that makes a wrong reading visible on sight.
4. **Generated OSCAL component-definition** export from the registry, as a projection rather than an authored artifact.
5. **Broader jurisdictions.** DE/AT as a second pack to prove the axis (note: Germany has no universal cross-sector personal identifier; Austria uses sectoral derived identifiers — a genuine contrast with the Nordic model). PESEL and codice fiscale drop into the same generator/validator pattern.
6. **Bilingual obligation register** (EN/DE) rendered from one source of truth — EUR-Lex publishes in all official languages, so this costs a language field and a loop.
7. **GDPR Chapter V transfer analysis** for non-EEA model endpoints: adequacy vs SCCs.
