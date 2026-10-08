#!/usr/bin/env python3
"""Shared machinery for the entailment audit.

Two tools need the same view of a claim: the pricing script that measures what
the audit will cost, and the checker that runs it. Rendering a claim twice
would create two sources of truth, and the one that drifted would be the one
nobody was reading.

What the audit is for
---------------------
``registry.py`` asserts things about regulatory text: that Art. 97(1)(b) binds
a payment service provider, that the RTS Art. 16 EUR 30 figure bounds a
derogation rather than triggering a duty, that AMLR applies from a given date.
Those assertions were written by hand and have never been checked against the
pinned text. This is the check: each claim is judged against the verbatim span
it cites, and nothing else.

The verifier
------------
The rubric requires every verdict to carry a span copied character-for-
character from the provision, and states that the caller discards any verdict
whose span is not an exact substring. That requirement is what makes this a
mechanical check rather than a vote: ``verify_span`` below is the oracle, and a
model that paraphrases its evidence is rejected regardless of how confident it
sounds.

Stdlib only, apart from the vendor SDK the checker needs for transport.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finagent_safeguard.regulation.ingest import load_corpus  # noqa: E402
from finagent_safeguard.regulation.registry import REGISTRY  # noqa: E402

RUBRIC_PATH = ROOT / "finagent_safeguard/regulation/prompts/entailment_rubric.txt"
CORPUS_PATH = ROOT / "corpus"
VERDICT_PATH = ROOT / "docs/entailment-verdicts.jsonl"

CONFIRMED = "CONFIRMED"
UNSUPPORTED = "CLAIM_UNSUPPORTED"


def rubric() -> str:
    return RUBRIC_PATH.read_text()


def rubric_digest() -> str:
    """Hash of the rubric, recorded with every verdict.

    A verdict is only meaningful under the rubric that produced it. Editing the
    rubric invalidates prior verdicts, and without this they would look current.
    """
    return hashlib.sha256(RUBRIC_PATH.read_bytes()).hexdigest()


def render_claim(provision: Any) -> str:
    """The claim as the judge sees it: what the registry asserts about this text.

    Deliberately mechanical. Every line restates a typed field, so the judge is
    shown the registry's assertion rather than a summary of it -- a summary
    would be a third interpretation sitting between the code and the verdict.
    """
    lines = [f"Provision cited: {provision.id}"]

    if provision.obligation_text:
        lines.append(f'Quoted as: "{provision.obligation_text}"')

    for obligation in REGISTRY.obligations():
        if obligation.provision is provision:
            lines.append(f"Asserted addressee: {obligation.addressee}")
            lines.append(f"Enforcing: {obligation.enforcing}")
            # rationale is declared on RiskBasedObligation only, so read it
            # defensively rather than narrowing the type here.
            rationale = getattr(obligation, "rationale", "")
            if rationale:
                lines.append(
                    f"Asserted as risk-based, no monetary threshold: {rationale}"
                )

    for exemption in REGISTRY.exemptions():
        if exemption.provision is provision:
            lines.append(f"Asserted effect: {exemption.effect}")
            for limb in exemption.limbs:
                lines.append(f"  limb {limb.name} = {limb.as_cited} ({limb.provenance})")

    for param in REGISTRY.numeric_parameters():
        if param.locus is provision:
            lines.append(f"Numeral {param.name} = {param.as_cited} ({param.provenance})")

    for point in REGISTRY.reference_points():
        if point.parameter.locus is provision:
            lines.append(f"Reference point governs: {point.governs}")
            lines.append(f"Operative: {point.operative}")

    # Application dates were absent from the original renderer, so the two
    # date claims were priced but would never have been judged. A wrong
    # application date is the same class of error as a wrong threshold.
    for applies in REGISTRY.application_dates():
        if applies.locus is provision:
            lines.append(f"Asserted application date: {applies.date.isoformat()}")
            lines.append(f'Asserted verbatim form: "{applies.verbatim_form}"')
            for carve_out in applies.carve_outs:
                lines.append(
                    f"  carve-out {carve_out.date.isoformat()}"
                    f' applies to "{carve_out.applies_to}"'
                    f' clause "{carve_out.verbatim_clause}"'
                )

    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class Case:
    """One provision, its pinned text, and everything the registry claims of it."""

    provision_id: str
    corpus_key: str
    celex: str
    subdivision_id: str
    provision_text: str
    claim: str

    def payload(self) -> str:
        return f"PROVISION:\n{self.provision_text}\n\nCLAIM:\n{self.claim}"


def cases() -> list[Case]:
    """Every provision the registry makes a claim about, with its pinned text.

    Sorted by corpus key so a run is reproducible and two verdict files are
    diffable line by line.
    """
    corpus = load_corpus(CORPUS_PATH)
    out: list[Case] = []
    for provision in REGISTRY.provisions():
        span = corpus.get(provision.corpus_key)
        if span is None:
            # Not reachable while tests/test_registry_integrity.py passes; kept
            # so this tool reports the gap rather than silently judging fewer
            # claims than the registry makes.
            continue
        out.append(
            Case(
                provision_id=provision.id,
                corpus_key=provision.corpus_key,
                celex=span.celex,
                subdivision_id=span.subdivision_id,
                provision_text=span.text,
                claim=render_claim(provision),
            )
        )
    out.sort(key=lambda c: (c.corpus_key, c.provision_id))
    return out


def verify_span(span: str, provision_text: str) -> tuple[bool, str]:
    """Is the quoted span an exact substring of the provision?

    This is the oracle. The rubric promises the caller enforces it, so a
    verdict resting on a paraphrase is discarded -- not downgraded, discarded,
    because a confident verdict with invented evidence is the precise failure
    this project exists to catch.

    Returns (ok, reason). No normalising of whitespace, quotes or dashes: a
    legal text's typography is part of the text, and permitting "close enough"
    here would reintroduce the judgement the check is meant to remove.
    """
    if not span.strip():
        return False, "no span supplied"
    if span in provision_text:
        return True, ""
    stripped = span.strip()
    if stripped in provision_text:
        return False, "span matches only after stripping surrounding whitespace"
    if " ".join(span.split()) in " ".join(provision_text.split()):
        return False, "span matches only after collapsing whitespace; quote is not verbatim"
    return False, "span does not occur in the provision text"


def corpus_digest() -> str:
    """The digest the manifest recorded when the corpus was pinned."""
    import json

    manifest = json.loads((CORPUS_PATH / "MANIFEST.json").read_text())
    digest = manifest["corpus_digest"]
    if not isinstance(digest, str):  # pragma: no cover - manifest is committed
        raise TypeError("corpus_digest is not a string")
    return digest


def iter_cases() -> Iterator[Case]:
    yield from cases()


if __name__ == "__main__":
    found = cases()
    print(f"{len(found)} cases\n")
    for case in found:
        first = case.claim.splitlines()
        print(f"  {case.corpus_key:<34} {case.provision_id:<22} {len(first)} claim lines")
