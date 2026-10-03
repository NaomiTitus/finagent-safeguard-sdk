"""Instruments and provisions as validated values, not strings.

A citation that is merely a ``str`` is decoration: nothing resolves it and
nothing objects when it contradicts the number beside it. Here an instrument
carries its CELEX, its status, its application date per jurisdiction concept,
and a review date; a provision carries the corpus key of the verbatim span it
quotes, so the quotation can be machine-checked.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

__all__ = ["EeaStatus", "Instrument", "NOT_YET_DETERMINED", "Provision", "Status"]

_BASE_ACT: Final = re.compile(r"^3\d{4}[LRD]\d{4}$")
_CONSOLIDATED: Final = re.compile(r"^0\d{4}[LRD]\d{4}-\d{8}$")

#: Sentinel for an instrument whose application date is genuinely unknown --
#: PSD3/PSR is politically agreed but not in the Official Journal, and
#: PLAN.md section 4.6 forbids publishing a date for it. This is explicitly
#: "not determined", never a claim that the date is far in the future.
NOT_YET_DETERMINED: Final[_dt.date] = _dt.date.max


class Status(StrEnum):
    IN_FORCE = "in_force"
    PENDING = "pending"
    SUPERSEDED = "superseded"


class EeaStatus(StrEnum):
    INCORPORATED = "incorporated"
    PENDING_INCORPORATION = "pending_incorporation"
    NOT_EEA_RELEVANT = "not_eea_relevant"


@dataclass(frozen=True, slots=True, kw_only=True)
class Instrument:
    """One legal instrument, dated and status-aware."""

    short_name: str
    title: str
    status: Status
    applies_from: _dt.date
    review_by: _dt.date
    celex: str | None = None
    consolidated_celex: str | None = None
    eea_status: EeaStatus = EeaStatus.NOT_EEA_RELEVANT
    predecessor_in_force: str = ""
    #: Free-text note on anything the metadata cannot express honestly.
    note: str = ""

    def __post_init__(self) -> None:
        if self.celex is None:
            if self.status is not Status.PENDING:
                raise ValueError(
                    f"{self.short_name}: only a pending instrument may lack a CELEX"
                )
        elif not _BASE_ACT.match(self.celex):
            raise ValueError(f"{self.short_name}: not a base-act CELEX: {self.celex!r}")

        if self.consolidated_celex is not None and not _CONSOLIDATED.match(
            self.consolidated_celex
        ):
            raise ValueError(
                f"{self.short_name}: not a consolidated CELEX: {self.consolidated_celex!r}"
            )

    @property
    def corpus_celex(self) -> str:
        """The expression the corpus pins: consolidated where one exists."""
        if self.consolidated_celex is not None:
            return self.consolidated_celex
        if self.celex is None:
            raise ValueError(f"{self.short_name} has no pinned expression")
        return self.celex

    @property
    def eurlex_url(self) -> str:
        if self.celex is None:
            raise ValueError(f"{self.short_name} is not yet published")
        return f"https://eur-lex.europa.eu/eli/legal-content/EN/TXT/?uri=CELEX:{self.celex}"


@dataclass(frozen=True, slots=True, kw_only=True)
class Provision:
    """One article, paragraph or point, bound to its pinned verbatim span."""

    instrument: Instrument
    article: str
    subdivision_id: str
    paragraph: str | None = None
    point: str | None = None
    #: A verbatim quotation. Must be a substring of the pinned span --
    #: enforced by tests/test_registry_integrity.py. Paraphrase is rejected.
    obligation_text: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def id(self) -> str:
        """Stable, low-cardinality identifier. Safe as a metric label."""
        tail = ".".join(p for p in (self.article, self.paragraph, self.point) if p)
        celex = self.instrument.celex or self.instrument.short_name
        return f"{celex}:{tail}"

    @property
    def corpus_key(self) -> str:
        return f"{self.instrument.corpus_celex}:{self.subdivision_id}"
