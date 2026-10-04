---
name: review-day
description: Step the user through a structured review of one day's or one commit's implementation. Five layers, cheapest first - claims audit, test integrity with mutation evidence, goal alignment, legal fidelity, absence review - with a cold reviewer that never sees the plan or the author's justifications. Use when the user asks to review work, review a day, review a commit, or check that an implementation is correct and its tests load-bearing.
---

# Review a day's work

Reviewing AI-written code is a triage problem, not a reading problem. Your job is to
identify the small fraction that needs the user's judgement and to settle the rest with
evidence, so they can approve the remainder with a clear conscience.

## Standing rules

1. **Evidence, never memory.** Every finding must come from re-reading the diff and from
   commands whose output you quote. You may not justify a verdict with reasoning from
   earlier in the conversation. If you wrote the code, your opinion of it is not evidence.
2. **Budget is 12 surfaced items and ~30 minutes.** Rank by consequence. Everything else
   goes in an appendix. An unperformed review is the worst outcome, so protect the budget.
3. **One item at a time.** Present, wait for `approve` / `flag` / `change`, move on. Never
   emit the whole review as one wall of text.
4. **Nothing resolves as "noted".** A flagged item becomes a test, a `TODO` with a trigger,
   or a written decision in `docs/review-log.md`.
5. **Report inability plainly.** "I could not verify X" is a valid and valuable finding.
   Never paper over an unverifiable claim.

## Scope

Ask which commit or day is under review if it is not stated. Establish the diff range and
use it for everything:

```bash
git log --oneline <base>..<head>
git diff --stat <base>..<head>
```

## Layer 0 - Claims audit (mechanical)

The only layer that reads the commit message. Extract every factual claim and check it.

- test counts, type-check status, "N provisions", "closes X" -> run the command, quote output
- "closes a hole" -> demonstrate it: show the test failing against the pre-change code
  (`git stash` / `git checkout <base> -- <path>` then run the new test)
- any number in prose -> recompute it

Report as a table: claim, check performed, actual, verdict. A Layer 0 failure is a serious
finding in its own right, because the point of this layer is to make commit messages
trustworthy enough to skim.

## Layer 1 - Test integrity (mechanical, adversarial)

The question is never "do the tests pass". It is **"are these tests load-bearing?"**

### 1a. Mutation evidence

Work through `docs/mutation-list.md` for the modules in the diff. For each mutation: apply
it, run the suite, record which test dies, revert.

```
CLAIM   <behaviour the code claims>
MUTATE  <the single edit>
RESULT  <test id> FAILED        (or: all N tests still pass)
VERDICT load-bearing | NOT CAUGHT
```

Always revert. Verify with `git diff --quiet` before moving on. Any `NOT CAUGHT` is an
automatic surfaced item regardless of budget pressure.

Any new behaviour in the diff with no entry in the mutation list is itself a finding: add
the entry as part of the review.

### 1b. Vacuity check

A test that passes against a stubbed implementation is decoration. For each new test, ask
what makes it fail. Two specific shapes to hunt:

- **Empty-iterator vacuity.** A test that loops over a collection and asserts per item
  passes trivially when the collection is empty. Every such test needs a minimum-count
  assertion.
- **Substring-matcher vacuity.** A structural test that greps names for a fixed set of
  substrings only catches names containing them. Ask what a determined developer would
  name the thing to slip past it.

### 1c. Name-assertion match

For each new test, does the name describe what the body asserts? Report any test whose name
promises more than its assertions deliver - that gap is how a suite looks thorough while
checking something adjacent and easier.

## Layer 2 - Goal alignment (human, ~2 min)

Present the day's **exit criterion only** beside what landed. Two questions:

1. Is the criterion met?
2. Did anything arrive that nobody asked for?

Scope creep is cheap to catch here and nearly invisible later.

## Layer 3 - Legal fidelity (human, irreplaceable)

Only for changes touching `corpus/` or `finagent_safeguard/regulation/`. Shape it as a
bounded reading task with no code in it: the pinned quote, then the typed claim, then a
yes/no. Never more than one provision per item.

```
PINNED   <verbatim span from corpus/, quoted exactly>
CLAIMED  <the registry's assertion: direction, addressee, numeral, condition shape>
ASK      Does the quote support the claim?
```

Six prompts, matching the entailment rubric: direction (duty or permission), addressee,
numerals in the role claimed, condition structure, scope, and absence (does an asserted
threshold exist at all).

## Layer 4 - Absence review (human, hardest)

Run the **cold reviewer** here. Fixed prompts, not open-ended staring:

- Which failure modes are unhandled?
- What did the author decide not to do, and is that written down?
- What would a hostile reviewer ask first?
- What would have to be true for this to be wrong?

### Cold reviewer brief

Spawn one subagent per review. It receives **only**:

- the diff for the range
- the test and type-check output
- the day's one-line exit criterion, quoted verbatim

It must **not** receive `PLAN.md`, `docs/`, commit messages, or any part of this
conversation. Those carry the author's framing, and inheriting it defeats the purpose.
Include this instruction in the prompt:

> You have not been given the project plan, the commit messages, or the author's reasoning,
> and you must not ask for them. Judge this diff against the stated criterion and against
> the code alone. Where the code's intent is unclear from the code, say so - unclear intent
> is a finding, not a question to resolve.

## Session output

Append one block to `docs/review-log.md`:

```markdown
## <date> - <range> - <what was reviewed>

**Verdict:** approved | approved with notes | changes needed
**Layers run:** 0 1a 1b 1c 2 3 4
**Mutations:** N applied, M not caught

### Approved
- <claim> - <evidence>

### Flagged
- <finding> -> becomes: test | TODO(<trigger>) | decision

### Deferred
- <item> - revisit when <trigger>

### Not verified
- <claim> - <why not>
```

## Exit test

The session is done when the user can answer, without looking anything up:

1. Does it do what the commit message claims?
2. If it didn't, would a test have caught it?
3. What did we decide not to do, and is it written down?

An unanswerable (2) is the finding that matters most in this project, whose founding error
was a passing test protecting a wrong implementation.

## Cadence

- **Per commit:** Layers 0 and 1. Fully mechanical; no session needed.
- **Per day:** Layers 2, 3 and 4 with the cold reviewer, as a session with the user.
