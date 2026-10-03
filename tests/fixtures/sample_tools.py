"""Fixture module. Importing it must populate the registry, with no calls."""

from __future__ import annotations

from decimal import Decimal

from finagent_safeguard.core.decorators import regulated_tool
from finagent_safeguard.taxonomy.policies import FinancialCategory


@regulated_tool(FinancialCategory.PSD2_PAYMENT_EXECUTION)
def send_money(amount: Decimal, recipient_iban: str) -> str:
    return f"sent {amount} to {recipient_iban}"


@regulated_tool(FinancialCategory.PSD2_PAYMENT_EXECUTION)
@regulated_tool(FinancialCategory.GDPR_PII_PROCESSING)
def pay_and_notify(amount: Decimal, national_id: str) -> str:
    return f"paid {amount}, notified {national_id}"


def undecorated_transfer(amount: Decimal, iban: str) -> str:
    return f"moved {amount} to {iban}"
