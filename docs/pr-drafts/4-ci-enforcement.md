# Enforce the review layers in CI, and block merges on them

## Summary

Three of the five review layers were habits someone had to remember. This makes them
properties the build enforces. It also adds the GitHub Actions workflow and the branch
protection that blocks a merge when they fail.

The motivation is immediate: the previous PR on this stack shipped with nine failing tests
and a description claiming 85 passing (F-010). Layer 0 would have caught it in seconds. It
didn't, because nothing ran it.

## What becomes automatic

**Vacuity (Layer 1b).** Twenty-one tests loop over a registry collection and assert per
item. Each would pass on an empty collection — mutation R12 proved it. Rather than edit
twenty-one tests, `TestCollectionsAreNonEmpty` makes the collections incapable of being
empty. If they can't empty, no loop over them is vacuous. One guard, full coverage.

**Name-assertion match (Layer 1c), partly.** Three mechanical rules: a test whose name
promises a rejection (`*_raises*`, `*_rejected*`, `*_cannot_*`) must contain
`pytest.raises` or `assert not`; every test must assert something; and no test may wrap its
whole body in an `if hasattr(...)` guard. The semantic version — does this name describe
what this body checks — stays a human job and is not claimed here.

**Mutation (Layer 1a).** `tools/run_mutations.py --check` runs every curated mutation
against a recorded baseline. A mutation drifting from `caught` to `not_caught` fails the
build. A mutation becoming caught is a fix and requires updating the baseline by hand in the
same commit — the same deliberate friction as the corpus golden constants.

## It earned its place before it was finished

Writing the meta-suite surfaced three real problems:

- `test_no_exception_path_permits_construction` wrapped its body in `if hasattr(...)`, so
  renaming its target made it report green while asserting nothing (F-003, now fixed here
  rather than in the gate PR, because the test that detects it lives here).
- `test_extraction_makes_no_network_call` contained no assertion at all. It relied on a
  fixture to raise, which works but hides what the test checks. It now asserts the result.
- The mutation checker's own clean-tree check conflated "a mutation leaked" with "the tree
  was already dirty". It now snapshots dirtiness up front and fails only on a change.

## The workflow

Two required jobs, plus a scheduled one.

- **fast** — `pytest` (which now includes the vacuity and name guards) and `mypy --strict`.
- **mutation** — the baseline check.
- **staleness** — scheduled only; goes red with no code change when a regulatory `review_by`
  date passes.

`concurrency` with `cancel-in-progress` so successive pushes to one PR don't each run a full
build. `permissions: contents: read`. The fast job references **no secrets at all**, which
is what makes "the suite runs on a fresh clone with no credentials" a structural fact rather
than a claim.

## What is still not automatic

Layers 2, 3 and 4 — goal alignment, legal fidelity, and the cold reviewer. Those need a
day's context and a human, and are invoked with `/review-day`. Automating them is not
attempted, and pretending otherwise would be the same category of overclaim this project
keeps catching.

## Verification

```
python3 -m pytest tests/ -q                    # 95 passed
python3 tools/run_mutations.py --check         # 7 match baseline
python3 -m mypy --strict finagent_safeguard/   # clean
```
