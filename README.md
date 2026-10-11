# FinAgent-SafeGuard

**A linter that finds the code in your repository that EU financial regulation
applies to — and refuses to guess which regulation.**

Shift-left governance for AI agents in EU/Nordic banking. It runs in CI, reads
your Python with the `ast` module, and flags functions that handle money or
personal data and can reach a bank client. It then names the provisions a
developer should read, and **stops**.

```bash
pip install -e .
finagent-lint src/
```

```
  src/payments.py:4: transfer_funds [name]

1 file(s) scanned. 1 actionable, 0 advisory, 0 unresolved.
```

With `--diff-only`, the edit it *would* make — written to stdout, not to disk:

```diff
+from finagent_safeguard.core.decorators import regulated_tool
+from finagent_safeguard.taxonomy.policies import FinancialCategory
 import bank_client


+@regulated_tool(FinancialCategory.REVIEW_REQUIRED)
+# finagent-lint: money+pii identifiers, name signal.
+#   candidates to read before deciding:
+#     PSD2 Art 97(1)(b) - strong customer authentication where the payer initiates an electronic payment transaction
+#     PSD2 Art 97(2) - authentication elements dynamically linking the transaction to a specific amount and a specific payee
+#     TFR Art 4(4) - the payment service provider of the payer verifies the accuracy of the payer information
+#     GDPR Art 5(1)(c) - personal data adequate, relevant and limited to what is necessary
+#     GDPR Art 32 - security of processing appropriate to the risk
+# Replace REVIEW_REQUIRED with the category you have confirmed.
 def transfer_funds(iban, amount, customer_id):
     bank_client.execute_transfer(iban, amount)
```

Note what it wrote: **`REVIEW_REQUIRED`**, not a category.

That diff ends with a digest of itself, and `--fix` will write **only** that
edit:

```
  plan 113eba20a6dc  --fix --expect-plan 113eba20a6dc
```

If the file, the rule, or the candidate provisions have moved on since you read
it, the write is refused rather than applying an edit nobody looked at:

```
error: pay.py: approved plan 113eba20a6dc is not the plan that would be
written now (a9cc04c4a6e0).
  Re-run with --diff-only, read the new edit, and approve that one.
```

And if you ship `REVIEW_REQUIRED` without deciding, the runtime gate refuses to
construct the agent:

```
UnregulatedToolError: 1 tool(s) passed to this agent carry
FinancialCategory.REVIEW_REQUIRED:
  - payments:transfer_funds

REVIEW_REQUIRED is the linter's statement that it found a regulated function
and had no basis to name the regulation. It is not a classification, and this
gate will not accept it as one.
```

---

## The design principle: propose, never assert

A tool that guesses "this is PSD2 Article 97" and is wrong is worse than no
tool, because someone will believe it. Nothing here outputs a regulatory
conclusion. It outputs a **marker that something needs a decision**, the
candidate provisions to inform that decision, and a gate that fails loudly
until a human makes it.

The threat model is **error, not adversary**. This is a linter, not a sandbox:
it helps a developer who is trying to get it right, and it does not pretend to
stop one who is trying to get around it. Any claim it made to the latter would
be false.

## Architecture

Four layers. Layers 1 and 4 run **in parallel**, not in sequence — the
classifier is an independent detector, so it fires on functions the AST rule is
silent about.

| | Layer | What it does | Gates CI? |
|---|---|---|---|
| 1 | **AST rule** | Deterministic tree walk — money/PII vocabulary in signatures, import resolution, sink calls | **Yes**, exit 1 |
| 2 | **Candidates** | Signal → candidate provisions, drawn only from pinned regulatory text | No |
| 3 | **Human confirms** | `--diff-only` shows the exact edit and a digest; `--fix --expect-plan` writes only that edit; the agent gate refuses `REVIEW_REQUIRED` | **This is the control** |
| 4 | **Classifier** | Ranks a review queue by probability. Nothing in the write path reads it | No — advisory |

Layer 4 being advisory is what makes it admissible at all: a wrong score
misorders a queue, it cannot mislabel a function.

### How a function reaches a provision

The question this design has to answer is *how do you suggest the right law
rather than all of them?* — because a category is one-to-many, and looking up
"everything tagged PSD2" would be useless.

The answer is that **there is no category → law lookup.** One dictionary
exists, and it is keyed by what the AST actually found:

```python
CANDIDATES = {
    "money": (PSD2 Art 97(1)(b), PSD2 Art 97(2), TFR Art 4(4)),
    "pii":   (GDPR Art 5(1)(c), GDPR Art 32),
}
```

```
code  →  signal ("name")              how it was detected
      →  vocabularies ("money","pii") which word families matched — the key
      →  provisions (5 of 23 pinned)  a hand-written lookup
      →  categories                   comments only, for the human to paste
```

Three signals exist, tried in order of how much they tell you:

| Signal | Fires when | Gates CI |
|---|---|---|
| `name` | an identifier matched the money or person vocabulary | yes |
| `type` | an annotation names a risky type (`Decimal`, an IBAN type) | yes |
| `bank_client_import` | the module can reach a bank client, and nothing more specific fired | **advisory only** |

The last is deliberately broad — it is the net for functions no naming
convention would reveal — and on 155 top-level stdlib modules it produced **51
flags and 0 correct ones**. A build failing on that fails constantly and gets
switched off, so it is always reported and only counted under `--strict`.

The *vocabularies* are what narrow the provision set; categories are derived
**last**, for display.

Three filters do the narrowing, and none of them is the category:

| Filter | Provisions surviving |
|---|---|
| pinned in `corpus/` with a SHA-256 | 23 citable |
| evidenceable from a function signature | **5 reachable** |
| matching *this* function's vocabularies | 3 (money) or 2 (pii) |

The middle filter is the substantive one. **18 of 23 are recorded as
`UNREACHABLE`, each with its reason** — *"a third-country transfer depends on
where the endpoint is, which the current signals do not inspect"*; *"data
protection by design is a property of a system's architecture, not of a
function"*; *"a permission addressed to Member States, not a duty on a
controller, so no function can discharge it."* A test fails if a newly pinned
provision appears in neither the mapping nor that list, so the candidate set
can only grow by a deliberate act.

The mapping is hand-written on purpose. Every production tool in this space
does the same: CodeQL's sensitive-data classifier is a word list plus
identifier regexes, Bearer is pattern matching over 122 data types with any
machine learning used offline to *author* rules and never at scan time, Privado
is 107 regex data elements with a `tags/law` column. What makes it checkable is
not the algorithm but the pinning — a reviewer at a bank can read the file in a
minute, and CI re-verifies every quotation against the committed text.

### What a category is for

Almost nothing, inside the linter. `FinancialCategory` appears there in four
places: the one constant it writes (`REVIEW_REQUIRED`), a write guard refusing
any name not in the enum, the `if it applies:` suggestions (which are
comments), and the rendered decorator — which is always the refusal.

A category is the unit of **the human's decision** and the thing the **runtime
gate** checks. It is never something the linter computes:

```
linter  (static)   writes REVIEW_REQUIRED, suggests the rest as comments
human   (decides)  replaces it with the categories they have confirmed
gate    (runtime)  refuses to construct an agent still carrying the refusal
```

`@regulated_tool` stacks, and unions the categories — a money+PII function
usually needs two. Because registration records the function rather than
wrapping it, stacking is order-independent. And `REVIEW_REQUIRED` left stacked
alongside a confirmed category **still fails the gate**, so a half-finished
decision cannot pass CI.

**What a confirmed category buys, stated exactly.** It makes the gate pass, and
that is all it does in this version: nothing downstream reads
`agent.categories`. There is no step that says *you declared
PSD2_PAYMENT_EXECUTION, therefore Art 97 applies, therefore check SCA*.

So what this delivers is a **coverage** guarantee — you cannot ship an agent
containing a money- or PII-handling tool that nobody classified — and not a
correctness one. The enum's own docstring is the design statement: "a coverage
mechanism, not a correctness mechanism". A category records that a human
looked, not that they were right.

That boundary is deliberate, and it is why the obligation layer is scaffolding
rather than content: `Obligation` carries `addressee`, `enforcing` and
`interpretation_boundary`, is tested, and has **zero instances**. Mapping a
category to the live duties it attracts is the next layer, and asserting those
duties without the provenance machinery underneath would reproduce this
project's founding error in a tidier wrapper.

### Provenance

Every number in the regulatory registry is one of exactly two things:

- **`regulatory_verbatim`** — quoted from pinned text, with its locus
- **`operator_configured`** — a local policy choice, with a rationale of at
  least 40 characters

There is no third category, and no number without one of these. A CI job checks
every typed claim against the pinned text it cites, and fails on *drift* rather
than on absence. Five provisions are reachable; **18 are recorded as
unreachable with the reason why**, because a gap you can name is a different
thing from a gap you have not noticed.

## The classifier, and what measuring it properly cost

The honest version of this story is more useful than a good number, so here it
is.

The first model scored respectably under stratified cross-validation. It was
measuring **which repository the code came from.**

| | κ | precision |
|---|---|---|
| A one-bit baseline: "is this `python-sepaxml`?" | 0.503 | 80% |
| The full 16-feature model | 0.511 | 72% |

Paired bootstrap on the difference: **+0.008**, 95% CI [−0.182, +0.208],
**P(model no better than one bit) = 0.47**.

The corpus draws from six repositories whose positive rates run from 0% to 70%,
so a stratified split puts the same repositories on both sides of every fold —
it *cannot* detect this. **Leave-one-repository-out** can, and it inverted the
feature ranking:

| Feature group | stratified CV | LORO |
|---|---:|---:|
| `literal` (length, punctuation counts) | **+0.172** | **−0.069** |
| `vocabulary` | −0.001 | +0.024 |
| `verb` | +0.125 | +0.002 |

The strongest group under one split was the worst under the other. Everything
after that followed from changing the split, not the model:

| Feature set | LORO macro AUC |
|---|---:|
| Repository-prior baseline | 0.500 |
| Original 16 features | 0.576 |
| Minus the `literal` group | 0.645 |
| Plus `dataflow` and `docstring` groups | 0.774 |
| Minus `pii_hits` (redundant with a feature derived from it) | 0.793 |
| **Minus `risky_annotation` (harmful; one row in 996)** | **0.798** |

`docs/feature-measurement.md` and `docs/embedding-experiment.md` have the
mechanisms, the confidence intervals, and the corrections.

### Three results worth more than the headline

**An embedding beat the hand-written features and was rejected.**
`all-MiniLM-L6-v2` reduced to 16 SVD dimensions reached 0.826 against 0.774,
+0.052 with a 95% CI of [+0.003, +0.096] — statistically real. It is not
shipping: the gain is a *reshuffle* between two repositories rather than a lift
(`python-sepaxml` 0.637 → 0.882, `stripe-python` 0.878 → **0.783**), the CI
lower bound is +0.003, and it costs `onnxruntime` plus a model binary in a
project whose argument is that a reviewer can read the coefficients. A
code-specific model (`jina-v2-base-code`) did *worse* than the general-purpose
one.

**The model is pure standard library — `classifier.py` is 157 lines, the fit
itself 70 — and that equivalence is verified rather than asserted.** Against scikit-learn's `LogisticRegression` with the regularisation
matched, on the same leave-one-repository-out folds:

| | macro AUC |
|---|---|
| This project's hand-written gradient descent | **0.7934** |
| `sklearn.linear_model.LogisticRegression` | **0.7934** |

The output is 15 named coefficients in a JSON file, loaded and evaluated with
arithmetic. No pickle, no dependency, nothing a reviewer cannot read in a
minute.

**One feature carries the model, and the rest cannot be ranked confidently.**
Per-feature ablation (`--loro-per-feature`) puts `callee_vocabulary` — *does
this function call something whose name is money or person vocabulary* — at
**+0.104** of the model's +0.298 over baseline, four times the next feature.
That one is robust; the ordering below it is not, and the reason is worth more
than the ranking would have been.

Marginal contribution means "what this adds given everything else", so it moves
when everything else moves. Dropping one harmful feature changed `no_params`
from **+0.000 to +0.008** — nothing about `no_params` changed. And the five
features each measuring about +0.001 individually are worth **+0.009
together**. So features are excluded here only when a *mechanism* explains the
number: `pii_hits` was collinear with a feature derived from it;
`risky_annotation` held the model's largest weight, fitted from a single row in
996. Nothing was dropped on a measurement alone.

### Where this sits, and what it is not

It is a **ranker that makes a human's review faster**. It is not an oracle, and
nothing is gated on it.

That is a deliberate calibration, not modesty. On the nearest published
benchmark (GDPR-Bench-Android, arXiv 2511.00619) the best of eleven methods
reached **5.75% macro-F1** and a deterministic AST baseline reached 1.86%.
Trained human annotators agree on fine-grained regulatory categories at
Krippendorff's α of **0.251** (PrivDev, arXiv 2610.03518). Any tool claiming to
classify six regulatory categories accurately from source code would be
claiming to beat the literature by an order of magnitude. This one answers a
binary question — *does this handle regulated data?* — and hands the
classification to a person.

## Evidence discipline

The part that is actually reusable, independent of the regulatory domain.

| | |
|---|---|
| **423 tests** | including an adversarial bypass suite |
| **84 mutation rows** | each one a single edit, each naming the test that must fail — `docs/mutation-list.md` |
| **`mypy --strict`** | clean across 28 source files |
| **5 CI jobs** | 4 required on `main` |

```bash
python3 tools/battery.py --base origin/main
```

Runs the review layers as one command: tests, both type-check passes, the
mutation baseline, a **vacuity** suite (does any test actually assert
anything?), and a **name-assertion** layer that fails if a new definition has no
mutation row.

A mutation nothing kills is a defect in the test suite, not in the code — and
mutations that nothing killed are listed as such, in public, including **eight
known holes** in `docs/mutation-list.md` — bypasses like `object.__new__` and
`__qualname__` rebinding that a linter's threat model does not cover. Several of this project's real bugs
were found by writing those rows rather than by running the code:

- `fit()` returned **NaN weights** for a large `l2`. Every comparison against
  NaN is false, so the model did not score *badly* — it scored **unorderably**,
  ranking the queue at random while returning a float for every row.
- `sigmoid()`'s ±500 clamps were **dead code**; the sign branch was doing all
  the work. The mutation removing the clamps *survived*, which is how this was
  found. A dead guard that looks like the safety mechanism is worse than none.
- A test tolerance was **wider than the effect it named** — `abs=0.03` on a
  0.10 base rate passed even with the bug present.

## Layout

```
finagent_safeguard/
  cli/linter.py        the AST rule, --fix, the --since ratchet, 5 write guards
  cli/features.py      21 features produced, 15 fitted; exclusions declared
  cli/classifier.py    L2 logistic regression, standard library only
  core/agent.py        the runtime gate that refuses REVIEW_REQUIRED
  core/decorators.py   @regulated_tool
  regulation/          pinned text, citations, candidate provisions
  taxonomy/policies.py FinancialCategory
tools/                 battery, mutations, training, entailment, corpus harvest
benchmark/             180 human labels (SHA-pinned), 996 training items
docs/                  the measurements, the corrections, the known gaps
```

## Limits

Stated plainly, because a governance tool that oversells itself is the problem
it claims to solve.

- **Not legally reviewed.** The regulatory mapping is the author's reading of
  primary sources, not legal advice.
- **Not a transaction-monitoring system**, and not certified as anything.
- **The eval corpus is self-authored.** 180 hand labels by one annotator, 20 of
  them twice a day apart to measure self-consistency.
- **Three of six leave-one-out folds are unscoreable** (0, 1 and 4 held-out
  positives against a floor of 10). Every classifier figure here rests on a
  macro average of three repositories. That — not the model, not the features —
  is the binding constraint on all of it.
- **A confirmed category gates the build and nothing more.** Nothing reads
  `agent.categories` downstream, so this enforces that a classification *exists*,
  never that it is correct. `Obligation` and its addressee/enforcement fields are
  built and tested with **no instances** — the obligation engine is the next
  layer, not a shipped one.
- **`--check` does not detect a changed claim**, only a changed rubric or corpus.

## Next

- The obligation layer: category → the duties it attracts → a runtime check.
  The `Obligation` dataclass exists for this and is deliberately unpopulated;
  every instance needs the same provenance a `Provision` has.
- More repositories in the corpus. At three scoreable folds, more
  repositories are worth more than more features — one result in sixteen is
  already spurious by chance.
- Precision at fixed coverage with explicit abstention, the Typilus framing
  (arXiv 2004.10657), once there are enough folds to estimate it.
- The similarity-to-exemplars embedding form (3 features, +0.030) — the one
  variant that keeps an explanation a reviewer can read: *this resembles these
  five labelled functions*.
