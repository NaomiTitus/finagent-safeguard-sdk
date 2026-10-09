"""Which pinned provisions a code signal makes worth reading.

A *candidate* is not a classification. The linter finds a function whose
identifiers use money or person vocabulary; this module says which provisions a
developer should read before deciding. The decision stays theirs, and the
difference matters enough to be worth stating twice: nothing here asserts that
a function falls under a provision, only that the provision is relevant enough
to put in front of someone.

Why this module and not a field on ``FinancialCategory``
--------------------------------------------------------
``taxonomy/policies.py`` states that a category "deliberately carries no
threshold, citation or amount", because an enum member asserting a legal fact
would be exactly as unverified as the YAML key that founded this project's
original error. That boundary holds. The mapping here is from a **code signal**
to a **candidate provision** -- not from a category to a law -- so it adds no
legal content to the taxonomy and the taxonomy still imports nothing.

Why the mapping is hand-written
-------------------------------
Because it is short, it is reviewable in a minute, and every production tool
that does this does it the same way. CodeQL's sensitive-data classifier is a
five-word list plus identifier regexes; Bearer is "pattern matching and
heuristics" over 122 data types with machine learning used offline to *author*
rules and never at scan time; Privado is 107 regex data elements with a
``tags/law`` column. A reviewer at a bank can read this file and check it.

Every provision cited here is pinned: its verbatim text is committed under
``corpus/`` with a SHA-256, and ``tools/check_entailment.py`` audits the claims
the registry makes about it against that text. A provision that is not pinned
cannot be cited, which is why ``UNREACHABLE`` below exists rather than being
quietly ignored.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from finagent_safeguard.regulation.registry import (
    AMLR_ART_19_1_B,
    AMLR_ART_19_4,
    AMLR_ART_26_1,
    AMLR_ART_69_1_A,
    AMLR_ART_80,
    AMLR_ART_90,
    DORA_ART_23,
    DORA_ART_28_3,
    DORA_ART_64,
    GDPR_ART_5_1_C,
    GDPR_ART_25,
    GDPR_ART_32,
    GDPR_ART_44,
    GDPR_ART_87,
    PSD2_ART_97_1_B,
    PSD2_ART_97_2,
    RTS_ANNEX,
    RTS_ART_11,
    RTS_ART_13,
    RTS_ART_16,
    RTS_ART_18,
    TFR_ART_4_4,
    TFR_ART_5_2_B,
    Provision,
)

__all__ = ["Candidate", "CANDIDATES", "UNREACHABLE", "candidates_for", "cite"]


@dataclass(frozen=True, slots=True)
class Candidate:
    """A provision worth reading, and why this signal raised it."""

    provision: Provision
    #: What the provision requires, in the fewest words that stay true to it.
    #: Read off the pinned text, not from recollection -- the entailment audit
    #: found two registry claims that drifted from their text, including one
    #: that inverted a derogation into a wider duty.
    requires: str
    #: Why this *signal* makes it a candidate. Separate from ``requires``
    #: because one is about the law and the other is about our inference, and
    #: collapsing them is how an inference starts reading as a legal fact.
    because: str


#: Candidate provisions per token family. Ordered most-specific first, because
#: the first line of a comment block is the one that gets read.
CANDIDATES: Final[dict[str, tuple[Candidate, ...]]] = {
    "money": (
        Candidate(
            provision=PSD2_ART_97_1_B,
            requires=(
                "strong customer authentication where the payer initiates an "
                "electronic payment transaction"
            ),
            because="the identifiers name a payment or an amount being moved",
        ),
        Candidate(
            provision=PSD2_ART_97_2,
            requires=(
                "authentication elements dynamically linking the transaction to a "
                "specific amount and a specific payee"
            ),
            because=(
                "applies in addition to 97(1)(b) for remote electronic payment "
                "transactions, so it is the obligation an amount-and-payee "
                "signature most often attracts"
            ),
        ),
        Candidate(
            provision=TFR_ART_4_4,
            requires=(
                "the payment service provider of the payer verifies the accuracy "
                "of the payer information"
            ),
            because="a transfer of funds carries payer information with it",
        ),
    ),
    "pii": (
        Candidate(
            provision=GDPR_ART_5_1_C,
            requires=(
                "personal data adequate, relevant and limited to what is necessary"
            ),
            because=(
                "the identifiers name a person or a national identifier, so the "
                "question is whether every field taken is needed"
            ),
        ),
        Candidate(
            provision=GDPR_ART_32,
            requires="security of processing appropriate to the risk",
            because="personal data in transit through a tool is processing",
        ),
    ),
}

#: Pinned provisions that no signal currently raises, with the reason.
#:
#: Listed rather than omitted. The registry is deliberately broader than the
#: linter: it pins duties that no function signature can evidence, and a
#: provision silently absent from the mapping is indistinguishable from one
#: nobody thought about. ``test_every_pinned_provision_is_mapped_or_excused``
#: fails when a provision appears in neither this list nor ``CANDIDATES``, so
#: pinning something new forces a decision about it.
UNREACHABLE: Final[dict[str, str]] = {
    GDPR_ART_25.id: (
        "data protection by design is a property of a system's architecture, "
        "not of a function"
    ),
    GDPR_ART_44.id: (
        "a third-country transfer depends on where the endpoint is, which the "
        "current signals do not inspect. A string-literal endpoint signal would "
        "reach it and is future work"
    ),
    GDPR_ART_87.id: (
        "a permission addressed to Member States, not a duty on a controller, "
        "so no function can discharge it"
    ),
    AMLR_ART_19_1_B.id: (
        "the EUR 10 000 occasional-transaction trigger depends on the value of a "
        "transaction at runtime, not on a signature"
    ),
    AMLR_ART_19_4.id: (
        "likewise -- the EUR 3 000 cash derogation turns on the value and the "
        "means of payment"
    ),
    AMLR_ART_26_1.id: (
        "ongoing monitoring is a property of an institution's processes; the "
        "provision states no monetary threshold and no signature evidences it"
    ),
    AMLR_ART_69_1_A.id: (
        "reporting attaches to suspicion, which is a human judgement about a "
        "customer and not a property of code"
    ),
    AMLR_ART_80.id: (
        "the EUR 10 000 cash payment limit binds persons trading in goods, not "
        "the software they use"
    ),
    AMLR_ART_90.id: "an institutional record-keeping duty, not a per-function one",
    DORA_ART_23.id: (
        "extends the incident-reporting chapter to four named entity types; "
        "about who the firm is, not what a function does"
    ),
    DORA_ART_28_3.id: (
        "an ICT third-party contractual duty. A vendor-call signal would reach "
        "it and is future work"
    ),
    DORA_ART_64.id: "an application-date provision, not an obligation on code",
    RTS_ART_11.id: (
        "a contactless point-of-sale derogation. Reachable only once the linter "
        "can tell a channel apart, which it cannot"
    ),
    RTS_ART_13.id: "a recurring-transaction derogation; same reason as Art. 11",
    RTS_ART_16.id: (
        "the low-value remote derogation. It relaxes 97(1)(b) rather than "
        "imposing a duty, so raising it next to a payment signal would invite "
        "exactly the direction error that founded this project"
    ),
    RTS_ART_18.id: "the transaction-risk-analysis derogation; same reason as Art. 16",
    RTS_ANNEX.id: "a table of reference fraud rates, cited by Art. 18",
    TFR_ART_5_2_B.id: (
        "defines a value bracket for a derogation. A bracket is not a duty, and "
        "the figure is commonly misread as a reporting threshold"
    ),
}


def candidates_for(vocabularies: tuple[str, ...]) -> tuple[Candidate, ...]:
    """Candidates for the token families a function matched.

    Order follows ``vocabularies``, which is itself stable, so the comment the
    linter writes does not churn between runs and a diff stays readable.
    Duplicates are impossible today and are collapsed anyway, because a
    provision listed twice reads as emphasis nobody intended.
    """
    out: list[Candidate] = []
    for vocabulary in vocabularies:
        for candidate in CANDIDATES.get(vocabulary, ()):
            if candidate not in out:
                out.append(candidate)
    return tuple(out)


def cite(provision: Provision) -> str:
    """A provision as a developer would look it up: ``PSD2 Art 97(1)(b)``."""
    reference = f"{provision.instrument.short_name} Art {provision.article}"
    if provision.paragraph:
        reference += f"({provision.paragraph})"
    if provision.point:
        reference += f"({provision.point})"
    return reference
