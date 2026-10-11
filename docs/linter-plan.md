# The linter: agreed plan

**Status:** agreed 2026-10-09. Supersedes the day-by-day schedule in `PLAN.md` §7 for
everything after Day 3.

## Scope, and why it narrowed

The project is a portfolio piece for a job application, and the deadline is real. The decision
is to **finish the linter and ship nothing else half-built**, and to describe the rest as
future work in the repository description rather than pretend it exists.

What that rules out is stated here so nobody has to infer it: no runtime enforcement, no
cross-module call-graph propagation, no six-way category classifier. `docs/category-decision.md`
records why the last one is not merely deferred but unachievable at useful quality.

## Architecture

```
            target codebase
                   |
        1. finagent-lint CLI            argparse, exit 0/1/2
                   |
        2. AST signal scanner           params, annotations, call targets, imports
                   |
        3. signal -> candidate map      cites only SHA-256 pinned provisions
                   |
        4. --fix writes the flag        @regulated_tool(REVIEW_REQUIRED) + candidates
```

Three properties are non-negotiable, because each was arrived at the hard way.

**No model in the scan path.** CI must be reproducible. A thousand temperature-zero
completions have been measured producing eighty distinct outputs, caused by batching in the
serving stack and unfixable from the client. Whatever a model contributes must be frozen into
a reviewed artefact before CI reads it.

**The tool proposes; a human asserts.** The linter finds the function and names candidate
provisions. It does not decide which regulation applies. This is stated up front as the
premise of the tool, not buried as a caveat.

**Candidates come only from pinned text.** A candidate provision is cited from
`registry.py`, whose claims are themselves verified against SHA-256 pinned CELLAR spans by
`tools/check_entailment.py`. No provision is cited that has not been pinned.

## Sequence

| Day | Work | Notes |
|---|---|---|
| 1 | `REVIEW_REQUIRED` in the enum; agent gate rejects it; strengthen the write guard | ~2 hrs, then the CLI |
| 1 | CLI entry point: `finagent-lint [PATHS] [--fix] [--diff-only] [--strict]` | exit 0 clean, 1 unresolved, 2 invocation error |
| 2 | Signal -> candidate provision mapping | pinned provisions only |
| 3 | ~~`--fix` emits the flag plus candidate citations; diff-only ratchet~~ **done** | `--since REF` ratchets on touched lines, not touched files |
| 4 | Corpus harvest, pointer-only | licences verified below |
| 5 | **Owner labels 150 functions, ~2 hrs**, labels frozen and committed | 20 of them twice, a day apart |
| 6 | ~~Train the binary ranker, measure, write up~~ **done** | and the measurement overturned it — see below |
| 7-8 | LLM annotator experiment: Opus/Sonnet/Haiku, 3 runs each, against the frozen 180 | `docs/llm-annotator-experiment.md` |
| 9 | 996-item training corpus labelled by Sonnet | `benchmark/train-labels.jsonl` |
| 10 | **Leave-one-repository-out**, and the confounder it found | `docs/feature-measurement.md` |
| 10 | `dataflow` + `docstring` features; `literal` group excluded | LORO AUC 0.645 → **0.774** |
| 10 | Embedding experiment, with a stated bar, and the verdict was no | `docs/embedding-experiment.md` |
| 10 | `pii_hits` dropped — collinear with a feature derived from it | 0.774 → **0.793** |
| 11 | `risky_annotation` dropped — harmful; one row in 996, largest weight | 0.793 → **0.798** |
| 11 | `README.md`, and the per-feature view behind these drops | `--loro-per-feature` |
| 11 | The approval binds: `--expect-plan`, and `base_sha` re-read at write time | layer 3 was advisory until here |
| 11 | `--exclude GLOB`, after a first run of 80% test noise | `PurePath.match`, not `fnmatch` |

### What day 10 changed about days 1-6

The plan above assumed the measurement would be *stratified cross-validation
against the hand labels*. It was, and it was wrong — not in its arithmetic but
in its split. Both corpora draw from the same six repositories, whose positive
rates run from 0% to 70%, so the same repositories sit on **both sides** of
every stratified fold. Under that split the model scored respectably and the
`literal` group (length and punctuation counts) was the single strongest
contributor.

Held out by repository instead, the ranking inverted: `literal` went from
+0.172 to **−0.069**, and a one-bit "which repository is this?" baseline matched
the full sixteen-feature model to within noise (paired bootstrap +0.008,
P(no better) = 0.47). The model had learned the corpus, not the question.

Everything downstream of that — the two new feature groups, the excluded group,
the embedding experiment and its rejection — follows from changing the split,
not from changing the model. **The split was the finding.**

### Day 1 detail: three small things that are not optional

1. `REVIEW_REQUIRED` joins `FinancialCategory`. Without it `--fix` writes a file that raises
   `AttributeError` on import.
2. `_require_classification` must reject it. Today it only asks *is there a registration?*,
   so a tool tagged `REVIEW_REQUIRED` passes the runtime gate as classified. That is the one
   place human review cannot help.
3. The fourth write guard is weaker than its name. It verifies that *import statements*
   resolve, not that the module *executes* -- it wrote the `AttributeError` file above without
   complaint. It should execute the module.

## The classifier experiment

**Target:** binary. *Does this function handle regulated data?* Not the six-way category:
trained annotators reach Krippendorff's alpha of 0.251 on that task, and the best of eleven
methods on the nearest published benchmark scored 5.75% macro-F1.

**Two label sets, with different jobs.**

| | Source | Count | Cost |
|---|---|---|---|
| Training | the AST rule's own verdicts | thousands | free |
| Evaluation | the owner, by hand | 150 | ~2 hrs |

Training on the AST rule's verdicts is legitimate distant supervision. What is *not*
legitimate is scoring against them -- that is the circularity Kang, Aw & Lo (arXiv
`2202.05982`) found inflating the actionable-warning literature, where the oracle "produces
labels that do not agree with human oracles". Evaluating against independent human labels is
what makes the number mean something, and it measures exactly what the AST rule's blind spots
cost.

```
                          scored against the 150 hand labels
    AST rule (baseline)              ->  ?
    Classifier                       ->  ?
    Owner's own consistency          ->  ?   <- the ceiling
```

**Two conditions.** Labels frozen and committed before the classifier is written, so it cannot
be tuned toward them. And 20 of the 150 labelled twice a day apart, because a classifier
scoring 0.85 against an annotator who agrees with themselves 0.80 of the time has measured
nothing.

**Metric:** precision at coverage, with explicit abstention -- the Typilus framing. Typilus
reached 70% coverage at 95% precision because a type checker could verify every label; the
comparison is honest about why our ceiling is lower.

### Corpus, licences verified

Pointer-only: URL, commit SHA, line range, SHA-256. Nothing vendored, so no licence
obligation is triggered. Reuses the `corpus/MANIFEST.json` pattern.

| Repository | Licence | Role |
|---|---|---|
| `stripe-python` | MIT | real payment API surface |
| `python-sepaxml` | MIT | SEPA credit transfers |
| `pyiso20022` | MIT | **the blind spot** -- 6 of its 66 functions are flagged and **none of the 13 named for ISO 20022 messages** |
| `schwifty` | MIT | IBAN and BIC handling |
| `django-oscar` | BSD-3 | commerce, money and personal data mixed |
| `saleor` | BSD-3 | payment capture |
| CPython stdlib | PSF | negatives -- contains no financial code |

**Excluded:** `plaid-python`. MIT, but 41% structurally duplicate functions with one body
repeated 2,251 times; including it reproduces GDPR-Bench-Android's worst defect.

### The label source that costs nothing and compounds

Every time a developer replaces `REVIEW_REQUIRED` with a confirmed category, that is a sound
label for the only question that finally matters -- *would a human have agreed?* Log these
from the first release. Typilus was possible because annotated corpora already existed; ours
begins accumulating the day the tool ships.

## Corrections to the spec this plan came from

Recorded because they are the kind of thing that gets rebuilt by accident.

| Spec said | Reality |
|---|---|
| `src/finagent_safeguard/{cli,linter,mapping,fixer}.py` | No `src/` layout. The linter is `finagent_safeguard/cli/linter.py`, 744 lines, 262 tests |
| Day 2: build an AST visitor | It exists and is hardened. **Extend it** |
| Day 3: build a refactorer | `plan_fix`/`apply_fix` exist with four write guards, validated over 16,800 generated files across three cold reviews. Rewriting discards that |
| Candidates include `PSD2 Art 97(1)(a)` | Not declared -- but articles are pinned whole, so the text is already inside the pinned `art_97` span. Needs one `Provision` constant, no re-pin |
| ICT vendor signal: `acme_risk`, `fraud_vendor` | Placeholder names, not signals. Needs a real rule, probably calls into any module outside the first-party package |
| Classifier labels: AST rules, "zero LLM label noise" | The noise was never the problem. The AST oracle is unsound -- 51 flags / 0 correct on stdlib, 0 of 13 ISO-20022-named functions -- so it is fine for training and useless for scoring |

## Known gaps, carried deliberately

- ~~`tools/check_entailment.py --check` and `tools/kappa.py` are not in CI.~~ **Closed.** The
  entailment audit now runs as a CI job against the `ACCEPTED` baseline in
  `check_entailment.py`, failing on drift rather than demanding every claim be confirmed --
  fourteen stand at `REVIEW_REQUIRED` because the judges agree on the answer and cite
  different spans, and one is a genuine interpretive split. Mutation coverage of new code
  (layer 1c) runs as a second job. `tools/kappa.py` has nothing to gate until the 150 labels
  exist; its tests run with the rest.
- `--check` detects a changed rubric or a re-pinned corpus, but **not a changed claim**.
  Editing an obligation leaves its verdict looking current. A claim digest in the run header
  would close it. **Still open**, and now stated in the README's limits too.
- ~~A developer's first run is mostly test-file noise.~~ **Closed** by `--exclude GLOB`,
  which is an adoption aid rather than a boundary: everything it skips is simply not
  scanned, so the count excluded is always printed and a pattern matching nothing warns.
- ~~`plan_fix` and `apply_fix` do not connect, so a human cannot approve the exact
  edit.~~ **Closed.** `--diff-only` prints a digest of the bytes it showed;
  `--fix --expect-plan` writes only that edit; and `base_sha` is re-read at write time,
  because separating the plan from the write would otherwise *widen* the race it exists
  to close.
- **Three of six leave-one-out folds are unscoreable, and no amount of further
  harvesting from these six repositories fixes it.** `cpython-stdlib` has a 0% positive
  rate because the standard library holds no payment code; `schwifty` has 1% because it
  is an IBAN *validator*; `pyiso20022` is exhausted at all 66 of its functions. Six
  scoreable folds needs three further repositories above roughly a 10% positive rate.
- One entailment claim remains dissented: GDPR Art 5(1)(c), where Haiku 5.5 reads the point as
  passive and naming no addressee while Opus and Sonnet take the structural reading via Art
  5(2). Both are defensible. It stays flagged rather than forced green.

---

## Correction: the pyiso20022 figure

"0 of 66 functions trip the word lists" has been quoted in several commit
messages and pull request descriptions in this repository. Re-measured
first-hand against `phoughton/pyiso20022@cfb785fc`:

| | |
|---|---|
| Functions, including tests and examples | 66 |
| Flagged by the linter | **6** |
| Functions named for an ISO 20022 message (`pain`, `pacs`, `camt`, `remt`) | 13 |
| Of those, flagged | **0** |

The substance survives and sharpens. The six that are flagged are
`_validate_min_inclusive` and its siblings, caught on a `Decimal` annotation
rather than on anything to do with payments -- so the tool finds the validator's
helpers and misses every function that builds a credit transfer.

Two notes on how the number went wrong, because both are the kind of mistake
that repeats. The original "0 of 66" collapsed two different counts into one
figure. And the first attempt to re-measure it excluded any path containing
"test", which dropped 27 of the 66 functions including
`create_pain001_001_008` -- the exact function the claim was about. The
harvester keeps tests and examples for that reason.
