# Make registry tests capable of failing, and forbid orphans

## Summary

Two registry meta-tests were structurally incapable of failing. Fixes that, and bundles the
"orphan gap" found while costing the entailment check: regulatory *numbers* in this registry
all name a source and are machine-checked against pinned text, but `applies_from` *dates*
lived only in prose `note` fields, with the articles that state them cited by nothing.

## The issues

**Tautological meta-tests (F-001).** `test_obligation_text_is_a_substring_of_its_pinned_span`
and `test_every_provision_resolves_to_a_pinned_corpus_entry` both loop over the registry's
provisions and assert per item. On an empty collection they pass having checked nothing —
verified by mutation R12, which makes `provisions()` return an empty iterator and left both
green. The quotation test is what enforces *quote, don't paraphrase* on the registry's own
claims about the law, so a version that can pass vacuously reads as assurance it is not
providing.

**The orphan gap.** Three pinned spans were cited by nothing (`AMLR art_90`, `DORA art_64`,
`TFR art_5`) and one declared provision (`TFR_ART_5`) was reachable from nothing. So the
EUR 1 000 Transfer of Funds figure — one of the most commonly misdescribed in this area —
was verified against primary text and then never encoded. And two application dates had no
machine-checked citation at all.

Dates are precisely the class of fact that went wrong in the AI Act case, where a published
deadline moved. Prose would not have caught it.

## The fix

**Minimum-count assertions** on both quotation tests, the same guard the numeral test already
had — and had only because `assert checked >= 5` happened to be written into it.

**An `ApplicationDate` type** carrying the instrument, the date, the provision that states it,
and the date as the Official Journal writes it. A test asserts that string appears in the
pinned span, so "AMLR applies from 10 July 2027" is now checked against Art. 90 rather than
asserted in a comment.

**A bidirectional orphan constraint**, enforced at test time:

- no pinned span is uncited
- no declared provision is unreachable from the registry

**A shrinking allowlist.** PSD2, the RTS on SCA, GDPR and TFR still have no date provenance,
because the articles stating their application dates are not pinned yet. The test asserts the
gap is *exactly* that set, so adding a fifth instrument without date provenance fails the
build instead of quietly joining the gap.

## Two corrections to the earlier draft of this PR

- **The corpus digest does not change.** An earlier draft (mine) said it would, and that the
  golden constants needed a hand update. Wrong: all three articles were already pinned. Citing
  an already-pinned span from the registry does not touch `corpus/`. Digest is `f57c993491ab`
  before and after.
- The tests live in `tests/test_registry_integrity.py`, not `tests/registry/test_provenance.py`.

## Verification

```
python3 -m pytest tests/ -q                 # 85 passed (was 80)
python3 -m mypy --strict finagent_safeguard/  # clean
python3 tools/run_mutations.py R12          # now caught by 4 tests; was caught by none
```
