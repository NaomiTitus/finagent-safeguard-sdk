"""Golden membership and identity test for the pinned corpus.

The expectations below live in THIS FILE, deliberately, and not in
corpus/MANIFEST.json. A test that compares the manifest against the files it
was generated from asserts self-consistency and is satisfied by one
regeneration command -- which is the hole this test exists to close.

There is no --update-golden flag and no make target, also deliberately.
Changing the corpus should mean hand-editing the constants below in the same
commit that changes the files, so the diff shows both halves and the update is
an act of reading rather than a reflex.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
from pathlib import Path

import pytest

from finagent_safeguard.regulation import ingest

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS = REPO_ROOT / "corpus"

# --- GOLDEN EXPECTATIONS -------------------------------------------------
# Update by hand, in the same commit as the corpus change. See module docstring.

GOLDEN_DIGEST = "f57c993491ab7831396698bfe9a63cfd1d00a6151329f9740122e9ac114cb436"
GOLDEN_COUNT = 21
GOLDEN_KEYS: tuple[str, ...] = (
    "02015L2366-20250117:art_97",
    "02016R0679-20160504:art_25",
    "02016R0679-20160504:art_32",
    "02016R0679-20160504:art_44",
    "02016R0679-20160504:art_5",
    "02016R0679-20160504:art_87",
    "02018R0389-20230725:anx_1",
    "02018R0389-20230725:art_11",
    "02018R0389-20230725:art_13",
    "02018R0389-20230725:art_16",
    "02018R0389-20230725:art_18",
    "32022R2554:art_23",
    "32022R2554:art_28",
    "32022R2554:art_64",
    "32023R1113:art_4",
    "32023R1113:art_5",
    "32024R1624:art_19",
    "32024R1624:art_26",
    "32024R1624:art_69",
    "32024R1624:art_80",
    "32024R1624:art_90",
)
# ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pinned() -> dict[str, ingest.PinnedProvision]:
    return ingest.load_corpus(CORPUS)


def _describe_drift(expected: tuple[str, ...], actual: tuple[str, ...]) -> str:
    """A diff, not a hash mismatch. A bare hex comparison invites regeneration."""
    added = sorted(set(actual) - set(expected))
    removed = sorted(set(expected) - set(actual))
    lines = ["corpus membership drifted:"]
    lines += [f"  + {key}" for key in added]
    lines += [f"  - {key}" for key in removed]
    if not added and not removed:
        lines.append("  (membership identical; a pinned text or legal_value changed)")
    return "\n".join(lines)


class TestGoldenIdentity:
    def test_corpus_keys_match_golden(self, pinned: dict[str, ingest.PinnedProvision]) -> None:
        actual = ingest.corpus_keys(pinned)
        assert actual == GOLDEN_KEYS, _describe_drift(GOLDEN_KEYS, actual)

    def test_provision_count_matches_golden(
        self, pinned: dict[str, ingest.PinnedProvision]
    ) -> None:
        assert len(pinned) == GOLDEN_COUNT

    def test_corpus_digest_matches_golden(
        self, pinned: dict[str, ingest.PinnedProvision]
    ) -> None:
        actual = ingest.corpus_digest(pinned)
        assert actual == GOLDEN_DIGEST, _describe_drift(
            GOLDEN_KEYS, ingest.corpus_keys(pinned)
        )


class TestDigestSensitivity:
    """What the digest must and must not react to."""

    def test_detects_a_changed_span_hash(
        self, pinned: dict[str, ingest.PinnedProvision]
    ) -> None:
        """Substitution: the failure the per-file hash check cannot see."""
        key = next(iter(ingest.corpus_keys(pinned)))
        tampered = dict(pinned)
        tampered[key] = dataclasses.replace(pinned[key], sha256="f" * 64)
        assert ingest.corpus_digest(tampered) != ingest.corpus_digest(pinned)

    def test_detects_an_added_provision(
        self, pinned: dict[str, ingest.PinnedProvision]
    ) -> None:
        key = next(iter(ingest.corpus_keys(pinned)))
        extended = dict(pinned)
        extended["32024R1689:art_12"] = dataclasses.replace(
            pinned[key], celex="32024R1689", subdivision_id="art_12"
        )
        assert ingest.corpus_digest(extended) != ingest.corpus_digest(pinned)

    def test_detects_a_removed_provision(
        self, pinned: dict[str, ingest.PinnedProvision]
    ) -> None:
        reduced = dict(pinned)
        reduced.pop(next(iter(ingest.corpus_keys(pinned))))
        assert ingest.corpus_digest(reduced) != ingest.corpus_digest(pinned)

    def test_detects_a_flipped_legal_value(
        self, pinned: dict[str, ingest.PinnedProvision]
    ) -> None:
        """Marking a consolidated span 'authentic' is a claim about legal
        authenticity. Nothing else in the suite would notice."""
        consolidated = next(
            key for key, p in pinned.items() if p.legal_value == "consolidated_no_legal_value"
        )
        flipped = dict(pinned)
        flipped[consolidated] = dataclasses.replace(
            pinned[consolidated], legal_value="authentic"
        )
        assert ingest.corpus_digest(flipped) != ingest.corpus_digest(pinned)

    def test_ignores_retrieved_at(self, pinned: dict[str, ingest.PinnedProvision]) -> None:
        """A re-pin yielding identical text with a fresh date must stay green,
        or the test becomes noise and gets disabled."""
        repinned = {
            key: dataclasses.replace(provision, retrieved_at=_dt.date(2027, 1, 1))
            for key, provision in pinned.items()
        }
        assert ingest.corpus_digest(repinned) == ingest.corpus_digest(pinned)

    def test_ignores_source_url(self, pinned: dict[str, ingest.PinnedProvision]) -> None:
        moved = {
            key: dataclasses.replace(provision, source_url="https://mirror.invalid")
            for key, provision in pinned.items()
        }
        assert ingest.corpus_digest(moved) == ingest.corpus_digest(pinned)


class TestPublishedManifest:
    def test_manifest_agrees_with_the_files(
        self, pinned: dict[str, ingest.PinnedProvision]
    ) -> None:
        manifest = json.loads((CORPUS / "MANIFEST.json").read_text())
        assert manifest["corpus_digest"] == ingest.corpus_digest(pinned)
        assert manifest["provision_count"] == len(pinned)

    def test_manifest_carries_no_single_retrieved_at(self) -> None:
        """One top-level date becomes a lie the moment a single file is re-pinned."""
        manifest = json.loads((CORPUS / "MANIFEST.json").read_text())
        assert "retrieved_at" not in manifest
        assert "retrieved_earliest" in manifest
        assert "retrieved_latest" in manifest

    def test_label_form_is_twelve_hex(self, pinned: dict[str, ingest.PinnedProvision]) -> None:
        label = ingest.corpus_label(pinned)
        assert len(label) == 12
        assert label == ingest.corpus_digest(pinned)[:12]


class TestNoRegenerationAffordance:
    def test_package_ships_no_golden_update_flag(self) -> None:
        """Encodes the decision: updating the golden must be a hand edit."""
        forbidden = ("--update-golden", "--update-digest", "--bless", "regen_golden")
        for path in (REPO_ROOT / "finagent_safeguard").rglob("*.py"):
            text = path.read_text()
            for token in forbidden:
                assert token not in text, f"{path.name} ships {token}"
