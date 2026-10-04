"""The regulatory registry: obligations, exemptions and cited reference points.

Three rules carry this module:

1. Every number declares where it came from. ``regulatory_verbatim`` demands a
   locus, and a test asserts the numeral actually occurs in the pinned span.
   ``operator_configured`` demands a written rationale.
2. An exemption can only ever *relax* a duty. There is no field in which an
   exemption could create one, so "PSD2 requires SCA above EUR 30" is not
   expressible here.
3. A risk-based obligation has no amount field at all. The AMLR monitoring and
   reporting duties impose no threshold, and the type cannot hold a fabricated
   one -- which is what the invented "EUR 500 AML reporting threshold" needed.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from finagent_safeguard.regulation.citation import (
    NOT_YET_DETERMINED,
    EeaStatus,
    Instrument,
    Provision,
    Status,
)

__all__ = [
    "REGISTRY",
    "Addressee",
    "ApplicationDate",
    "Exemption",
    "NumericParameter",
    "Obligation",
    "Provenance",
    "ReferencePoint",
    "Registry",
    "RiskBasedObligation",
    "SdkRole",
    "eu_numeral",
]


class Provenance(StrEnum):
    """Where a number came from. The field that makes invention hard."""

    REGULATORY_VERBATIM = "regulatory_verbatim"
    OPERATOR_CONFIGURED = "operator_configured"
    VENDOR_DEFAULT = "vendor_default"


class Addressee(StrEnum):
    """Who the obligation binds. Never the SDK."""

    PAYMENT_SERVICE_PROVIDER = "payment_service_provider"
    OBLIGED_ENTITY = "obliged_entity"
    CONTROLLER = "controller"
    DEPLOYER = "deployer"
    FINANCIAL_ENTITY = "financial_entity"


class SdkRole(StrEnum):
    CONTRIBUTES_ONLY = "contributes_only"


def eu_numeral(value: Decimal, currency: str | None) -> str:
    """Render a number the way the Official Journal does: ``EUR 10 000``."""
    if value == value.to_integral_value():
        rendered = f"{int(value):,}".replace(",", " ").replace(" ", " ")
    else:
        rendered = str(value.normalize()).replace(".", ",")
    return f"{currency} {rendered}" if currency else rendered


@dataclass(frozen=True, slots=True, kw_only=True)
class NumericParameter:
    """A number, with its provenance. Construction fails without it."""

    name: str
    value: Decimal
    currency: str | None = None
    provenance: Provenance
    locus: Provision | None = None
    rationale: str = ""
    #: The numeral exactly as the Official Journal writes it. EU texts spell
    #: small cardinals as words -- RTS Art. 16(c) reads "five consecutive",
    #: not "5" -- so a rendered numeral is not always a quotation. Leaving
    #: this blank falls back to EU numeral formatting; setting it is what
    #: forces the author to look at the actual wording.
    verbatim_form: str = ""

    def __post_init__(self) -> None:
        if self.provenance is Provenance.REGULATORY_VERBATIM:
            if self.locus is None:
                raise ValueError(
                    f"{self.name}: provenance=regulatory_verbatim requires a locus. "
                    "Naming the article is the step that forces you to read it."
                )
        elif len(self.rationale.strip()) < 40:
            raise ValueError(
                f"{self.name}: provenance={self.provenance} requires a written "
                "rationale of at least 40 characters"
            )

    @property
    def as_cited(self) -> str:
        """The string that must appear in the pinned span."""
        return self.verbatim_form or eu_numeral(self.value, self.currency)


@dataclass(frozen=True, slots=True, kw_only=True)
class Obligation:
    """A duty, addressed to a legal person the SDK is not."""

    provision: Provision
    addressee: Addressee
    enforcing: bool
    sdk_role: SdkRole = SdkRole.CONTRIBUTES_ONLY
    interpretation_boundary: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class RiskBasedObligation(Obligation):
    """An obligation with no threshold, by legislative design.

    Deliberately declares no numeric field. See AMLR Arts. 26(1) and 69(1)(a):
    monitoring is risk-based and reporting attaches to suspicion "regardless of
    the amount involved".
    """

    rationale: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Exemption:
    """A permission not to apply a duty. It can only ever relax."""

    exemption_id: str
    provision: Provision
    limbs: tuple[NumericParameter, ...] = field(default_factory=tuple)
    deployer_assertion_required: str = ""
    #: Literal, not str: the type system forbids an exemption that creates a duty.
    effect: Literal["relaxes"] = "relaxes"


@dataclass(frozen=True, slots=True, kw_only=True)
class ApplicationDate:
    """When an instrument applies, with a citation to the article that says so.

    Every number in this registry names its source; dates were the one class of fact
    exempt from that discipline, living in prose ``note`` fields. Dates are also exactly
    what was wrong in the AI Act case, where a published deadline moved.
    """

    instrument: Instrument
    date: _dt.date
    locus: Provision
    #: The date as the Official Journal writes it, e.g. "10 July 2027".
    verbatim_form: str

    @property
    def as_cited(self) -> str:
        return self.verbatim_form


@dataclass(frozen=True, slots=True, kw_only=True)
class ReferencePoint:
    """A real figure, cited but never enforced.

    This is where a correct number goes so that nobody has to invent one, and
    it is what the AML judge is shown instead of bare scalars.
    """

    parameter: NumericParameter
    governs: str
    applies_from: _dt.date | None = None
    operative: Literal[False] = False


# --------------------------------------------------------------------------
# Instruments. Dates and statuses are the verified set in PLAN.md 4.1.
# --------------------------------------------------------------------------

PSD2 = Instrument(
    short_name="PSD2",
    title="Directive (EU) 2015/2366 on payment services in the internal market",
    celex="32015L2366",
    consolidated_celex="02015L2366-20250117",
    status=Status.IN_FORCE,
    applies_from=_dt.date(2018, 1, 13),
    review_by=_dt.date(2027, 1, 31),
    eea_status=EeaStatus.INCORPORATED,
)

RTS_SCA = Instrument(
    short_name="RTS on SCA",
    title=(
        "Commission Delegated Regulation (EU) 2018/389 supplementing PSD2 as regards "
        "strong customer authentication and common and secure open standards of communication"
    ),
    celex="32018R0389",
    consolidated_celex="02018R0389-20230725",
    status=Status.IN_FORCE,
    applies_from=_dt.date(2019, 9, 14),
    review_by=_dt.date(2027, 1, 31),
    eea_status=EeaStatus.INCORPORATED,
)

GDPR = Instrument(
    short_name="GDPR",
    title="Regulation (EU) 2016/679 General Data Protection Regulation",
    celex="32016R0679",
    consolidated_celex="02016R0679-20160504",
    status=Status.IN_FORCE,
    applies_from=_dt.date(2018, 5, 25),
    review_by=_dt.date(2027, 6, 30),
    eea_status=EeaStatus.INCORPORATED,
)

AMLR = Instrument(
    short_name="AMLR",
    title="Regulation (EU) 2024/1624 on the prevention of the use of the financial system "
    "for money laundering or terrorist financing",
    celex="32024R1624",
    status=Status.IN_FORCE,
    applies_from=_dt.date(2027, 7, 10),
    review_by=_dt.date(2027, 7, 10),
    predecessor_in_force=(
        "Until 10 July 2027 the operative duty runs through Directive (EU) 2015/849 "
        "as transposed -- in Norway, hvitvaskingsloven."
    ),
    note="Art. 90: applies from 10 July 2027, and from 10 July 2029 for the obliged "
    "entities in Art. 3(3)(n) and (o).",
)

TFR = Instrument(
    short_name="TFR",
    title="Regulation (EU) 2023/1113 on information accompanying transfers of funds "
    "and certain crypto-assets",
    celex="32023R1113",
    status=Status.IN_FORCE,
    applies_from=_dt.date(2024, 12, 30),
    review_by=_dt.date(2027, 6, 30),
    eea_status=EeaStatus.INCORPORATED,
)

DORA = Instrument(
    short_name="DORA",
    title="Regulation (EU) 2022/2554 on digital operational resilience for the financial sector",
    celex="32022R2554",
    status=Status.IN_FORCE,
    applies_from=_dt.date(2025, 1, 17),
    review_by=_dt.date(2027, 1, 17),
    eea_status=EeaStatus.INCORPORATED,
    note="Incorporated into the EEA Agreement 20 February 2025; Norwegian DORA-loven "
    "and -forskriften in force 1 July 2025 -- a ~5.5-month window in which DORA "
    "applied in the EU but not in Norway.",
)

PSR_DRAFT = Instrument(
    short_name="PSR (draft)",
    title="Proposed Payment Services Regulation -- CELEX to be pinned on OJ publication",
    celex=None,
    status=Status.PENDING,
    applies_from=NOT_YET_DETERMINED,
    review_by=_dt.date(2026, 12, 31),
    predecessor_in_force="PSD2 and the RTS on SCA remain the operative law.",
    note="Politically agreed 27 November 2025; final compromise texts 23 April 2026; "
    "ECON approved 5 May 2026. Not in the Official Journal. No application date is "
    "published here, deliberately.",
)

INSTRUMENTS: tuple[Instrument, ...] = (PSD2, RTS_SCA, GDPR, AMLR, TFR, DORA, PSR_DRAFT)

# --------------------------------------------------------------------------
# Provisions. Every obligation_text below is a verbatim substring of the
# pinned span; tests/test_registry_integrity.py enforces that mechanically.
# --------------------------------------------------------------------------

PSD2_ART_97_1_B = Provision(
    instrument=PSD2,
    article="97",
    paragraph="1",
    point="b",
    subdivision_id="art_97",
    obligation_text=(
        "Member States shall ensure that a payment service provider applies strong "
        "customer authentication where the payer"
    ),
)

PSD2_ART_97_2 = Provision(
    instrument=PSD2,
    article="97",
    paragraph="2",
    subdivision_id="art_97",
    obligation_text=(
        "for electronic remote payment transactions, payment service providers apply "
        "strong customer authentication that includes elements which dynamically"
    ),
)

RTS_ART_11 = Provision(
    instrument=RTS_SCA,
    article="11",
    subdivision_id="art_11",
    obligation_text=(
        "the individual amount of the contactless electronic payment transaction does "
        "not exceed EUR 50"
    ),
)

RTS_ART_13 = Provision(
    instrument=RTS_SCA,
    article="13",
    subdivision_id="art_13",
    obligation_text=(
        "Payment service providers shall be allowed not to apply strong customer "
        "authentication, subject to compliance with the general authentication "
        "requirements, where the payer initiates a payment transaction and the payee "
        "is included in a list of trusted beneficiaries previously created by the payer."
    ),
    tags=("no_amount_cap", "no_count_cap"),
)

RTS_ART_16 = Provision(
    instrument=RTS_SCA,
    article="16",
    subdivision_id="art_16",
    obligation_text=(
        "Payment service providers shall be allowed not to apply strong customer "
        "authentication, where the payer initiates a remote electronic payment "
        "transaction provided that the following conditions are met"
    ),
)

RTS_ART_18 = Provision(
    instrument=RTS_SCA,
    article="18",
    paragraph="1",
    subdivision_id="art_18",
    obligation_text=(
        "Payment service providers shall be allowed not to apply strong customer "
        "authentication where the payer initiates a remote electronic payment "
        "transaction identified by the payment service provider as posing a low level of risk"
    ),
)

RTS_ANNEX = Provision(
    instrument=RTS_SCA,
    article="Annex",
    subdivision_id="anx_1",
    obligation_text="Reference fraud rate (%) for:",
)

GDPR_ART_5_1_C = Provision(
    instrument=GDPR, article="5", paragraph="1", point="c", subdivision_id="art_5"
)
GDPR_ART_25 = Provision(instrument=GDPR, article="25", subdivision_id="art_25")
GDPR_ART_32 = Provision(instrument=GDPR, article="32", subdivision_id="art_32")
GDPR_ART_44 = Provision(instrument=GDPR, article="44", subdivision_id="art_44")
GDPR_ART_87 = Provision(
    instrument=GDPR,
    article="87",
    subdivision_id="art_87",
    obligation_text=(
        "Member States may further determine the specific conditions for the processing "
        "of a national identification number or any other identifier of general application."
    ),
)

AMLR_ART_19_1_B = Provision(
    instrument=AMLR,
    article="19",
    paragraph="1",
    point="b",
    subdivision_id="art_19",
    obligation_text=(
        "when carrying out an occasional transaction of a value of at least EUR 10 000"
    ),
)
AMLR_ART_19_4 = Provision(instrument=AMLR, article="19", paragraph="4", subdivision_id="art_19")
AMLR_ART_26_1 = Provision(
    instrument=AMLR,
    article="26",
    paragraph="1",
    subdivision_id="art_26",
    obligation_text=(
        "Obliged entities shall conduct ongoing monitoring of business relationships, "
        "including transactions undertaken by the customer throughout the course of a "
        "business relationship"
    ),
)
AMLR_ART_69_1_A = Provision(
    instrument=AMLR,
    article="69",
    paragraph="1",
    point="a",
    subdivision_id="art_69",
    obligation_text=(
        "where the obliged entity knows, suspects or has reasonable grounds to suspect "
        "that funds or activities, regardless of the amount involved, are the proceeds "
        "of criminal activity"
    ),
)
AMLR_ART_80 = Provision(
    instrument=AMLR,
    article="80",
    paragraph="1",
    subdivision_id="art_80",
    obligation_text=(
        "Persons trading in goods or providing services may accept or make a payment in "
        "cash only up to an amount of EUR 10 000"
    ),
)

TFR_ART_4_4 = Provision(
    instrument=TFR,
    article="4",
    paragraph="4",
    subdivision_id="art_4",
    obligation_text=(
        "the payment service provider of the payer shall verify the accuracy of the "
        "information referred to in paragraph 1"
    ),
)
TFR_ART_5_3 = Provision(
    instrument=TFR,
    article="5",
    paragraph="3",
    subdivision_id="art_5",
    tags=("derogation_from_verification", "not_a_reporting_threshold"),
)

AMLR_ART_90 = Provision(instrument=AMLR, article="90", subdivision_id="art_90")
DORA_ART_64 = Provision(instrument=DORA, article="64", subdivision_id="art_64")

DORA_ART_23 = Provision(instrument=DORA, article="23", subdivision_id="art_23")
DORA_ART_28_3 = Provision(
    instrument=DORA,
    article="28",
    paragraph="3",
    subdivision_id="art_28",
    obligation_text=(
        "financial entities shall maintain and update at entity level, and at "
        "sub-consolidated and consolidated levels, a register of information in relation "
        "to all contractual arrangements on the use of ICT services provided by ICT "
        "third-party service providers"
    ),
)

# --------------------------------------------------------------------------
# Exemptions. Note the absence of any field able to create a duty.
# --------------------------------------------------------------------------

LOW_VALUE_REMOTE = Exemption(
    exemption_id="rts.art16.low_value_remote",
    provision=RTS_ART_16,
    limbs=(
        NumericParameter(
            name="art16.a.per_transaction_max",
            value=Decimal("30"),
            currency="EUR",
            provenance=Provenance.REGULATORY_VERBATIM,
            locus=RTS_ART_16,
        ),
        NumericParameter(
            name="art16.b.cumulative_max",
            value=Decimal("100"),
            currency="EUR",
            provenance=Provenance.REGULATORY_VERBATIM,
            locus=RTS_ART_16,
        ),
        NumericParameter(
            name="art16.c.consecutive_max",
            value=Decimal("5"),
            provenance=Provenance.REGULATORY_VERBATIM,
            locus=RTS_ART_16,
            verbatim_form="five consecutive individual remote electronic payment transactions",
        ),
    ),
)

CONTACTLESS_POS = Exemption(
    exemption_id="rts.art11.contactless_pos",
    provision=RTS_ART_11,
    limbs=(
        NumericParameter(
            name="art11.a.per_transaction_max",
            value=Decimal("50"),
            currency="EUR",
            provenance=Provenance.REGULATORY_VERBATIM,
            locus=RTS_ART_11,
        ),
        NumericParameter(
            name="art11.b.cumulative_max",
            value=Decimal("150"),
            currency="EUR",
            provenance=Provenance.REGULATORY_VERBATIM,
            locus=RTS_ART_11,
        ),
    ),
)

TRUSTED_BENEFICIARY = Exemption(
    exemption_id="rts.art13.trusted_beneficiary",
    provision=RTS_ART_13,
    limbs=(),  # Deliberately empty: Art. 13 sets no amount or count cap.
)

TRANSACTION_RISK_ANALYSIS = Exemption(
    exemption_id="rts.art18.transaction_risk_analysis",
    provision=RTS_ART_18,
    deployer_assertion_required="psp_fraud_rate_at_or_below_annex_reference_rate",
    limbs=(
        NumericParameter(
            name="annex.etv.500",
            value=Decimal("500"),
            currency="EUR",
            provenance=Provenance.REGULATORY_VERBATIM,
            locus=RTS_ANNEX,
        ),
        NumericParameter(
            name="annex.etv.250",
            value=Decimal("250"),
            currency="EUR",
            provenance=Provenance.REGULATORY_VERBATIM,
            locus=RTS_ANNEX,
        ),
        NumericParameter(
            name="annex.etv.100",
            value=Decimal("100"),
            currency="EUR",
            provenance=Provenance.REGULATORY_VERBATIM,
            locus=RTS_ANNEX,
        ),
    ),
)

EXEMPTIONS: tuple[Exemption, ...] = (
    LOW_VALUE_REMOTE,
    CONTACTLESS_POS,
    TRUSTED_BENEFICIARY,
    TRANSACTION_RISK_ANALYSIS,
)

# --------------------------------------------------------------------------
# Obligations.
# --------------------------------------------------------------------------

OBLIGATIONS: tuple[Obligation, ...] = (
    Obligation(
        provision=PSD2_ART_97_1_B,
        addressee=Addressee.PAYMENT_SERVICE_PROVIDER,
        enforcing=True,
        interpretation_boundary=(
            "The SDK cannot know whether SCA occurred. It knows only whether the agent "
            "presented evidence that it occurred, and whether it is acting autonomously "
            "where no exemption could apply."
        ),
    ),
    Obligation(
        provision=PSD2_ART_97_2,
        addressee=Addressee.PAYMENT_SERVICE_PROVIDER,
        enforcing=True,
    ),
    Obligation(provision=GDPR_ART_5_1_C, addressee=Addressee.CONTROLLER, enforcing=True),
    Obligation(provision=GDPR_ART_25, addressee=Addressee.CONTROLLER, enforcing=True),
    Obligation(provision=GDPR_ART_32, addressee=Addressee.CONTROLLER, enforcing=True),
    Obligation(
        provision=GDPR_ART_44,
        addressee=Addressee.CONTROLLER,
        enforcing=True,
        interpretation_boundary=(
            "Calling a model endpoint outside the EEA is a transfer. Whether it rests on "
            "an adequacy decision or on Art. 46 safeguards is the deployer's assessment."
        ),
    ),
    Obligation(
        provision=GDPR_ART_87,
        addressee=Addressee.CONTROLLER,
        enforcing=True,
        interpretation_boundary=(
            "A national identification number is not Art. 9 special-category data. The "
            "restriction arises from national law made under Art. 87, and the tests differ "
            "by country -- a single pan-Nordic rule would be wrong."
        ),
    ),
    Obligation(provision=DORA_ART_28_3, addressee=Addressee.FINANCIAL_ENTITY, enforcing=True),
    Obligation(provision=DORA_ART_23, addressee=Addressee.FINANCIAL_ENTITY, enforcing=True),
    RiskBasedObligation(
        provision=AMLR_ART_26_1,
        addressee=Addressee.OBLIGED_ENTITY,
        enforcing=False,
        rationale=(
            "Ongoing monitoring is risk-based. There is no amount at which structuring "
            "becomes reportable; structuring is reportable because it is structuring. "
            "Not enforcing: AMLR applies from 10 July 2027."
        ),
    ),
    RiskBasedObligation(
        provision=AMLR_ART_69_1_A,
        addressee=Addressee.OBLIGED_ENTITY,
        enforcing=False,
        rationale=(
            "Reporting attaches to suspicion 'regardless of the amount involved'. Any "
            "monetary trigger here would be both legally unfounded and a structuring "
            "roadmap. Not enforcing: AMLR applies from 10 July 2027."
        ),
    ),
    Obligation(provision=TFR_ART_4_4, addressee=Addressee.OBLIGED_ENTITY, enforcing=True),
)

# --------------------------------------------------------------------------
# Reference points: cited, never operative.
# --------------------------------------------------------------------------

REFERENCE_POINTS: tuple[ReferencePoint, ...] = (
    ReferencePoint(
        parameter=NumericParameter(
            name="amlr.art19.1.b.occasional_transaction_cdd",
            value=Decimal("10000"),
            currency="EUR",
            provenance=Provenance.REGULATORY_VERBATIM,
            locus=AMLR_ART_19_1_B,
        ),
        governs="CDD trigger for occasional transactions (was EUR 15 000 under AMLD4)",
        applies_from=_dt.date(2027, 7, 10),
    ),
    ReferencePoint(
        parameter=NumericParameter(
            name="amlr.art19.4.occasional_cash_cdd",
            value=Decimal("3000"),
            currency="EUR",
            provenance=Provenance.REGULATORY_VERBATIM,
            locus=AMLR_ART_19_4,
        ),
        governs=(
            "FULL customer due diligence trigger for occasional transactions in cash. "
            "Not a reduced or simplified regime -- simplified due diligence is Art. 33 "
            "and is risk-based, not threshold-based."
        ),
        applies_from=_dt.date(2027, 7, 10),
    ),
    ReferencePoint(
        parameter=NumericParameter(
            name="amlr.art80.cash_payment_limit",
            value=Decimal("10000"),
            currency="EUR",
            provenance=Provenance.REGULATORY_VERBATIM,
            locus=AMLR_ART_80,
        ),
        governs=(
            "Substantive prohibition on cash payments accepted or made in trade, including "
            "linked operations. Member States may set lower. Does not apply to payments "
            "between private individuals outside a professional capacity, nor to deposits "
            "at credit institutions, EMIs or PSPs."
        ),
        applies_from=_dt.date(2027, 7, 10),
    ),
    ReferencePoint(
        parameter=NumericParameter(
            name="tfr.art5.3.verification_derogation",
            value=Decimal("1000"),
            currency="EUR",
            provenance=Provenance.REGULATORY_VERBATIM,
            locus=TFR_ART_5_3,
        ),
        governs=(
            "Derogation from the Art. 4(4) duty to verify the accuracy of payer "
            "information, for intra-Union transfers not exceeding this amount and not "
            "appearing linked to others that together exceed it. Governs VERIFICATION, "
            "not reporting -- the figure is commonly misdescribed as a reporting "
            "threshold, and it is not one."
        ),
    ),
)

APPLICATION_DATES: tuple[ApplicationDate, ...] = (
    ApplicationDate(
        instrument=AMLR,
        date=_dt.date(2027, 7, 10),
        locus=AMLR_ART_90,
        verbatim_form="10 July 2027",
    ),
    ApplicationDate(
        instrument=DORA,
        date=_dt.date(2025, 1, 17),
        locus=DORA_ART_64,
        verbatim_form="17 January 2025",
    ),
)

# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Registry:
    _instruments: tuple[Instrument, ...]
    _obligations: tuple[Obligation, ...]
    _exemptions: tuple[Exemption, ...]
    _reference_points: tuple[ReferencePoint, ...]
    _application_dates: tuple[ApplicationDate, ...]

    def instruments(self) -> tuple[Instrument, ...]:
        return self._instruments

    def obligations(self) -> tuple[Obligation, ...]:
        return self._obligations

    def exemptions(self) -> tuple[Exemption, ...]:
        return self._exemptions

    def reference_points(self) -> tuple[ReferencePoint, ...]:
        return self._reference_points

    def application_dates(self) -> tuple[ApplicationDate, ...]:
        return self._application_dates

    def exemption(self, exemption_id: str) -> Exemption:
        for exemption in self._exemptions:
            if exemption.exemption_id == exemption_id:
                return exemption
        raise KeyError(exemption_id)

    def numeric_parameters(self) -> Iterator[NumericParameter]:
        for exemption in self._exemptions:
            yield from exemption.limbs
        for point in self._reference_points:
            yield point.parameter

    def provisions(self) -> Iterator[Provision]:
        seen: set[str] = set()
        candidates: list[Provision] = [o.provision for o in self._obligations]
        candidates += [e.provision for e in self._exemptions]
        candidates += [
            p.parameter.locus for p in self._reference_points if p.parameter.locus is not None
        ]
        candidates += [
            limb.locus
            for e in self._exemptions
            for limb in e.limbs
            if limb.locus is not None
        ]
        candidates += [d.locus for d in self._application_dates]
        for provision in candidates:
            key = f"{provision.corpus_key}|{provision.id}"
            if key not in seen:
                seen.add(key)
                yield provision


REGISTRY = Registry(
    _instruments=INSTRUMENTS,
    _obligations=OBLIGATIONS,
    _exemptions=EXEMPTIONS,
    _reference_points=REFERENCE_POINTS,
    _application_dates=APPLICATION_DATES,
)
