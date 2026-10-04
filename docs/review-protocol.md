# Review protocol

Reviewing machine-written code is a triage problem. The reviewer's scarce resource is
attention, so the protocol's job is to spend it only where judgement is irreplaceable and
to settle everything else with evidence.

Run it with `/review-day`. The operational detail lives in
`.claude/skills/review-day/SKILL.md`; this file explains why it is shaped this way.

## The author is the wrong reviewer

A self-review where the author re-narrates their work confirms their own reasoning, because
they still have it. Two structural answers:

1. **Evidence, not memory.** Findings come from re-reading the diff and from commands whose
   output is quoted. Reasoning from earlier in the session is not admissible.
2. **A cold reviewer for the judgement layers.** A subagent receives the diff, the test
   output, and the day's one-line exit criterion — and deliberately **not** `PLAN.md`, the
   commit messages, or any of the conversation. Those carry the author's framing, and
   inheriting it defeats the exercise.

The precedent: the Day 0 corpus hashes were committed and described as change detection. A
fresh agent reading only the code established in minutes that they were self-consistency
and would pass through an arbitrary corpus substitution. The author had been looking at
them for an hour.

## Five layers, cheapest first

| Layer | Question | Who |
|---|---|---|
| 0 Claims audit | Is the commit message true? | machine |
| 1a Mutation evidence | Are the tests load-bearing? | machine |
| 1b Vacuity | What makes each test fail? | machine |
| 1c Name match | Does the name match the assertion? | machine |
| 2 Goal alignment | Right thing built, nothing extra? | human, 2 min |
| 3 Legal fidelity | Does the quote support the claim? | human |
| 4 Absence | What is missing? | cold reviewer + human |

Layer 0 exists to make commit messages trustworthy enough to skim later. Layer 3 is a
reading task with no code in it — a quote, a claim, a yes/no — because that keeps the one
irreplaceable layer cheap enough to actually perform.

## Mutation testing is the load-bearing technique

This project's founding error was *a passing test protecting a wrong implementation*.
Mutation testing is the only technique that detects that class directly, which is why the
mutation list is a first-class project asset (`docs/mutation-list.md`) rather than a tool
invocation. A curated list also documents what each test is for.

It earned its place immediately: authoring the list exposed two holes in tests that had
been written, praised and committed. Both are recorded as F-001 and F-002 in
`docs/review-log.md`.

## Budget

Twelve surfaced items, about thirty minutes, one item at a time with an explicit
approve / flag / change. Anything longer does not get done, and an unperformed review is
worse than none because it manufactures confidence without earning it.

## Cadence

Layers 0 and 1 run per commit and are fully mechanical. Layers 2 to 4 run per day as a
session, because legal fidelity needs a day's context to be worth the attention.

## Exit test

1. Does it do what the commit message claims?
2. If it didn't, would a test have caught it?
3. What did we decide not to do, and is it written down?

An unanswerable (2) is the finding that matters most here.
