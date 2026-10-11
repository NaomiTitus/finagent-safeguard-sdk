# Labelling decisions

**Status:** agreed 2026-10-11, during the first labelling pass. Frozen before any
classifier is written.

The corpus is `benchmark/pointers.jsonl` (160 functions, pointer-only) and the labels are
the **evaluation** set for the binary classifier. One question is asked of each function:

> **Does this function handle money movement or personal data?**

Not the six-way category question. Trained annotators reach a Krippendorff's alpha of 0.251
on that one, so it has no reliable answer to measure a classifier against; binary has a
usable ceiling.

These decisions exist because the question turned out to have three systematic ambiguities,
each covering ten or more items. Recorded here rather than left implicit in the data, where
neither of us could reconstruct in a month why item 14 went the way it did. **Consistency
matters more than per-item correctness**: an unevenly applied rule is mathematically
indistinguishable from noise, and noise is the one thing that would make all 160 items
worthless.

## The rule

| | |
|---|---|
| **No** | Computing, formatting, displaying or converting an amount. Format validation and checksum arithmetic over an identifier. Pass-through plumbing that never materialises what it carries. Transport and protocol implementations. |
| **Yes** | Holding, persisting, transmitting or acting on a **specific subject's** money or data. |

Judge the function **as written** -- not the module it sits in, not what it could be used
for. If you cannot tell from the function itself, that is a `no`, and worth noting, because
it is the same limit the linter has.

### Decision 1: tests are judged like anything else

Neither excluded nor automatically regulated. The directory never decides; the data does.

| | Carries | |
|---|---|---|
| `create_pain001_001_008()` | debtor name, IBAN, amount, mandate | **yes** |
| `test_is_creatable()` | `country="US"`, `type="custom"` | **no** |

Two reasons tests are kept rather than skipped. The classifier cannot see a file path, so
asking it to distinguish a test that builds a `pain.001` from production code that builds one
is asking it to infer something it is not shown. And test-versus-production is *mechanically*
decidable from the path -- anything a machine can decide must not be baked into human labels,
where nobody can find it later.

Consequence for the product: the linter currently scans test files, and nothing needs a
compliance decorator on a test. That is an `--exclude` rule the tool should have and does
not. Recorded as a gap, not resolved by the labels.

Because the path is in every pointer, the metric can be reported twice -- over all 160, and
over the 98 non-test items -- and a large gap between them is a finding rather than a problem.

### Decision 2: presence, not shape

A field being a regulated *kind* of data does not make a function that passes a placeholder
into it a handler of that data.

- `test_validate_string_length_constraints` passes `""` and `"a" * 50` into
  `end_to_end_id`. The field is arguably personal data in context -- from the pinned TFR
  text, Art 4(3) makes a unique transaction identifier substitute for the payment account
  number -- but fifty letter *a*s identify nobody. **No.**
- `test_validate_complete_instance` passes `"INSTR123"`, `"E2E123"`, `"TX123"` and the
  textbook example UUID into four TFR-named fields. Well-formed placeholders are still
  placeholders. **No.**
- A synthetic amount does not flip a request-shape assertion. `post_data="amount=2000"` with
  no payer, no payee and no account is the *shape* of payment data. **No.**
- The `pain.008` fixtures carry a named debtor, their IBAN, an amount, and a mandate id and
  date. Everything needed to debit an account is present. **Yes.**

Taking the looser reading would make nearly every function in a payments library a yes, and
the label would stop discriminating -- measuring "is this django-oscar?" rather than "is this
regulated?".

### Decision 3: the conduit and protocol question

This arrived three times in different clothes and is the one that needed a decision.

| Function | How data arrives | Does it decide or hold anything? | |
|---|---|---|---|
| `validate(components, expected)` (schwifty) | parameter | no -- checksum arithmetic | **no** |
| `fetch_related_object_async(self)` (stripe) | `self.related_object` | no -- GET and cast | **no** |
| `SMTP.send_message(msg, from_addr, to_addrs)` | parameter | no -- it is the SMTP protocol | **no** |
| `camt053_to_excel(xml_fname, excel_fname)` | parameter | **yes** -- writes a copy of a bank statement | **yes** |
| `save_payment_details(self, order)` | `order` | **yes** -- persists billing address and payment sources | **yes** |

**A correction made while deciding this.** The first wording of the rule said "receiving,
storing, *transmitting* or deciding with real data is yes". Applied literally that makes
`smtplib.SMTP.send_message` a yes -- it extracts email addresses from headers and transmits
them -- and drags `ftplib`, `http.client` and much of the standard library with it, which
contradicts treating the standard library as the negative class. The rule and the guidance
disagreed, and the owner found it by refusing to label past it.

The sharper line: **implementing a transport protocol is no; transmitting on behalf of a
subject whose data you hold is yes.** `send_message` moves whatever message it is handed and
has no notion of whose it is -- `from_addr` and `to_addrs` are envelope fields. The deployer
calling it is processing personal data; this is the instrument.

### Where the footing is weakest

Deciding whether identifier validation counts as *processing* properly needs GDPR Art 4(2),
the definition of processing. **It is not pinned.** The corpus holds GDPR Art 5, 25, 32, 44
and 87 -- all duties, no definitions. So the reasoning above rests on a reading rather than on
verbatim text, and this project's own rule is not to assert law from memory.

That is the scope-and-definitions gap recorded in `docs/category-decision.md` §3, biting
exactly where it would be expected to. Pinning PSD2 Art 3 and 4 and GDPR Art 4 would put this
on firmer ground and is future work.

## Two findings about the detector, surfaced by labelling

**The ISO 20022 vocabulary gap, at its most consequential.** `camt053_to_excel` reads a
bank-to-customer statement -- account holder, account number, balances, every transaction
with counterparty names -- and writes a copy of it to a spreadsheet. The linter does not flag
it: the parameters are `xml_fname`, `excel_fname`, `translate`, and the function name splits
to `{camt053, to, excel}`. Meanwhile it *does* flag `_validate_min_inclusive` for carrying a
`Decimal` annotation. It finds the validator's helpers and misses the function that exports
someone's bank statement.

**Data arriving through instance state is invisible.** The linter reads function and parameter
names. `fetch_related_object_async(self)` takes its data from `self.related_object`, so a
method could carry a customer's entire record on `self` and the detector would never see it.
Distinct from the vocabulary gap, and previously unidentified.
