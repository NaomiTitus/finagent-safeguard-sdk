# Harden the construction gate, and scope what it can promise

Review session one found four ways around the agent construction gate, and found that two
of the tests written to prevent exactly that were checking the wrong thing. Details in
`docs/review-log.md` (F-002, F-003, F-004, F-007, F-008, F-009, A1).

## The thing worth reading first

Writing this fix changed my view of what the gate is for.

The original design treated the construction check as a security boundary, and the
docstring claimed a subclass "has nothing to override". That was false — `__init__` is
itself the hook, and a subclass that defines it and never calls `super()` gets an
unchecked agent. But chasing that led somewhere more useful: **you cannot make a Python
class unforgeable from inside the same process.** A determined developer can always
assemble an object. Any amount of `__new__` and `__init_subclass__` machinery only raises
the effort.

So this PR stops pretending otherwise and states the threat model instead:

- The construction gate exists for the developer who **forgot** to classify a tool, and
  for a tool an AI assistant generated without classifying. It is fast, local feedback.
- The control that actually **blocks a merge** is the CI gate — static analysis of the
  source plus the test suite. That reads the code rather than trusting the runtime, so
  forging an object in a test does not get past it.

Framing it that way, the gate's job is to be unmissable, not unforgeable. That is a
weaker claim and an honest one, and it is now in the module docstring.

## What changes

- **`__init_subclass__` wraps a subclass's `__init__`** so the classification check runs
  whether or not the subclass calls `super()`. This closes the realistic vector — the
  developer who subclasses and reimplements construction — without pretending to close
  the unrealistic one.
- **Construction state is sealed.** `tools` and `categories` become read-only after
  validation, and `tools` is no longer assigned before the check runs. Previously
  `agent.tools = (...)` silently replaced the validated set.
- **The subclass-override test stops guessing names.** It now generates an attack for
  *every* attribute on the class rather than three names I happened to think of. The old
  `dir()` substring test is removed: it checked a naming convention, would have fired on
  an innocent `checkpoint_dir`, and missed anything called `_gate` or `_require`.
- **A disarmed test is re-armed.** One fault-injection case was wrapped in
  `if hasattr(...)`, so renaming its target made it report green while asserting nothing.
  It now asserts the target exists.
- **Fail-closed is tested against the realistic failure.** Previously only exceptions were
  injected. The likelier fail-open is a registry that wrongly answers *yes*, so there is
  now a test for that.
- **The error names every offender**, not just the first.

## What this deliberately does not do

No attempt to block `object.__new__` plus attribute assignment, or unpickling. Both are
deliberate acts by someone with code execution in the same process, and the CI gate is the
control for that. Saying so beats shipping machinery that implies a guarantee it cannot
give.

## Verifying

`python3 tools/run_mutations.py A1 A5 A6 A7 A11 A12` — all currently uncaught; all should
be caught after this. Suite and `mypy --strict` stay green.
