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
