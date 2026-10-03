"""Regulatory categories: a coverage mechanism, not a correctness mechanism.

A category answers one question -- *has this function been classified?* -- and
deliberately carries no threshold, citation or amount. Legal content lives in
``finagent_safeguard.regulation.registry``, where every number has provenance
and every quotation is machine-checked against pinned text.

Keeping that boundary is what stops the project reproducing its own original
error in a tidier wrapper: an enum member asserting "SCA above EUR 30" would
be just as unverified as the YAML key it replaced.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["FinancialCategory"]


class FinancialCategory(StrEnum):
    """What regulatory concern a developer's function touches."""

    PSD2_PAYMENT_EXECUTION = "psd2_payment_execution"
    PSD2_ACCOUNT_ACCESS = "psd2_account_access"
    GDPR_PII_PROCESSING = "gdpr_pii_processing"
    GDPR_THIRD_COUNTRY_TRANSFER = "gdpr_third_country_transfer"
    AML_TRANSACTION_MONITORING = "aml_transaction_monitoring"
    DORA_ICT_THIRD_PARTY = "dora_ict_third_party"
