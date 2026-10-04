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
