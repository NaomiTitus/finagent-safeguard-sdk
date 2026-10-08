# How the category gets decided

**Status:** method agreed, not yet built. Supersedes nothing; `_category_for` in
`finagent_safeguard/cli/linter.py` is the thing being replaced.

Three isolated agents were asked how to assign the regulatory category: one arguing
deterministic static analysis, one arguing a learned model, one designing the evaluation.
None of them saw the others' work. Their full briefs are kept out of the repo; what follows
is the decision and the evidence that forced it.

## 1. The measurement that settled it

Each agent, independently, ran the real linter rather than theorising. So did I. The results
agree, and they are worse than "imprecise" — the classifier is **inverted**.

| Function | Linter says | Truth |
|---|---|---|
| `transfer_focus(widget, target)` — GUI focus | `PSD2_PAYMENT_EXECUTION` | not financial |
| `compute_mean(amount_a, amount_b)` — statistics | `PSD2_PAYMENT_EXECUTION` | not financial |
| `create_pain001_001_008(msg_id, ccy, nb_txs)` — ISO 20022 | **not flagged** | PSD2 payment |
| `gen_pacs008(dbtr, cdtr, instd_amt)` — SEPA credit transfer | **not flagged** | PSD2 payment |
| `capture(payment, amount: Decimal, customer_id)` — Saleor | `GDPR_PII_PROCESSING` | PSD2 payment |

Over 155 top-level stdlib modules: **51 functions flagged, 0 correct.** The stdlib contains no
financial code, so precision on non-financial input is zero. `ipaddress` alone contributes 15,
because `address` is a PII token.

`pyiso20022` (MIT), a pure ISO 20022 payments library, trips **0 of its 66 functions**. This is
the systematic finding, not an anecdote: real payment vocabulary is message-type codes
(`pain.001`, `pacs.008`, `camt.053`), not English nouns. A Nordic core-banking codebase is named
this way, which is precisely the codebase this tool claims to serve.

## 2. Root causes, each verified in the source

| Cause | Evidence |
|---|---|
| `_category_for` is three lines and hard-codes two outcomes | **4 of 6 `FinancialCategory` members are unreachable** from the linter: `PSD2_ACCOUNT_ACCESS`, `GDPR_THIRD_COUNTRY_TRANSFER`, `AML_TRANSACTION_MONITORING`, `DORA_ICT_THIRD_PARTY` |
| PII is tested first and returns on first match | A function with both money and person evidence is always GDPR. This fires on the *most* regulated functions, which are the ones carrying both |
| `category: str` holds one value | `amount` + `email` is genuinely **both** categories. The data model cannot say so, so the tie-break is forced to invent a winner |
| Bare `Decimal` counts as evidence | `statistics._decimal_sqrt_of_frac` and `pytest.approx` are flagged as regulated |
| Tokens are matched without exclusions | `address` → all of `ipaddress`; `pan` → `matplotlib.start_pan`; `transaction` → `alembic.begin_transaction`; `cpr` → `prompt_toolkit.ask_for_cpr` (ANSI Cursor Position Request vs. Danish CPR-nummer) |

## 3. What the prior art says

The decisive discovery is that **this is a solved problem, and nobody solves it with a model at
scan time.**

| Tool | Mechanism, read from its source | Licence |
|---|---|---|
| CodeQL | `SensitiveDataClassification` is a five-word list plus identifier regexes; data flow only *propagates* a name-derived label | — |
| Bearer | "pattern matching and heuristics" — 122 data types, 194 regex rules. ML is used **offline to author rules**, never at scan time | ELv2 |
| Privado | 107 regex data elements with a `tags/law` column mapping to legislation | GPL-3.0 — **cannot vendor** |
| Presidio | pattern + checksum + context; ML only for `PERSON`/`LOCATION` | — |

All three code scanners carry hand-written negative lookarounds for **our exact bug**:
Privado has `(?<!question)bank`, `dna(?!me)`, `(?<!(sh|tr))ip`; CodeQL excludes
`concert|wildcard|accountable`. CodeQL also contains a commented-out `email` rule annotated
`// this seems too noisy`. They tried the thing we are doing, measured it, and removed it.

Two citations that change how this project should present itself:

- **Hjerppe, Ruohonen & Leppänen (2020)**, DOI `10.1007/978-3-030-42504-3_22`, University of
  Turku — annotation-driven GDPR compliance tooling. This is substantially this project's
  design, in Java, six years earlier. Cite it; do not claim novelty.
- **Tang, Østvold & Bruntink (2023)**, DOI `10.3233/FAIA230228`, Norsk Regnesentral — the only
  verified precision figure in the literature for this task: **0.87, n=4, recall not reported.**
- **PrivDev** (arXiv `2610.03518`) maps Bearer's 122 types to GDPR via DPV: 43 derived
  mechanically, **79 needed an LLM** — used offline, SHACL-validated, frozen. Nine annotators
  agreed only **0.72**.

That last number is the warning. If trained annotators reach 0.72 on data-type→regulation
mapping, no classifier can be judged against a ground truth we have not measured agreement on.

## 4. The determinism finding

The model-side agent conceded this rather than defending it, which is why it carries weight.

> Temperature 0 is **not** determinism. 1,000 temperature-0 completions produced **80 unique
> outputs**. The cause is non-batch-invariant GPU kernels plus varying server batch size — it
> is a property of the serving stack and cannot be fixed from the client.

So "temperature 0 for reproducibility" must never appear in this project's documentation. An
LLM can still be used, but only where its output is **frozen into a reviewed artefact** before
CI reads it — the same cassette pattern `PLAN.md` §5.4 already uses for the entailment gate.
Cost at scan time is then **$0.00**, and the thing an auditor inspects is a table, not a model.

## 5. Agreed method

Four steps, in this order. Each gate must pass before the next begins.

### Step 0 — agreement pilot (half a day, no code)

Label 40 functions twice, independently, against the 6-way taxonomy. Compute Cohen's kappa.

- **κ < 0.40 → stop.** The taxonomy is broken, not the classifier. No amount of engineering
  fixes a label two competent readers cannot agree on, and building a classifier against it
  would produce a number that means nothing.
- **κ ≥ 0.40 → continue,** and publish κ as the ceiling beside every accuracy figure.

This is first because it is the only step that can invalidate all the others.

### Step 1 — fix the defects (2–3 days)

These need no method decision; they are bugs, and they are most of the measured error.

1. Make the category **multi-label** — `amount` + `email` emits both.
2. Delete the first-match tie-break entirely.
3. Add `NOT_REGULATED` so "scanned and cleared" is distinguishable from "never looked at".
4. Stop treating a bare `Decimal` annotation as evidence.
5. Reach all six categories, or delete the four that are unreachable from the enum.

### Step 2 — the benchmark (3–4 days)

Labels frozen and committed **before** Step 3 begins, so the classifier author cannot tune
against them.

- **~350 items** minimum (McNemar power to distinguish two methods), 600 comfortable.
- **~40 minimal pairs** — near-identical functions with different correct labels. This is the
  technique that catches a classifier keying on surface tokens, and it is what exposed the
  inversion in §1.
- **Pointer-only corpus**: URL + commit SHA + line range + SHA-256, never vendored source.
  This triggers no licence obligation and reuses the existing `corpus/MANIFEST.json` pattern.
- Harvested from verified-permissive Python: `stripe-python` (MIT), `django-oscar` (BSD-3),
  `mollie` (BSD-2), `schwifty` (MIT), `python-sepaxml` (MIT), `saleor` (BSD-3),
  `pyiso20022` (MIT). **Excluded:** `plaid-python` — MIT but 41% structurally duplicate
  functions, one body repeated 2,251 times. `Open Bank Project` is AGPL-3.0 Scala.
  There is **no open-source Python core-banking system** to harvest; Fineract is Java, Mifos is
  TypeScript/Kotlin, Cyclos has no public repo.
- `pyiso20022` goes in the **test** split specifically, because it is the blind spot.

The only existing code-level compliance benchmark, **GDPR-Bench-Android** (arXiv `2511.00619`,
MIT), was audited and has five defects we must not reproduce: **0 negative instances** (false
positives unmeasurable), **56% duplicate snippets**, classes with n=1 inside a 23-class
macro-F1, median snippet 95 characters, and **no agreement statistic anywhere** — its own
README links annotation guidelines that 404. Publishing ours with those five fixed is a better
contribution than any score.

**Headline metric: governance regret per 1,000 functions**, under a published cost matrix —
miss 10, wrong category 25, abstain 2, false alarm 1. It is the only candidate that prices the
asymmetry (a false compliance claim in someone's source is worse than a missed function) and
cannot be gamed by always abstaining *or* always guessing. Never publish it without the human
agreement ceiling from Step 0 beside it.

### Step 3 — evidence sets and structural abstention (8–12 days, ~600–1000 LOC, no dependencies)

A category is **asserted** only when there is either one unambiguous item (a domain type, a
known call target) **or** two independent corroborating loci. Two or more asserted → emit all.
One weak locus → `REVIEW_REQUIRED`, naming the candidates. Reachability alone →
`REVIEW_REQUIRED` with no candidate.

No tuned threshold appears anywhere, which matters: the provenance rule would reject a `0.7`
that cannot be traced to a provision. Highest value per line is literal-subscript evidence
(`req["iban"]`) at 40–80 LOC.

**`REVIEW_REQUIRED` must not be a `FinancialCategory` member.** As an enum member it becomes a
value a developer can write by hand, which makes it a silent exemption — the exact bug class
`linter.py` has already fought three times (`@overload`, docstring-suppressed imports,
`TYPE_CHECKING`). It is a separate state meaning *the tool refused*, and it fails CI.

### Step 4 — LLM at authoring time only, if Step 3's abstention rate is too high to use

Adjudicates the abstain band, offline, writing a `labels.jsonl` that a human reviews and
commits. CI reads the committed file and never calls a model. **$0.00 per CI run**;
$0.03–0.09 per adjudicated function.

## 6. The strongest argument against all of this

It comes from this project's own source. `taxonomy/policies.py` states that a category
"deliberately carries no threshold, citation or amount", and that the decorator answers only
*"has this been classified?"*

If that scoping is sincere, **category correctness is already disclaimed** — and the honest
move is to narrow the product rather than engineer a classifier for a claim we are not making:
emit `REVIEW_REQUIRED` plus candidate provisions, always, and let the developer pick. That
dissolves Steps 2 and 3 and most of the risk.

The counter-argument is that a tool which never names a category gives a developer nothing to
act on, and that candidate provisions are themselves a claim requiring the same evidence. This
is unresolved and is the first thing to settle after Step 0, because it determines whether
Steps 2–3 are worth building at all.

## 7. Residual holes, disclosed not solved

- The annotation guideline would be written by the classifier's author. Mitigation: freeze it
  before Step 3 and publish it; it is already the strongest available guard, and it is weak.
- Synthetic items for rare classes are easier than real ones, so rare-class scores are
  optimistic.
- The corpus is e-commerce and payment-client Python. The deployment target is Nordic core
  banking. These are not the same population, and no permissive sample of the latter exists.

## 8. Two corrections to existing documents

- `PLAN.md` §5.4.1 quotes `claude-opus-5` at $4/$20 per Mtok; the pricing page read during this
  work gave $5/$25. **Unverified by me** — check before relying on either.
- `PLAN.md` §13 should cite Hjerppe et al. (2020) as prior art rather than implying novelty.
