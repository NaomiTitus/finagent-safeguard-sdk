"""Corpus ingestion: pin verbatim provisions from CELLAR.

Design notes
------------
Source is CELLAR (``publications.europa.eu``), never ``eur-lex.europa.eu``,
which returns HTTP 202 with an empty body to programmatic clients.

Consolidated expressions mark up each article as
``<div class="eli-subdivision" id="art_16">``. Some subdivisions -- the RTS
Annex among them -- are a bare ``<div id="anx_1">`` with no class at all, and
the Art. 18 reference fraud rates live there, so the selector must not require
the class.

Ingestion runs once; its output is committed. Nothing here is called at
request time, and ``load_corpus`` never touches the network.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import html as _html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

__all__ = [
    "LegalValue",
    "NoApplicableVersion",
    "PinnedProvision",
    "Subdivision",
    "SubdivisionNotFound",
    "extract_subdivision",
    "load_corpus",
    "pin",
    "select_consolidated_version",
]

LegalValue = Literal["authentic", "consolidated_no_legal_value"]

_CONSOLIDATED_CELEX: Final = re.compile(r"^0\d{4}[LRD]\d{4}-(\d{8})$")
_BASE_ACT_CELEX: Final = re.compile(r"^3\d{4}[LRD]\d{4}$")
_DIV_TAG: Final = re.compile(r"<(/?)div\b[^>]*>", re.IGNORECASE)
_TAG: Final = re.compile(r"<[^>]+>")
_TITLE_DIV: Final = re.compile(
    r'<div[^>]*class="[^"]*\beli-title\b[^"]*"[^>]*>(.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)


class SubdivisionNotFound(LookupError):
    """The requested subdivision id is absent from the document."""


class NoApplicableVersion(LookupError):
    """No consolidated version was in force on the requested date."""


@dataclass(frozen=True, slots=True)
class Subdivision:
    """One article, paragraph or annex, as marked up in the source document."""

    subdivision_id: str
    title: str
    text: str


@dataclass(frozen=True, slots=True)
class PinnedProvision:
    """A verbatim span committed to the corpus, with its provenance."""

    celex: str
    subdivision_id: str
    title: str
    text: str
    sha256: str
    retrieved_at: _dt.date
    source_url: str
    legal_value: LegalValue

    def to_dict(self) -> dict[str, Any]:
        return {
            "celex": self.celex,
            "subdivision_id": self.subdivision_id,
            "title": self.title,
            "text": self.text,
            "sha256": self.sha256,
            "retrieved_at": self.retrieved_at.isoformat(),
            "source_url": self.source_url,
            "legal_value": self.legal_value,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PinnedProvision:
        return cls(
            celex=payload["celex"],
            subdivision_id=payload["subdivision_id"],
            title=payload["title"],
            text=payload["text"],
            sha256=payload["sha256"],
            retrieved_at=_dt.date.fromisoformat(payload["retrieved_at"]),
            source_url=payload["source_url"],
            legal_value=payload["legal_value"],
        )


def _plain_text(fragment: str) -> str:
    """Strip markup and normalise whitespace, preserving the wording."""
    text = _TAG.sub(" ", fragment)
    text = _html.unescape(text).replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip()


def _find_subdivision_span(html: str, subdivision_id: str) -> str:
    """Return the raw markup of the div bearing ``subdivision_id``.

    Matches on the id alone: requiring ``class="eli-subdivision"`` would
    silently drop the Annex, which carries no class.
    """
    opening = re.search(
        rf'<div[^>]*\bid="{re.escape(subdivision_id)}"[^>]*>', html, re.IGNORECASE
    )
    if opening is None:
        raise SubdivisionNotFound(subdivision_id)

    depth = 0
    for tag in _DIV_TAG.finditer(html, opening.start()):
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            return html[opening.end() : tag.start()]
    # Unbalanced markup: treat as absent rather than return a truncated span.
    raise SubdivisionNotFound(subdivision_id)


def extract_subdivision(html: str, subdivision_id: str) -> Subdivision:
    """Extract one subdivision's title and verbatim text."""
    span = _find_subdivision_span(html, subdivision_id)
    title_match = _TITLE_DIV.search(span)
    title = _plain_text(title_match.group(1)) if title_match else ""
    body = _TITLE_DIV.sub(" ", span, count=1) if title_match else span
    return Subdivision(subdivision_id=subdivision_id, title=title, text=_plain_text(body))


def select_consolidated_version(candidates: tuple[str, ...], as_of: _dt.date) -> str:
    """Pick the consolidated expression in force on ``as_of``.

    CELLAR offers no single "version in force on date D" lookup, so the rule
    is mechanical: the greatest consolidation date at or before D.
    """
    dated: list[tuple[_dt.date, str]] = []
    for celex in candidates:
        match = _CONSOLIDATED_CELEX.match(celex)
        if match is None:
            raise ValueError(f"not a dated consolidated CELEX: {celex!r}")
        dated.append((_dt.datetime.strptime(match.group(1), "%Y%m%d").date(), celex))

    applicable = [(date, celex) for date, celex in dated if date <= as_of]
    if not applicable:
        raise NoApplicableVersion(
            f"no consolidated version of {candidates[0][:10]} at or before {as_of.isoformat()}"
        )
    return max(applicable)[1]


def _legal_value_for(celex: str) -> LegalValue:
    if _CONSOLIDATED_CELEX.match(celex):
        return "consolidated_no_legal_value"
    if _BASE_ACT_CELEX.match(celex):
        return "authentic"
    raise ValueError(f"unrecognised CELEX form: {celex!r}")


def pin(
    *,
    celex: str,
    subdivision_id: str,
    html: str,
    retrieved_at: _dt.date,
    source_url: str,
) -> PinnedProvision:
    """Extract a subdivision and record it with its hash and provenance."""
    subdivision = extract_subdivision(html, subdivision_id)
    return PinnedProvision(
        celex=celex,
        subdivision_id=subdivision_id,
        title=subdivision.title,
        text=subdivision.text,
        sha256=hashlib.sha256(subdivision.text.encode("utf-8")).hexdigest(),
        retrieved_at=retrieved_at,
        source_url=source_url,
        legal_value=_legal_value_for(celex),
    )


def load_corpus(root: Path) -> dict[str, PinnedProvision]:
    """Load every pinned provision from disk. Never touches the network."""
    provisions: dict[str, PinnedProvision] = {}
    for path in sorted(root.glob("*/*.json")):
        provision = PinnedProvision.from_dict(json.loads(path.read_text()))
        provisions[f"{provision.celex}:{provision.subdivision_id}"] = provision
    return provisions


# --------------------------------------------------------------------------
# Corpus identity
#
# The per-file hashes above are self-consistency only: re-running the pin step
# rewrites text and hash together, so an arbitrary corpus substitution passes
# every check that recomputes a file's hash from its own text. What detects
# substitution is an expectation held OUTSIDE corpus/ -- and what detects
# addition or deletion is an expectation about membership.
#
# `legal_value` is part of the digest tuple deliberately. Flipping a
# consolidated span to "authentic" is a claim about legal authenticity, and
# nothing else in the suite would notice.
#
# `retrieved_at` and `source_url` are deliberately excluded: a re-pin that
# produces byte-identical text with a fresh date must not go red, or the test
# becomes noise and gets disabled.
# --------------------------------------------------------------------------


def digest_tuple(provision: PinnedProvision) -> tuple[str, str, str, str]:
    """The fields that constitute a provision's identity."""
    return (
        provision.celex,
        provision.subdivision_id,
        provision.sha256,
        provision.legal_value,
    )


def corpus_keys(provisions: dict[str, PinnedProvision]) -> tuple[str, ...]:
    """Sorted membership of the corpus. Reviewable in a diff, unlike a hash."""
    return tuple(sorted(provisions))


def corpus_digest(provisions: dict[str, PinnedProvision]) -> str:
    """One short token identifying the whole pinned corpus state."""
    canonical = "\n".join(
        "|".join(digest_tuple(provisions[key])) for key in corpus_keys(provisions)
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def corpus_label(provisions: dict[str, PinnedProvision]) -> str:
    """Short form for a Prometheus label, by git short-hash convention."""
    return corpus_digest(provisions)[:12]


def build_manifest(provisions: dict[str, PinnedProvision]) -> dict[str, Any]:
    """The published artifact. Not the oracle -- see tests/test_corpus_manifest.py."""
    dates = sorted(p.retrieved_at for p in provisions.values())
    return {
        "corpus_digest": corpus_digest(provisions),
        "provision_count": len(provisions),
        "retrieved_earliest": dates[0].isoformat() if dates else None,
        "retrieved_latest": dates[-1].isoformat() if dates else None,
        "provisions": [
            {
                "celex": provisions[key].celex,
                "subdivision_id": provisions[key].subdivision_id,
                "sha256": provisions[key].sha256,
                "legal_value": provisions[key].legal_value,
                "retrieved_at": provisions[key].retrieved_at.isoformat(),
            }
            for key in corpus_keys(provisions)
        ],
    }
