# What the classifier's features actually measure

The classifier is layer 4 of four: an **advisory ranker** that orders a review
queue. Nothing in the linter's write path reads a feature, and a wrong score
misorders a queue rather than mislabelling a function. That is what makes it
admissible at all — and it is also why its features have to be measured rather
than argued about, because nothing downstream will catch a feature that is
quietly wrong.

Every number here is **leave-one-repository-out (LORO) macro AUC** over the
996-item training corpus (213 positives), reproducible with
`python3 tools/train_classifier.py --loro` and `--loro-ablate`.

---

## The problem this document exists to solve

The first version of the model looked fine and was measuring the wrong thing.

| What was measured | κ | precision |
|---|---|---|
| Repository identity alone — one bit, "is this `python-sepaxml`?" | 0.503 | 80% |
| The full 16-feature model | 0.511 | 72% |

Paired bootstrap on the difference: **+0.008**, 95% CI [−0.182, +0.208],
P(model no better than one bit) = **0.47**. The model had learned which
repository a function came from. Both corpora draw from the same six
repositories, whose positive rates run from 0% (`cpython-stdlib`) to 70%
(`python-sepaxml`), so a stratified split *cannot* detect this: the same
repositories sit on both sides of every fold.

Leave-one-repository-out can, and the two splits disagree about almost
everything:

| Feature group | stratified CV | LORO | verdict |
|---|---:|---:|---|
| vocabulary | −0.001 | **+0.024** | transfers |
| verb | +0.125 | +0.002 | did not transfer |
| sink | −0.002 | **+0.017** | transfers |
| structure | +0.007 | **+0.013** | transfers |
| literal | +0.172 | **−0.069** | actively harmful |

`literal` — `body_lines`, `string_literals`, `placeholder`, `party_key` — was
the strongest group under stratified CV and the worst under LORO. It counts
length and punctuation, so what it learns is house style, and house style is
repository identity in disguise. **The reversal is the entire argument for
measuring this way**, which is why the group is kept in the codebase, kept
produced by `extract`, and kept out of the fit via `EXCLUDED_GROUPS`. Deleting
it would erase the evidence.

---

## The two groups added in response

The brief was explicit: a feature that predicts via length does not address the
question the tool asks, which is *does this function handle money or personal
data*. So the new features had to describe **what the function does to data**,
not how it is written.

### `dataflow` — which key is read for its value

`sensitive_subscripts`, `subscript_total`, `callee_vocabulary`.

Counts the distinct sensitive keys a body reads out of a mapping, by `x["k"]`
and by `x.get("k")`. Deliberately generic: `callee_vocabulary` checks whether a
callee is *named* in money or person vocabulary rather than matching a specific
library's method, because a literal target like `bank_client.execute_transfer`
would encode which library this is, and repository identity is exactly what
LORO punishes.

### `docstring` — stated intent, and stated non-intent

`docstring_intent`, `docstring_disclaims`.

Not an embedding. The discriminating word in the corpus's hardest pair is plain
English: `_create_PmtInf_node`'s docstring says it creates the **blank** payment
information nodes, and `_create_header`'s carries no such qualifier. A one-word
lexical check reaches that; a 384-dimensional vector would need the same 213
positives to fit. So this was tried first, and the embedding only after
(`docs/embedding-experiment.md`).

### The result

| Feature set | LORO macro AUC | κ |
|---|---:|---:|
| repository-prior baseline | 0.500 | 0.000 |
| original 16 | 0.576 | +0.108 |
| 16 minus `literal` (12) | 0.645 | +0.059 |
| 17 — minus `literal`, plus `dataflow` and `docstring` | 0.774 | +0.165 |
| **16 — also minus `pii_hits`** | **0.793** | **+0.152** |

Per fold, for the shipped 17:

| Held-out repository | n | positives | AUC | scoreable |
|---|---:|---:|---:|---|
| cpython-stdlib | 150 | 0 | — | no |
| django-oscar | 250 | 23 | 0.808 | yes |
| pyiso20022 | 41 | 4 | 0.885 | no |
| python-sepaxml | 105 | 73 | 0.637 | yes |
| schwifty | 150 | 1 | 0.587 | no |
| stripe-python | 300 | 112 | 0.878 | yes |
| **macro over scoreable folds** | | | **0.774** | |

`python-sepaxml` is the one that matters: it scored **0.324** with the original
features — *below chance*, meaning the model was actively anti-correlated with
the labels on the repository it had seen least of. It is now 0.637. The
inversion is gone.

### Which groups earn their place, on the shipped set

`python3 tools/train_classifier.py --loro-ablate`, each group removed in turn
from the full 0.774:

| Group removed | AUC without it | contribution | verdict |
|---|---:|---:|---|
| `dataflow` | 0.682 | **+0.092** | earns it — the largest in the set |
| `docstring` | 0.744 | **+0.030** | earns it |
| `structure` | 0.747 | +0.027 | earns it |
| `verb` | 0.753 | +0.021 | earns it |
| `sink` | 0.761 | +0.013 | earns it |
| `vocabulary` | 0.792 | **−0.018** | no longer earns it |
| `literal` | — | — | not fitted; excluded at −0.069 |

Two rows deserve comment rather than a nod.

**`dataflow` at +0.092 is larger than every pre-existing group combined.** The
brief that produced it — *a feature that predicts via length does not address
whether the function handles money or PII* — was right, and specifically right:
the group that asks what the function reads carries more transferable signal
than the five groups that describe how it is written.

**`vocabulary` has gone negative, and it was +0.024 before.** Removing it now
*improves* the model. The honest reading is that `callee_vocabulary` covers the
same ground more precisely — a money word in a callee's name is evidence about
what the function does; the same word anywhere in the body is evidence about
the file it lives in. It is kept for now because −0.018 across three folds is
within the noise this corpus can resolve, and dropping a group on a
three-fold average is how the `literal` mistake happened in the other
direction. It is flagged as the next thing to measure when more repositories
are available, not quietly retained.

### `pii_hits`: dropped, and not for being wrong

The `vocabulary` group's −0.018 turned out to be **one feature**, and the
reason is redundancy rather than error.

| Dropped | Δ LORO AUC | 95% CI | P(no gain) |
|---|---:|---|---:|
| **`pii_hits`** | **+0.019** | [+0.009, +0.032] | **0.00** |
| `money_hits` | +0.005 | [−0.002, +0.014] | 0.09 |
| `both_vocabularies` | +0.001 | [−0.001, +0.003] | 0.15 |
| whole `vocabulary` group | +0.018 | [+0.007, +0.030] | 0.00 |
| *control:* `verb` group (which earns its place) | −0.021 | [−0.071, +0.026] | 0.82 |

The control is what makes the first row safe to act on: removing three features
that **do** earn their place gives a negative point estimate and an interval
three times wider. Dropping `pii_hits` does not look like "removing any three
features from an over-parameterised model".

**Two mechanisms were proposed and measured, and both were wrong.**

*Wrong hypothesis 1 — "it means different things in different repositories."*
No. Its within-repository correlation with the label is positive everywhere, and
its spread across repositories (0.192) is **smaller** than that of
`sensitive_subscripts` (0.417), which transfers fine.

*Wrong hypothesis 2 — "the model is over-parameterised, so removing any weak
feature helps."* No. `risky_annotation` fires on **one row in 996** and dropping
it is worth +0.003 with an interval touching zero. Dropping the four
near-dead features together (+0.014) is worth **less** than dropping `pii_hits`
alone.

**The actual mechanism: redundancy.** `both_vocabularies` is literally
`1 if money_hits and pii_hits`, computed from the same scan of the same names,
so it can only fire where `pii_hits` already has — r = 0.547, and that is not a
coincidence, one is derived from the other. L2 was splitting a single feature's
credit across two expressions of it:

| | weight with `pii_hits` | without it |
|---|---:|---:|
| `both_vocabularies` | +1.831 | **+2.283** |
| every other feature | — | moves < 0.04 |

So the signal is not lost by dropping it, it is **concentrated**. On its own
`pii_hits` is a *good* feature — P(label is yes \| it fires) is 0.50 against a
0.09 base rate in django-oscar, 0.83 against 0.37 in stripe-python.

**Why it was always going to be weak.** It scans the **signature** only — the
function's own name and its parameters, never the body. Real code takes a `row`
or a `self` and reads the sensitive key inside, so `def store(row)` reading
`row['email']` scores zero. That is why it fires on just 23 of 996 functions,
and why `sensitive_subscripts`, which reads the body, is worth +0.092 instead.

| | `pii_hits` |
|---|---:|
| `def send_receipt(customer_email, amount)` | 1 |
| `def validate(national_id)` | 1 |
| `def _create_header(self)` reading `self._config['name']` | 0 |
| `def store(row)` reading `row['email']` | 0 |

Result: **16 fitted features, LORO macro AUC 0.793**, with `python-sepaxml`
moving 0.637 → 0.696.

**The caveat is recorded with the decision.** `pii_hits` was selected by testing
six candidate drops against the **same three scoreable folds** it is then
measured on. The interval is tight and the collinearity is independent
evidence, so it is acted on — but a selection made on three repositories is the
same shape as the mistake leave-one-repository-out exists to catch. It is
flagged in `EXCLUDED_FEATURES` as provisional, to revisit when more
repositories are scoreable.

### The canonical hard pair, separated on two independent signals

These two functions are the whole difficulty of the labelling task. Both build
`Nm` nodes; the human labelled one yes and one no.

| | `sensitive_subscripts` | `docstring_disclaims` | label |
|---|---:|---:|---|
| `_create_header` — writes a party's name into a field | 1 | 0 | **yes** |
| `_create_PmtInf_node` — creates the same fields empty | 0 | 1 | **no** |

Two features that fire on the same evidence are one feature. These do not: one
reads the body's subscripts, the other reads prose the body does not contain.
`tests/test_features.py` asserts both separations, and the test that used to
document the *gap* is kept inverted so that losing the separation fails loudly.

---

## Three features were proposed and two of them were rejected

Measured, not guessed:

| Proposed | Verdict |
|---|---|
| `subscript_literal_keys` | **Shipped** as `sensitive_subscripts` / `subscript_total` — the largest transferable gain in the set. |
| `docstring_intent_score` | **Shipped** as `docstring_intent` / `docstring_disclaims`. |
| `callee_vocabulary` | **Shipped**, but in the generic form only. A call-graph edge to a named library method would have been repository identity. |
| `module_path_depth_risk` | **Rejected without measuring.** The depth and name of a file's path is a property of the repository, not of the function. It is the confounder this exercise removed, re-entered under a new name. |
| `type_annotation_risk` | **Kept, but as a known weak feature.** Only 2 678 of 70 826 public Python repositories carry PEP 484 annotations (Rak-amnouykit, DLS 2020), so whether a function is annotated says more about its project's conventions than about what it does. |

---

## The bug this group shipped with

`sensitive_subscripts` fired **backwards** on first implementation, caught by a
smoke test on the canonical pair rather than by any aggregate number. Two
causes, both instructive:

1. `'name'` is not in `PII_TOKENS`, which holds only `forename` and `surname`.
   That gap is why the earlier `party_key` feature could never fire on
   `self._config['name']` — the single most common way a party is named in this
   corpus. `SENSITIVE_KEYS` now includes it deliberately.
2. Subscript **stores** were counted as sensitive reads.
   `ED['Nm_Dbtr_Node'] = ET.Element('Nm')` names a slot in a local dictionary;
   nothing sensitive is read. Counting it made the blank-node builder outscore
   the function that reads a real name.

The aggregate AUC still looked plausible while this was true. That is the whole
reason the feature module carries mutation rows (`docs/mutation-list.md`, F1–F18):
**"the model still scores about the same" is not evidence that a feature works.**

---

## What is still not known

- **Three of six folds are scoreable.** `cpython-stdlib`, `pyiso20022` and
  `schwifty` have 0, 4 and 1 held-out positives, below the floor of 10. Every
  conclusion here rests on a macro average of three numbers, and that is the
  binding constraint on the whole exercise — not the model, not the features.
- **The confounder is in the human labels too**, though less: correlation with
  quote count is r 0.458 for the human and r 0.358 for Sonnet. Computed *within*
  repository it largely vanishes, which is consistent with it being a property
  of which repositories were sampled rather than of how either annotator judged.
- **κ remains low (0.165)** even where AUC is good. That is a calibration
  failure, not a discrimination failure: the model is trained at one base rate
  and scored at another. AUC is the figure to read for a ranker, and the
  precision/recall columns show the threshold is the part that needs work.
