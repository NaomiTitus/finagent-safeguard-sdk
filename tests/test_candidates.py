"""Tests for the signal-to-candidate-provision mapping.

A candidate is not a classification. These tests mostly guard that distinction,
because it is the one the whole project turns on: the tool may say "read this",
never "this applies".
"""

from __future__ import annotations

import pytest

from finagent_safeguard.regulation.candidates import (
    CANDIDATES,
    UNREACHABLE,
    candidates_for,
    cite,
)
from finagent_safeguard.regulation.registry import REGISTRY


class TestEveryProvisionIsAccountedFor:
    def test_mapped_or_excused_with_a_reason(self) -> None:
        """Pinning a provision forces a decision about it.

        The registry is deliberately broader than the linter -- it pins duties
        no function signature can evidence. A provision silently absent from
        the mapping is indistinguishable from one nobody thought about, so this
        fails until it appears in CANDIDATES or in UNREACHABLE with a reason.
        """
        pinned = {p.id for p in REGISTRY.provisions()}
        mapped = {c.provision.id for group in CANDIDATES.values() for c in group}
        undecided = sorted(pinned - mapped - set(UNREACHABLE))
        assert not undecided, (
            f"{len(undecided)} pinned provision(s) are neither offered as a "
            f"candidate nor excused: {undecided}"
        )

    def test_nothing_is_cited_that_is_not_pinned(self) -> None:
        """A candidate whose text is not committed cannot be checked, and would
        be exactly the unverified citation this project exists to prevent."""
        pinned = {p.id for p in REGISTRY.provisions()}
        named = {
            c.provision.id for group in CANDIDATES.values() for c in group
        } | set(UNREACHABLE)
        assert not sorted(named - pinned)

    def test_no_provision_is_both_offered_and_excused(self) -> None:
        mapped = {c.provision.id for group in CANDIDATES.values() for c in group}
        assert not (mapped & set(UNREACHABLE))

    def test_every_excuse_says_something(self) -> None:
        """An empty reason is how a provision gets excused by accident."""
        for provision_id, reason in UNREACHABLE.items():
            assert len(reason) > 30, provision_id


class TestWhatGetsOffered:
    def test_money_offers_the_payment_provisions(self) -> None:
        offered = [cite(c.provision) for c in candidates_for(("money",))]
        assert "PSD2 Art 97(1)(b)" in offered
        assert "TFR Art 4(4)" in offered

    def test_pii_offers_the_data_protection_provisions(self) -> None:
        offered = [cite(c.provision) for c in candidates_for(("pii",))]
        assert "GDPR Art 5(1)(c)" in offered

    def test_both_vocabularies_offer_both_sets(self) -> None:
        """The case the old single-category tie-break could not express."""
        offered = [cite(c.provision) for c in candidates_for(("money", "pii"))]
        assert "PSD2 Art 97(1)(b)" in offered
        assert "GDPR Art 5(1)(c)" in offered

    def test_no_vocabulary_offers_nothing(self) -> None:
        """Reachability alone evidences no particular duty, so offering a
        provision for it would be invention."""
        assert candidates_for(()) == ()

    def test_order_follows_the_vocabularies(self) -> None:
        """So the written comment does not churn between runs."""
        first = [cite(c.provision) for c in candidates_for(("money", "pii"))]
        assert first == [cite(c.provision) for c in candidates_for(("money", "pii"))]
        assert first.index("PSD2 Art 97(1)(b)") < first.index("GDPR Art 5(1)(c)")

    def test_an_unknown_vocabulary_is_ignored_not_guessed(self) -> None:
        assert candidates_for(("not_a_vocabulary",)) == ()

    def test_no_duplicates(self) -> None:
        offered = [c.provision.id for c in candidates_for(("money", "pii"))]
        assert len(offered) == len(set(offered))

    def test_a_provision_reachable_from_two_vocabularies_is_offered_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The dedup is unreachable with today's mapping -- no provision sits in
        both groups -- so the test above passes whether or not it works, and a
        mutation removing it survived.

        Rather than baseline it as unprovable, this constructs the case. GDPR
        Art 32 is a plausible future addition to "money": security of
        processing applies to payment data too. When that happens the guard
        must already work, and this is what proves it does.
        """
        from finagent_safeguard.regulation import candidates as module

        shared = module.CANDIDATES["pii"][1]
        monkeypatch.setitem(
            module.CANDIDATES, "money", (*module.CANDIDATES["money"], shared)
        )
        offered = [c.provision.id for c in candidates_for(("money", "pii"))]
        assert offered.count(shared.provision.id) == 1, offered


class TestTheRequirementsAreGroundedInPinnedText:
    @pytest.mark.parametrize(
        "vocabulary", sorted(CANDIDATES),
    )
    def test_every_candidate_states_what_it_requires(self, vocabulary: str) -> None:
        for candidate in CANDIDATES[vocabulary]:
            assert len(candidate.requires) > 20, candidate.provision.id
            assert len(candidate.because) > 20, candidate.provision.id

    def test_requires_and_because_are_different_fields(self) -> None:
        """One is about the law, the other about our inference. Collapsing them
        is how an inference starts reading as a legal fact."""
        for group in CANDIDATES.values():
            for candidate in group:
                assert candidate.requires != candidate.because


class TestCitation:
    def test_a_point_level_provision_reads_as_a_lawyer_would_write_it(self) -> None:
        from finagent_safeguard.regulation.registry import PSD2_ART_97_1_B

        assert cite(PSD2_ART_97_1_B) == "PSD2 Art 97(1)(b)"

    def test_a_paragraph_level_provision_omits_the_point(self) -> None:
        from finagent_safeguard.regulation.registry import PSD2_ART_97_2

        assert cite(PSD2_ART_97_2) == "PSD2 Art 97(2)"

    def test_an_article_level_provision_omits_both(self) -> None:
        from finagent_safeguard.regulation.registry import GDPR_ART_32

        assert cite(GDPR_ART_32) == "GDPR Art 32"
