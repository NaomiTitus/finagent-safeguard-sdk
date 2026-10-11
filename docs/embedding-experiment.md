# Do code embeddings help rank a review queue?

A half-day experiment with a stated bar, run because the alternative was to
assume. Every number here is leave-one-repository-out (LORO) macro AUC over the
996-item training corpus, 213 positives, measured the same way as every other
number in `docs/`.

**Verdict: no, not at this corpus size.** `all-MiniLM-L6-v2` reduced to 16
dimensions does beat the hand-written features — +0.052 AUC, 95% CI
[+0.003, +0.096] — but the lower bound of that interval is +0.003, the gain is a
reshuffle between repositories rather than a uniform lift, and buying it costs
`onnxruntime` plus a model file in an SDK whose entire argument is that it is
auditable stdlib. The hand features ship; the embedding is written up and left
out.

---

## A correction to an earlier number

An earlier note in this project reported a TF-IDF + SVD probe at **0.821**, and
that figure was used to set the bar for this experiment. **It was inflated.**
The vectoriser and the SVD basis were fitted on the whole corpus, which under
leave-one-repository-out leaks the held-out repository's own vocabulary into the
features it is then scored on.

Re-run with both fitted inside each fold, the same configuration scores
**0.791**. The bias is measured, not estimated:

| TF-IDF + SVD, 16 dims | LORO macro AUC |
|---|---|
| basis fitted on the whole corpus (the earlier, leaky protocol) | 0.833 |
| basis fitted per fold (correct) | 0.791 |
| **inflation attributable to the leak** | **+0.042** |

So the bar for this experiment was never 0.821. The honest bar is the hand
features alone at **0.774**, and the honest TF-IDF result is +0.017 with a
confidence interval that contains zero.

The general lesson is the one this project keeps relearning: *anything fitted
on the corpus must be fitted inside the fold.* That includes a vocabulary, an
SVD basis, a scaler, and a set of nearest-neighbour exemplars — not just the
model weights. Four of the five arms below would have been flattered by getting
this wrong.

---

## What was tested

| Model | Dims | How it ran |
|---|---|---|
| `sentence-transformers/all-MiniLM-L6-v2` | 384 | ONNX Runtime, mean-pooled, L2-normalised |
| `jinaai/jina-embeddings-v2-base-code` | 768 | same, a code-specific model as the contrast |
| TF-IDF over identifier subtokens | 4 000 → SVD | pure stdlib-reachable baseline |
| `microsoft/graphcodebert-base` | — | **not tested — see below** |

### GraphCodeBERT could not be tested

Not dropped quietly: it has **no ONNX export** on the Hub (`Xenova/graphcodebert-base`
does not exist; `microsoft/graphcodebert-base` ships no `onnx/` directory), and
PyTorch has published **no macOS x86_64 wheel since 2.3**, so
`sentence-transformers` cannot run on this machine at all. Both facts were
checked rather than assumed. Testing it needs either a torch-capable machine or
an ONNX export of our own, and neither was worth a half-day for a model whose
nearest tested relative (`jina-v2-base-code`, also code-specific) came out
*worse* than the general-purpose MiniLM.

### Why ONNX rather than `sentence-transformers`

Forced by the platform, but not a compromise: the proposed architecture
specified ONNX at runtime anyway, so this measures what would actually have
shipped. The encoder is mean-pooling over the attention mask then L2
normalisation — the standard recipe for this model family.

---

## Three forms, because raw dimensions are hopeless by construction

384 features against 213 positives is 0.55 events per variable, against a rule
of thumb of ten. The raw arm is measured to document that, not as a candidate.

| Form | Dims | Why |
|---|---|---|
| raw | 384 / 768 | what over-parameterisation does |
| SVD → 16 | 16 | comparable to the TF-IDF baseline |
| similarity to exemplars | 3 | mean cosine to the 5 nearest positives, to the 5 nearest negatives, and the margin — the Type4Py shape, and the only form that respects the events-per-variable budget *and* stays explainable ("it resembles these five labelled functions") |

The exemplar pool excludes the row being scored. Without that, every training
positive scores a perfect 1.0 against itself and the feature becomes a label
echo — the same class of mistake as the leak above, one layer down.

---

## Results

Hand features alone (17 features, the `literal` group dropped): **0.774**
— django-oscar 0.808, python-sepaxml 0.637, stripe-python 0.878.
Three of six repositories have ≥10 held-out positives and are scoreable.

| Model | Form | Dims | AUC | Δ vs hand |
|---|---|---|---|---|
| MiniLM-L6-v2 | raw | 384 | 0.743 | −0.031 |
| MiniLM-L6-v2 | **SVD → 16** | 16 | **0.826** | **+0.052** |
| MiniLM-L6-v2 | SVD → 16, *no hand features* | 16 | 0.783 | +0.009 |
| MiniLM-L6-v2 | similarity | 3 | 0.805 | +0.030 |
| MiniLM-L6-v2 | similarity, *no hand features* | 3 | 0.699 | −0.075 |
| jina-v2-base-code | raw | 768 | 0.784 | +0.010 |
| jina-v2-base-code | SVD → 16 | 16 | 0.785 | +0.011 |
| jina-v2-base-code | SVD → 16, *no hand features* | 16 | 0.695 | −0.079 |
| jina-v2-base-code | similarity | 3 | 0.817 | +0.043 |
| jina-v2-base-code | similarity, *no hand features* | 3 | 0.635 | −0.139 |
| TF-IDF subtokens | SVD → 16, per fold | 16 | 0.791 | +0.017 \* |

\* **Superseded.** That +0.017 is measured against the 17-feature baseline of
0.774 that existed when this experiment ran. Two features have since been
dropped on measured evidence (`pii_hits`, then `risky_annotation`), moving the
baseline to **0.798**. Re-measured against it, TF-IDF's contribution is
**−0.002** — it now adds nothing at all. Dropping `pii_hits` closed precisely
the gap TF-IDF had been filling, which is independent support for that drop.
The 0.791 figure itself is unchanged and verified; only the delta moved.

Three things in that table are worth more than the headline:

**Raw dimensions lose.** Exactly as the events-per-variable budget predicts.
384 features and 213 positives fits noise and reports it as skill; LORO is the
split that notices.

**No embedding beats the hand features on its own.** Every "no hand features"
row is at or below 0.783, and three are below 0.700. Whatever the embedding
contributes, it is a *supplement* to explicit features, not a replacement —
which is the opposite of the usual argument for reaching for one.

**The code-specific model is worse than the general one.** `jina-v2-base-code`
is trained on code and loses to `all-MiniLM-L6-v2` in every matched form. Our
items are short function bodies whose signal is largely identifier vocabulary,
and a model tuned to code *structure* appears to spend its capacity on exactly
the house-style regularities LORO punishes.

---

## Is the MiniLM gain real?

Two checks, because a macro average over three folds is three numbers.

### Stability: a shelf, not a spike

If 16 dimensions genuinely carry signal, neighbouring widths should carry
roughly the same signal. A spike at exactly 16 with nothing either side is a
lucky draw.

| Dims | MiniLM | Jina | TF-IDF |
|---:|---:|---:|---:|
| 4 | 0.803 | 0.804 | 0.817 |
| 8 | 0.806 | 0.802 | 0.776 |
| 12 | 0.818 | 0.790 | 0.778 |
| 16 | 0.826 | 0.785 | 0.791 |
| 20 | 0.815 | 0.792 | 0.786 |
| 24 | 0.825 | 0.799 | 0.764 |
| 32 | 0.813 | 0.800 | 0.709 |
| 48 | 0.837 | 0.733 | 0.696 |

**MiniLM is a shelf**: every width from 4 to 48 lands between 0.803 and 0.837,
all above 0.774. **TF-IDF is not**: it peaks at 4 dimensions and collapses to
0.696 by 48. That is the qualitative difference between the two, and it is the
check the original TF-IDF probe failed. MiniLM's signal survives the choice of
reduction width; TF-IDF's does not.

### Significance: a paired bootstrap

Both models score the same held-out rows, so the rows are resampled once and
both rescored on that resample, cancelling the row-to-row difficulty that
dominates an unpaired comparison. Resampled *within* fold, so fold composition
stays fixed. 2 000 draws.

| Arm | AUC | Δ | 95% CI | P(no gain) |
|---|---|---|---|---|
| MiniLM SVD-16 | 0.826 | +0.052 | [+0.003, +0.096] | **0.02** |
| Jina SVD-16 | 0.785 | +0.011 | [−0.034, +0.058] | 0.31 |
| TF-IDF SVD-16 | 0.791 | +0.017 | [−0.029, +0.062] | 0.23 |

MiniLM's interval clears zero; the other two do not. So the gain is real in the
statistical sense — and the lower bound is **+0.003**, which is to say the
evidence is consistent with the gain being almost nothing.

### What the gain actually is

| Fold | hand alone | + MiniLM SVD-16 |
|---|---:|---:|
| django-oscar | 0.808 | 0.813 |
| python-sepaxml | 0.637 | **0.882** |
| stripe-python | 0.878 | **0.783** |

Not a lift. A **reshuffle**: it repairs the one repository the hand features
handle worst and damages the one they handle best. Averaged over three folds
that reads as +0.052, and with six repositories it might read as anything. A
tool that gets better on SEPA XML and worse on a payments API has not learned
"handles money" — it has learned something about one of those two codebases,
and three folds cannot tell us which.

---

## The decision, and why it goes against the number

MiniLM SVD-16 wins on AUC and is still not shipping. The reasons are not
statistical:

1. **Cost of the dependency.** `onnxruntime` plus a model file, in an SDK whose
   entire argument is that a reviewer at a bank can read the coefficients. The
   current model is 17 named numbers in a JSON file. Swapping that for 17
   numbers plus 16 projections of a 384-dimensional vector from a downloaded
   binary gives up the one property that makes the model admissible at all.
2. **The gain is not the kind we need.** +0.052 that comes from a reshuffle
   between two repositories, with a CI lower bound of +0.003, is not the
   evidence to justify that trade.
3. **Nothing explains a score.** The hand features answer "why is this
   function in my queue?" with `sensitive_subscripts=2, docstring_disclaims=0`.
   An SVD component answers it with a number. For an advisory ranker whose
   whole job is to make a human's review faster, that is a real loss, not a
   presentational one.

The similarity-to-exemplars form (3 features, +0.030) is the one worth
revisiting, because it keeps the explanation — "this resembles these five
labelled functions, and you can read them" — and costs only three coefficients.
It scored below SVD-16 here, but it is the form that could ship, and the one to
re-measure when the corpus has enough positives to make six folds scoreable
instead of three.

## What would change this verdict

- **More repositories.** Three scoreable folds is the binding constraint on
  every conclusion in this document. Six would turn the reshuffle question from
  unanswerable into answerable.
- **GraphCodeBERT, properly.** Untested, for the platform reasons above. The
  jina result makes it a weak prospect, but a weak prospect is not a measurement.
- **A larger positive count.** At 213 positives, 16 dimensions is already
  13 events per variable. Nothing richer than that is measurable here, whatever
  the encoder.

## The 0.791 figure, verified four ways

A correction deserves the scrutiny it gave. The figure that replaced the
inflated 0.821 was itself checked:

| Check | Method | Result |
|---|---|---|
| The AUC implementation | hand-rolled `_auc` vs sklearn's `roc_auc_score`, on real fold output | **agree** to 1.1 × 10⁻¹⁶ |
| The fit | hand-rolled gradient descent vs sklearn `LogisticRegression` at `C = 1/l2` | **0.7934 vs 0.7934** |
| Determinism | two runs, same input | **identical** to nine decimals |
| Convergence | the headline at 250 → 200 000 gradient steps | **flat at 0.7934** from 1 000 steps on |

Two notes on how that went, because both were my errors rather than the code's.

**The first sklearn comparison disagreed by 0.013**, and I read it as evidence
the shipped fit was under-converged. It was not: the conversion from `l2` to
sklearn's `C` was wrong. For the objective
`mean(loss) + l2/(2n)·‖w‖²` the equivalent is `C = 1/l2`, not `C = n/l2`.
I had handed sklearn a model with effectively no regularisation and then
treated its worse generalisation as a defect in mine. Matched properly, the two
implementations agree exactly.

**The convergence sweep settles the related worry.** The headline does not move
between 1 000 and 200 000 steps, so the shipped 4 000 is not doing undeclared
regularisation by stopping early. 250 steps is marginally higher (0.7946),
which is early stopping, and is why the step count is fixed rather than tuned.

## Reproducing

The experiment lives in the scratchpad rather than the repository, because it
depends on `onnxruntime`, `sklearn` and `numpy`, none of which the SDK ships.
The LORO harness it measures against is `tools/train_classifier.py --loro`, and
the vectorised fit used for the 384-dimension arms was checked against the
shipped `classifier.fit` to within 1.3 × 10⁻¹⁵ on the real corpus before any
number above was recorded.
