# Can a model label this corpus?

**Status:** run 2026-10-11. The question was whether LLM annotation is reliable enough to
build a larger corpus from, with the 160 human labels as the gold standard.

## Method

Three models, three runs each, 1,440 judgements. Each gets
`finagent_safeguard/regulation/prompts/labelling_rubric.txt` -- the labelling rule stated in
the abstract -- plus one function, and answers the same binary question the human answered.

**Contamination control.** `docs/labelling-decisions.md` names about eight worked examples
*with their answers*, and every one is in the corpus. Sending it would leak the answers to
precisely the items that discriminate. The rubric therefore names no example functions, so
the models work from a **weaker brief than the human had**.

**The gold standard runs one way.** Models are scored against the human labels, never the
reverse, and the ceiling is the human's own self-agreement: Cohen's kappa **0.762**. A model
at 0.76 is indistinguishable from a second competent annotator; one at 0.95 would be
suspicious rather than excellent, and the first thing to check would be contamination.

## Result

| model | kappa vs human | raw | self-kappa | precision | recall |
|---|---|---|---|---|---|
| claude-sonnet-5 | **0.805** | 94% | 1.000 | 93% | **77%** |
| claude-opus-5 | 0.773 | 93% | 0.976 | **100%** | 69% |
| claude-haiku-5-5 | 0.732 | 92% | 0.928 | 96% | 66% |
| *the human* | *0.762* | -- | -- | -- | -- |
| *the word-list rule* | -- | -- | -- | *61%* | *31%* |

2 of 1,440 judgements failed.

**All three models sit at the human's ceiling**, and every one is dramatically better than the
rule they would replace. Opus made **zero false positives across 160 items**.

Sonnet scored highest and is two and a half times cheaper than Opus. That is worth stating
plainly because the intuition runs the other way.

## Three findings worth more than the headline

**The models are systematically conservative.** Every single disagreement runs one way: the
human said yes, the model said no. For a compliance tool that is the wrong direction of
error -- a missed regulated function ships unannotated -- but it is consistent enough to
correct for rather than merely note.

**Selective prediction does not work here, and that was the plan.** The design expected
unanimous-versus-split to give a confidence signal, so that a model could label the items it
was sure about and a human took the rest. The models are *too* self-consistent for that:
kappa 0.93 to 1.00, leaving one to six split items each. They are confidently wrong on the
hard items rather than uncertain about them, which is the less useful failure mode.

**Cross-model agreement is the least informative figure in the table.** Opus, Sonnet and
Haiku agree with each other at kappa 0.80 to 0.90, but they share a training lineage, so
agreeing is cheap. Only the comparison against the human counts. An independent vendor would
test whether "at the human ceiling" is a property of the task or of one model family, and is
recorded as future work.

## The seven items all three models got wrong

Every one is a human *yes* the models called *no*. Three were flagged by the human as hard
while labelling. Reviewed against the source:

| item | verdict | reasoning |
|---|---|---|
| `_create_PmtInf_node` | **models right** | Creates blank XML elements and writes no values. The human flagged it *and* disagreed with herself on it in the second pass |
| `WeightedModulus.validate` | **models right** | Checksum arithmetic, which the agreed rule calls no |
| `test_treasury_transaction_entries_get` | **models right** | Placeholder id, mocked transport, request-shape assertion |
| `test_pandas_df_camt053_001_02` | **human right** | It materialises a camt.053 **bank statement** into a dataframe and reads entry references from it. The models called it read-only with no state change, over-applying the conduit clause -- holding statement data is the yes clause |
| `DefaultAlgorithm.solve` | **human right, for a reason nobody stated** | It operates on `account_holder_id`, and **Icelandic IBANs embed the holder's national identification number** in the BBAN. The models saw checksum arithmetic; the human saw a national identifier |
| `test_is_valid_with_international_phone_number` | **toss-up** | A real phone number in a shipping-address form is unambiguously personal data, but the function only validates. The rule's weakest clause |
| `strf` | **toss-up** | Constructs an object holding a creditor name and IBAN -- the same act as `_create_header`, a yes -- but with placeholder values |

**Models right 3, human right 1, genuine toss-up 3.**

On four of the seven the human labelled against advice given explicitly during labelling, so
that part is rule-application drift rather than missing domain knowledge. And where her
instinct did diverge from the rule, it was **domain insight that beat the rule twice**: that a
camt.053 is a real statement, and that an Icelandic account-holder id is a national
identifier. Neither point was made by any of the three models.

## Why the labels were not revised

Revising three labels towards the models would raise the measured kappa, and no reader could
then distinguish "corrected a slip" from "moved towards the answer". The labels stay frozen
and this review stands beside them as a separate analysis. A defensible number with a
documented disagreement is worth more than a cleaner number with an undocumented one.

## A defect in the harness, recorded

The first run set `max_tokens` to 300 and lost **96 of 1,440 judgements to truncation** --
valid JSON cut off before its closing brace, which the parser then rejected. The loss was not
random: longer explanations mean harder items, so the dropped judgements were biased towards
exactly the cases that discriminate. Recovering 30 of them from the stored error text moved
Haiku's kappa *down* from 0.756 to 0.743, confirming the lost data had flattered it.

The parser now reads the verdict even from a truncated reply, because the rubric asks for it
first, and `max_tokens` is 700. A second run then failed 248 calls on an exhausted API credit
balance -- an HTTP 400, not a code fault. The figures above are from the third, clean run.
