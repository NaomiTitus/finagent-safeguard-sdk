"""RED-phase specification for the regulatory registry.

See docs/test-matrix.md section 1. These are meta-tests over the registry
itself: they are the mechanism that makes the EUR 30 / EUR 500 error class
hard to commit, by refusing to build when a number lacks provenance or a
quotation is not actually a quotation.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import re
from decimal import Decimal
from typing import Any

import pytest

from finagent_safeguard.regulation import registry as reg
from finagent_safeguard.regulation.citation import NOT_YET_DETERMINED, Status
from finagent_safeguard.regulation.registry import REGISTRY, Provenance

BASE_ACT_CELEX = re.compile(r"^3\d{4}[LRD]\d{4}$")
CONSOLIDATED_CELEX = re.compile(r"^0\d{4}[LRD]\d{4}-\d{8}$")


class TestProvenance:
    def test_every_numeric_parameter_declares_provenance(self) -> None:
        params = list(REGISTRY.numeric_parameters())
        assert params, "registry declares no numbers; nothing is being verified"
        for param in params:
            assert isinstance(param.provenance, Provenance), param.name

    def test_regulatory_verbatim_requires_locus(self) -> None:
        for param in REGISTRY.numeric_parameters():
            if param.provenance is Provenance.REGULATORY_VERBATIM:
                assert param.locus is not None, param.name
                assert param.locus.article, param.name

    def test_regulatory_verbatim_number_appears_in_pinned_span(
        self, corpus: dict[str, Any]
    ) -> None:
        """A number claimed as verbatim must occur in the text it cites.

        This is what would have stopped the invented 'EUR 500 AML reporting
        threshold': there is nowhere in Art. 69 for it to appear.
        """
        checked = 0
        for param in REGISTRY.numeric_parameters():
            if param.provenance is not Provenance.REGULATORY_VERBATIM:
                continue
            assert param.locus is not None
            span = corpus[param.locus.corpus_key]
            assert param.as_cited in span.text, (
                f"{param.name}: {param.as_cited!r} absent from {param.locus.id}"
            )
            checked += 1
        assert checked >= 5, "too few verbatim numbers to be a meaningful check"

    def test_operator_configured_requires_rationale(self) -> None:
        for param in REGISTRY.numeric_parameters():
            if param.provenance is not Provenance.REGULATORY_VERBATIM:
                assert len(param.rationale.strip()) >= 40, param.name

    def test_constructing_verbatim_number_without_locus_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="locus"):
            reg.NumericParameter(
                name="bogus",
                value=Decimal("500"),
                currency="EUR",
                provenance=Provenance.REGULATORY_VERBATIM,
                locus=None,
            )

    def test_constructing_configured_number_without_rationale_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="rationale"):
            reg.NumericParameter(
                name="bogus",
                value=Decimal("30"),
                currency="EUR",
                provenance=Provenance.OPERATOR_CONFIGURED,
                rationale="because",
            )


class TestQuotation:
    def test_obligation_text_is_a_substring_of_its_pinned_span(
        self, corpus: dict[str, Any]
    ) -> None:
        """Quote, do not paraphrase -- enforced mechanically.

        PLAN.md section 3: a test whose docstring paraphrases is not done.
        The same rule applies to the registry's own obligation_text.
        """
        checked = 0
        for provision in REGISTRY.provisions():
            if not provision.obligation_text:
                continue
            span = corpus[provision.corpus_key]
            assert provision.obligation_text in span.text, provision.id
            checked += 1
        assert checked == 14, f"quotation count moved to {checked}; update deliberately"

    def test_every_provision_resolves_to_a_pinned_corpus_entry(
        self, corpus: dict[str, Any]
    ) -> None:
        checked = 0
        for provision in REGISTRY.provisions():
            assert provision.corpus_key in corpus, provision.id
            checked += 1
        assert checked == 23, f"provision count moved to {checked}; update deliberately"


class TestCitationForm:
    def test_base_act_celex_format(self) -> None:
        for instrument in REGISTRY.instruments():
            if instrument.celex is None:
                continue
            assert BASE_ACT_CELEX.match(instrument.celex), instrument.short_name

    def test_consolidated_celex_format(self) -> None:
        for instrument in REGISTRY.instruments():
            if instrument.consolidated_celex is None:
                continue
            assert CONSOLIDATED_CELEX.match(instrument.consolidated_celex), (
                instrument.short_name
            )

    def test_only_pending_instruments_may_lack_a_celex(self) -> None:
        """PSD3/PSR has no CELEX because it is not in the Official Journal."""
        for instrument in REGISTRY.instruments():
            if instrument.celex is None:
                assert instrument.status is Status.PENDING, instrument.short_name


class TestApplicability:
    def test_no_enforcing_obligation_cites_a_pending_instrument(self) -> None:
        for obligation in REGISTRY.obligations():
            if obligation.enforcing:
                assert obligation.provision.instrument.status is Status.IN_FORCE, (
                    obligation.provision.id
                )

    def test_no_enforcing_obligation_cites_a_not_yet_applicable_instrument(
        self, frozen_today: _dt.date
    ) -> None:
        """AMLR applies from 10 July 2027. Today's duty runs through AMLD4/5."""
        for obligation in REGISTRY.obligations():
            if obligation.enforcing:
                assert obligation.provision.instrument.applies_from <= frozen_today, (
                    obligation.provision.id
                )

    def test_future_applies_from_requires_predecessor(self, frozen_today: _dt.date) -> None:
        for instrument in REGISTRY.instruments():
            if instrument.applies_from > frozen_today:
                assert instrument.predecessor_in_force, instrument.short_name

    def test_every_instrument_has_review_by(self) -> None:
        for instrument in REGISTRY.instruments():
            assert isinstance(instrument.review_by, _dt.date), instrument.short_name

    def test_no_instrument_review_is_overdue(self) -> None:
        """The drift alarm. Goes red with no code change; runs nightly in CI."""
        overdue = [
            i.short_name for i in REGISTRY.instruments() if i.review_by < _dt.date.today()
        ]
        assert not overdue, f"regulatory review overdue: {overdue}"


class TestObligationShape:
    def test_risk_based_obligation_has_no_amount_field(self) -> None:
        """AMLR Arts. 26(1) and 69 impose no threshold; the type cannot hold one."""
        names = {f.name for f in dataclasses.fields(reg.RiskBasedObligation)}
        types = {str(f.type) for f in dataclasses.fields(reg.RiskBasedObligation)}
        assert not (names & {"amount", "threshold", "value", "limit"})
        assert not any("Decimal" in t for t in types)

    def test_every_obligation_declares_addressee(self) -> None:
        for obligation in REGISTRY.obligations():
            assert isinstance(obligation.addressee, reg.Addressee), obligation.provision.id

    def test_sdk_role_is_contributes_only(self) -> None:
        """The SDK is not the addressee of any obligation and never claims to be."""
        for obligation in REGISTRY.obligations():
            assert obligation.sdk_role is reg.SdkRole.CONTRIBUTES_ONLY

    def test_exemption_requiring_institutional_fact_demands_assertion(self) -> None:
        """RTS Art. 18 needs a PSP fraud rate the SDK cannot self-certify."""
        tra = REGISTRY.exemption("rts.art18.transaction_risk_analysis")
        assert tra.deployer_assertion_required
        assert "fraud" in tra.deployer_assertion_required

    def test_every_exemption_only_relaxes(self) -> None:
        """An exemption can never create a duty. Structural guard on polarity."""
        for exemption in REGISTRY.exemptions():
            assert exemption.effect == "relaxes", exemption.exemption_id


class TestReferencePoints:
    def test_reference_point_requires_governs(self) -> None:
        for point in REGISTRY.reference_points():
            assert point.governs.strip(), point.parameter.name

    def test_the_two_amlr_ten_thousands_are_distinct_entries(self) -> None:
        """Art. 19(1)(b) is a CDD trigger; Art. 80 is a payment prohibition."""
        tens = [p for p in REGISTRY.reference_points() if p.parameter.value == Decimal("10000")]
        assert len(tens) == 2, "expected exactly two EUR 10 000 reference points"
        first, second = tens
        assert first.parameter.locus is not None and second.parameter.locus is not None
        assert first.parameter.locus.article != second.parameter.locus.article
        assert first.governs != second.governs

    def test_no_reference_point_is_enforced(self) -> None:
        """Reference points are cited, never operative. This is where a real
        figure goes so that nobody has to invent one."""
        for point in REGISTRY.reference_points():
            assert point.operative is False, point.parameter.name


class TestNoOrphans:
    """Both directions. A pinned span nothing cites, or a declared provision nothing
    reaches, is dead weight that looks like coverage."""

    def test_no_pinned_span_is_uncited(self, corpus: dict[str, Any]) -> None:
        cited = {p.corpus_key for p in REGISTRY.provisions()}
        uncited = sorted(set(corpus) - cited)
        assert not uncited, f"pinned but cited by nothing: {uncited}"

    def test_every_declared_provision_is_reachable(self) -> None:
        from finagent_safeguard.regulation.citation import Provision

        declared = {
            f"{v.corpus_key}|{v.id}"
            for v in vars(reg).values()
            if isinstance(v, Provision)
        }
        reachable = {f"{p.corpus_key}|{p.id}" for p in REGISTRY.provisions()}
        orphans = sorted(declared - reachable)
        assert not orphans, f"declared but unreachable from the registry: {orphans}"


class TestApplicationDateProvenance:
    """Every number in this registry names its source. Dates were exempt, and dates are
    the class of fact that was wrong in the AI Act case."""

    def test_declared_application_dates_appear_in_their_pinned_span(
        self, corpus: dict[str, Any]
    ) -> None:
        assert REGISTRY.application_dates(), "no application date carries provenance"
        for entry in REGISTRY.application_dates():
            span = corpus[entry.locus.corpus_key]
            assert entry.as_cited in span.text, (
                f"{entry.instrument.short_name}: {entry.as_cited!r} absent from "
                f"{entry.locus.id}"
            )

    def test_declared_date_matches_the_instrument(self) -> None:
        for entry in REGISTRY.application_dates():
            assert entry.date == entry.instrument.applies_from, entry.instrument.short_name

    def test_instruments_without_date_provenance_are_the_documented_set(self) -> None:
        """A two-way lock on the date-provenance gap, not a one-way ratchet.

        Adding an instrument without date provenance fails the build; so does adding
        provenance for one of these four. Both directions require a deliberate edit
        here, which is the point.

        Filtered on whether an application date exists to cite at all, not on status:
        a status-shaped filter would let a non-IN_FORCE instrument join the gap
        silently, which is exactly what this test exists to prevent.
        """
        covered = {e.instrument.short_name for e in REGISTRY.application_dates()}
        missing = {
            i.short_name
            for i in REGISTRY.instruments()
            if i.short_name not in covered and i.applies_from != NOT_YET_DETERMINED
        }
        assert missing == {"PSD2", "RTS on SCA", "GDPR", "TFR"}, (
            f"date-provenance gap changed: {sorted(missing)}"
        )


class TestStagedApplicationDates:
    """An article that states two application dates must have both declared.

    AMLR Art. 90 applies from 10 July 2027 "except in relation to obliged entities
    referred to in Article 3, points (3)(n) and (o), to which it shall apply from
    10 July 2029". A single date field answers two years early for that class.
    """

    def test_every_date_in_the_span_is_accounted_for(self, corpus: dict[str, Any]) -> None:
        for entry in REGISTRY.application_dates():
            span = corpus[entry.locus.corpus_key]
            in_text = {m.group(0) for m in reg.OJ_DATE_RE.finditer(span.text)}
            declared = {v for _d, v in entry.all_dates}
            undeclared = sorted(in_text - declared)
            assert not undeclared, (
                f"{entry.instrument.short_name} {entry.locus.id} states "
                f"{sorted(in_text)} but the registry declares only {sorted(declared)}. "
                "An undeclared date is a staged application the registry is flattening."
            )

    def test_carve_out_dates_appear_in_the_span(self, corpus: dict[str, Any]) -> None:
        for entry in REGISTRY.application_dates():
            span = corpus[entry.locus.corpus_key]
            for carve in entry.carve_outs:
                assert carve.verbatim_form in span.text, carve.verbatim_form
                assert carve.applies_to in span.text, (
                    f"the class {carve.applies_to!r} is not worded that way in "
                    f"{entry.locus.id}"
                )

    def test_amlr_declares_its_staged_class(self) -> None:
        amlr = next(
            e for e in REGISTRY.application_dates() if e.instrument.short_name == "AMLR"
        )
        assert amlr.carve_outs, "AMLR Art. 90 stages its application; declare the carve-out"
        assert amlr.carve_outs[0].date.year == 2029

    def test_locus_must_belong_to_the_instrument(self) -> None:
        import datetime as _dt

        with pytest.raises(ValueError, match="must be traceable"):
            reg.ApplicationDate(
                instrument=reg.AMLR,
                date=_dt.date(2025, 1, 17),
                locus=reg.DORA_ART_64,
                verbatim_form="17 January 2025",
            )
