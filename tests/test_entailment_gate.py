"""Tests for the entailment audit's verifier.

The audit lets a model judge the registry's regulatory claims. That is only
acceptable because the model is not trusted: the rubric demands a span copied
character-for-character from the provision, and the caller discards any verdict
whose span is not an exact substring.

So the substring test *is* the safety property. These tests are about the one
function that enforces it, plus the reply parsing that feeds it. Neither
touches the network.
"""

from __future__ import annotations

import pytest

from tools.check_entailment import _parse
from tools.entailment import render_claim, verify_span

# A real pinned provision, with the typography legal texts actually carry:
# a non-breaking space before the currency and a right single quote.
PROVISION = (
    "Article 16 1. Payment service providers shall be allowed not to apply strong "
    "customer authentication, where the payer initiates a remote electronic payment "
    "transaction not exceeding EUR 30, provided that the payment service user’s "
    "previous transactions do not exceed EUR 100."
)


class TestTheSpanMustBeVerbatim:
    def test_an_exact_span_passes(self) -> None:
        ok, why = verify_span("not exceeding EUR 30", PROVISION)
        assert ok and why == ""

    def test_the_whole_provision_passes(self) -> None:
        assert verify_span(PROVISION, PROVISION)[0]

    def test_a_paraphrase_is_rejected(self) -> None:
        """The failure this gate exists for: a plausible, fluent, invented quote."""
        ok, why = verify_span("shall not exceed EUR 30 per transaction", PROVISION)
        assert not ok
        assert why == "span does not occur in the provision text"

    def test_a_reworded_numeral_is_rejected(self) -> None:
        ok, _ = verify_span("not exceeding 30 EUR", PROVISION)
        assert not ok

    def test_collapsed_whitespace_is_rejected_and_named(self) -> None:
        """A model that tidies the text is not quoting it. Reporting *why*
        matters: this failure looks like a correct answer in a diff."""
        ok, why = verify_span("not  exceeding   EUR 30", PROVISION)
        assert not ok
        assert "collapsing whitespace" in why

    def test_a_substituted_quote_character_is_rejected(self) -> None:
        """A straight apostrophe for the text's typographic one. The numeral and
        every word are right, and it is still not the text."""
        ok, _ = verify_span("payment service user's previous transactions", PROVISION)
        assert not ok

    def test_an_empty_span_is_rejected(self) -> None:
        assert verify_span("", PROVISION) == (False, "no span supplied")
        assert verify_span("   \n ", PROVISION) == (False, "no span supplied")

    def test_surrounding_whitespace_is_reported_separately(self) -> None:
        """Distinguished from a wrong quote because the remedy differs: this one
        is a transport artefact, the other is fabricated evidence."""
        ok, why = verify_span("\n  not exceeding EUR 30  \n", PROVISION)
        assert not ok
        assert "stripping surrounding whitespace" in why


class TestReplyParsing:
    def test_a_bare_object_parses(self) -> None:
        got = _parse('{"verdict": "CONFIRMED", "span": "x", "reason": "y"}')
        assert got["verdict"] == "CONFIRMED"

    def test_a_fenced_object_parses(self) -> None:
        got = _parse('```json\n{"verdict": "CLAIM_UNSUPPORTED", "span": "", "reason": "z"}\n```')
        assert got["verdict"] == "CLAIM_UNSUPPORTED"

    def test_prose_around_the_object_parses(self) -> None:
        got = _parse('Here is my answer:\n{"verdict": "CONFIRMED", "span": "a", "reason": "b"}\nDone.')
        assert got["span"] == "a"

    def test_a_reply_with_no_object_raises(self) -> None:
        """Better a loud failure recorded as MALFORMED than a silent default,
        which would count as a confirmation nobody made."""
        with pytest.raises(ValueError, match="no JSON object"):
            _parse("CONFIRMED, the provision says so.")


class TestClaimRendering:
    def test_application_dates_reach_the_judge(self) -> None:
        """They were absent from the original renderer, so the two date claims
        were priced but never judged. A wrong application date is the same
        class of error as a wrong threshold."""
        from finagent_safeguard.regulation.registry import REGISTRY

        dates = list(REGISTRY.application_dates())
        assert dates, "no application dates to check"
        rendered = [render_claim(d.locus) for d in dates]
        assert all("Asserted application date:" in r for r in rendered)
        assert all("Asserted verbatim form:" in r for r in rendered)

    def test_every_claim_renders_something_beyond_the_citation(self) -> None:
        """A claim rendering to its provision id alone would be judged against
        no assertion at all, and would confirm vacuously."""
        from tools.entailment import cases

        thin = [c.provision_id for c in cases() if len(c.claim.splitlines()) < 2]
        assert not thin, f"claims with nothing to judge: {thin}"
