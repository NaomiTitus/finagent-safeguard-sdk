# Review log

One block per review session. Flagged items must resolve to a test, a `TODO` with a
trigger, or a written decision — never to "noted".

---

## 2026-10-04 — apparatus build — mutation list authored

**Verdict:** changes needed
**Layers run:** 1a (partial), 1b
**Mutations:** 2 applied, **2 not caught**

### Flagged

- **F-001 — empty-iterator vacuity in the quotation tests.**
  `Registry.provisions()` returning an empty iterator leaves both
  `test_obligation_text_is_a_substring_of_its_pinned_span` and
  `test_every_provision_resolves_to_a_pinned_corpus_entry` passing. Verified by mutation
  R12. The numeral test is immune because it asserts `checked >= 5`; these two have no
  such guard, so they certify nothing if the iterator ever breaks.
  → becomes: test (minimum-count assertion on both)

- **F-002 — the bypass suite guesses method names.**
  `test_class_exposes_no_overridable_validation_hook` greps `dir()` for names containing
  `valid` / `enforce` / `check`, and `test_subclass_cannot_override_enforcement` overrides
  three guessed names. Verified by mutation A5: binding enforcement to the class as
  `_require_classification` and overriding it in a subclass constructs an agent around an
  unclassified tool **with all 80 tests passing**. Both tests are name-dependent where the
  property is structural.
  → becomes: test (generated attack over every attribute on the class)

### Not verified

- The remaining 34 mutations in `docs/mutation-list.md` are listed but unrun. They are the
  worklist for the Day 0, 1 and 2 review sessions.

---

## 2026-10-04 — `6244fdb..9066e9d` — session one, Day 2 (agent gate + bypass suite)

**Verdict:** changes needed
**Layers run:** 0, 1a, 1b, 1c, 2, 4 (3 not applicable — the diff touches no corpus or registry code)
**Mutations:** 7 applied, **3 not caught** (A1, A3b, A5)

### Approved

- Layer 0 claims audit: **11/11 verified.** Test counts, type-check status, the module-level
  enforcement structure, the empty public surface, the exception base class, and all three
  error-message contents check out against actual output.
- Layer 1b vacuity: **12 of 14 tests fail against a stubbed gate.** The two survivors
  (`test_empty_tool_list_is_permitted`, `test_class_exposes_no_overridable_validation_hook`)
  are legitimately structural rather than behavioural.
- Mutations A2, A3, A4, D6 are load-bearing, each killed by its predicted test.
- No scope creep: everything in the diff belongs to the day's deliverable.

### Flagged

- **F-004 — `__init__` is the overridable hook. CRITICAL.** A subclass that defines
  `__init__` and never calls `super().__init__` constructs an agent with no check;
  `object.__new__` plus attribute assignment, `pickle` and `copy.copy` do the same. No
  `__init_subclass__` or `__new__` guard exists. The module docstring claimed the opposite.
  Found by the cold reviewer, whose first question was "what calls `__init__`?"
  → becomes: test (both vectors) + guard
- **F-002 — the subclass-override tests guess method names. CRITICAL.** Verified by mutation
  A5 and independently by the cold reviewer: binding enforcement to the class as
  `_require_classification` and overriding it in a subclass bypasses the gate with all 80
  tests green. → becomes: test (generated attack over every class attribute)
- **F-005 — `module:qualname` identifies a name, not code. HIGH.** `__qualname__` is writable
  and the decorator does not wrap, so classifying a stub and rebinding the name defeats
  classification entirely. The `decorators.py` docstring presented the keying as a security
  win without noting its cost. → becomes: decision (design question — see below)
- **F-006 — callable objects and `functools.partial` raise `AttributeError`. HIGH.** The
  annotation `Sequence[Callable[..., Any]]` admits them; the runtime does not. A *classified*
  tool wrapped in `partial` becomes unusable. mypy passed because the annotation is wider
  than the runtime contract. → becomes: test + explicit handling or a narrowed annotation
- **F-003 — a fault-injection test reports green while asserting nothing. MED-HIGH.** The
  `if hasattr(agent_module, failing_symbol)` guard turns a renamed target into a pass.
  → becomes: test (`assert hasattr`, or drop the guard)
- **F-007 — fail-closed is untested against a permissive registry. MEDIUM.** Only exceptions
  are injected, never a registry that wrongly answers yes. `TOOL_REGISTRY` is public and
  writable. → becomes: test
- **F-008 — no post-construction invariant. MEDIUM.** `agent.tools` is reassignable with no
  re-validation, and is assigned *before* validation runs. → becomes: test + frozen state
- **F-001 — empty-iterator vacuity in two registry quotation tests. MEDIUM.** (Day 1 scope,
  carried.) → **RESOLVED** in `fix/registry-vacuity`: minimum-count guards added; mutation R12
  now killed by 4 tests. Bundled with the orphan gap and application-date provenance.
- **A1 — only the first unclassified tool is named. LOW.** → becomes: test (two offenders)
- **F-009 — the `dir()` substring test checks a naming convention. LOW.** Brittle in both
  directions. → becomes: subsumed by the F-002 fix

### Decisions

- **A3b — closed, not a defect.** Removing the prose lead-in from the error message kills no
  test, correctly: it is prose, not a claim. Recorded so the mutation list does not read as
  having a gap there.
- **Commit message of 9066e9d is left uncorrected in history.** It repeats the false
  anti-subclass claim. History is additive: the retraction is a later commit plus this entry,
  which is better evidence of the review working than a silently rewritten message.

### Layer 2 — criterion

> "Four bypass attempts documented as permanently failing tests."

**Not met.** `tests/bypass/` holds five test functions reducing to three distinct attack
classes, one of which is a structural assertion rather than a bypass attempt. "Permanently"
fails twice over: two tests survive a refactor that reopens the hole they guard, and one can
be silently disarmed into a vacuous pass. The most obvious bypass of a construction gate —
override `__init__`, skip `super()` — is neither attempted nor blocked.

### Open design question (blocks the F-005 remediation)

Tying a registration to *code* rather than to a name requires either wrapping the function
(rejected on Day 1 to preserve identity and signatures) or registering the code object
itself. This changes the decorator's contract and belongs in its own PR, separate from the
test fixes.

### Not verified

- The 29 remaining mutations in `docs/mutation-list.md` covering ingest, citation and
  registry. They are the worklist for sessions two and three.

---

## 2026-10-04 — `fix/registry-vacuity` — self-inflicted, caught by the new meta-suite

- **F-010 — a PR was pushed whose tests could not pass.** The branch carried the tests
  for PR 3 without the registry implementation they exercise: 9 failures, and a PR body
  claiming 85 passing. The claim was measured before the loss and written after it.
  **Cause:** `git checkout -- registry.py`, used to revert a mutation experiment by hand,
  discarded uncommitted work. `tools/run_mutations.py` applies and reverts safely and
  verifies the tree afterwards; the mistake was reverting outside it.
  **Lesson:** never hand-revert a file with uncommitted work in it. Prefer committing
  before mutating, and let the runner do the reverting.
  → RESOLVED by restoring the implementation; prevention is the CI gate, which would have
  caught this on push rather than two steps later.

---

## 2026-10-04 — `fix/registry-vacuity` — session three, and F-010 repeating

Third cold review returned `changes needed` with four blocking or high findings. All are
fixed; the review also stated plainly where the defences hold, which the previous one did not.

### Flagged and fixed

- **AMLR Art. 90 stages its application.** 10 July 2027 generally, 10 July 2029 for obliged
  entities under Art. 3(3)(n) and (o). `ApplicationDate` carried one date, so a caller got an
  answer two years early for that class with nothing hinting an exception existed. The class
  docstring says dates are what went wrong in the AI Act case; the first instrument it covered
  was the one with a staged date, and it flattened it. → `carve_outs`, plus a test asserting
  every Official-Journal-form date in the cited span is accounted for. This also narrows the
  wrong-clause risk: picking 2029 as the general date now leaves 2027 undeclared and fails.
- **Nothing tied `instrument` to `locus`.** An AMLR date citing DORA Art. 64 passed every
  check. → rejected at construction.
- **`provisions()` deduped on `corpus_key|id`**, silently dropping a second provision with the
  same citation but a different `obligation_text`, so a fabricated quotation never reached the
  substring check. → dedup on the whole frozen provision.
- **`strptime("%d %B %Y")` resolves months through `LC_TIME`.** A host app that had called
  `setlocale(LC_TIME, "de_DE")` would make the module raise at import. → explicit English
  month map; verified under `de_DE` and `fr_FR`.

### Deferred, with triggers

- **F-011** — paragraph-level citations are unvalidated because pinning is article-level.
  Mutation R13 cites a nonexistent `5(9)(z)` and passes. Trigger: Day 6 entailment.
- Two-way-lock exit: setting `applies_from` to the sentinel removes an instrument from both
  the covered and documented sets. Trigger: next registry PR.
- Orphan test cannot see `Provision`s held inside a module-level container, only bound names.
  Trigger: next registry PR.

### Process finding — F-012, and it is the serious one

**F-010 recurred within the hour.** `git checkout -- registry.py`, used again to revert a
mutation experiment by hand, again discarded uncommitted implementation work. The F-010 entry
recorded the lesson in prose; prose did not change the behaviour.

Then, recovering, I re-applied the edits, ran the suite, and **committed a state where the
suite errored on collection** — one string replacement had silently failed to match because
the target text I supplied omitted a docstring. The commit message asserted "89 passing". It
was not true at commit time.

Two concrete rules, not aspirations:

1. **Commit before mutating.** The mutation runner reverts safely and verifies the tree; a
   hand `git checkout --` does not know what is uncommitted. If work is committed first, a
   bad revert costs nothing.
2. **A silent `str.replace` miss is a defect class.** Replacements must assert the anchor was
   found. Where an edit is large, anchor on indices and verify, or the file is left in a state
   nobody inspected.

Both failures share a shape: an assertion about state made from memory of a command run
earlier, not from the state itself. That is precisely what Layer 0 exists to catch, and
neither was caught by me.
## 2026-10-04 — `main..fix/registry-vacuity` — session two, PR #1

**Verdict:** approved with notes
**Layers run:** 0, 1a, 1c, 3 (1b covered by the meta-suite on PR #2; 4 pending)
**Mutations:** R12 now caught by 4 tests

### Approved

- Layer 0: **5/5 claims verified** — 85 passing, mypy clean, digest unchanged at
  `f57c993491ab`, all three cited articles already pinned.
- Layer 3, AMLR Art. 90: *"It shall apply from 10 July 2027, except in relation to obliged
  entities referred to in Article 3, points (3)(n) and (o)"* — supports the claim exactly.
- Layer 3, DORA Art. 64: *"It shall apply from 17 January 2025"* — supports the claim exactly.
- F-001 resolved: mutation R12 killed by 4 tests, was killed by none.

### Flagged

- **F-011 — a numeral's *role* is unverified when an article mentions it more than once.
  MEDIUM.** Pinning is article-level; citations are paragraph-level. `EUR 1 000` occurs
  **three times** in TFR Art. 5, in at least two roles: Art. 5(2) is an
  information-on-request boundary, Art. 5(3) is the derogation from the Art. 4(4)
  verification duty. The provenance test only asserts the numeral appears *somewhere in the
  article*, so it cannot confirm the number is in the role the registry assigns it.

  The registry's claim here is **correct** — the span does contain "By way of derogation from
  Article 4(4)" and "need not verify". The defect is that nothing would have told us if it
  were wrong. This is the same shape as the founding EUR 30 error: real number, real
  citation, unverified role.

  → becomes: mutation **R13**, baselined as `not_caught`, so CI reports the gap on every run
  and it cannot be forgotten. **Trigger:** closes with the Day 6 entailment check, which
  reasons over the quote and can therefore distinguish 5(2) from 5(3). When it closes, the
  baseline must be updated by hand — the same deliberate friction as the corpus golden
  constants.

### Process finding

- I wrote "logged" in the session report one message *after* running Layer 0, before the
  entry existed. Caught by the user, not by me. A claim about the review process is still a
  claim, and the audit does not exempt itself.
