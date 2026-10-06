"""RED-phase specification for corpus ingestion.

See docs/test-matrix.md section 11. These tests run entirely against a
CELLAR response recorded on 2026-10-03; none of them touch the network.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import re
from pathlib import Path

import pytest

from finagent_safeguard.regulation import ingest

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestSubdivisionExtraction:
    def test_chunk_extracts_only_target_article(self, rts_sca_html: str) -> None:
        """An Art. 16 span must not bleed into its neighbours.

        If chunking leaks adjacent articles, every later PIN step pins the
        wrong text and the project's whole verification discipline inverts.
        """
        art15 = ingest.extract_subdivision(rts_sca_html, "art_15")
        art16 = ingest.extract_subdivision(rts_sca_html, "art_16")
        art17 = ingest.extract_subdivision(rts_sca_html, "art_17")

        assert "EUR 30" in art16.text
        assert art15.title not in art16.text
        assert art17.title not in art16.text
        assert "Article 17" not in art16.text

    def test_chunk_captures_article_title(self, rts_sca_html: str) -> None:
        """RTS on SCA Art. 16 is titled 'Low-value transactions'."""
        assert ingest.extract_subdivision(rts_sca_html, "art_16").title == (
            "Low-value transactions"
        )

    def test_chunk_handles_inserted_article(self, rts_sca_html: str) -> None:
        """art_10a is a genuine inserted article; it must not contaminate art_10."""
        art10 = ingest.extract_subdivision(rts_sca_html, "art_10")
        art10a = ingest.extract_subdivision(rts_sca_html, "art_10a")

        assert art10.text
        assert art10a.text
        assert art10.text != art10a.text
        assert art10a.text not in art10.text

    def test_annex_without_eli_class_is_extracted(self, rts_sca_html: str) -> None:
        """The Annex is a bare <div id="anx_1"> with no eli-subdivision class.

        The Art. 18 reference fraud rates live here, so a selector that
        requires the class would silently lose the entire table.
        """
        annex = ingest.extract_subdivision(rts_sca_html, "anx_1")
        for figure in ("EUR 500", "0,01", "0,005", "EUR 250", "0,06", "EUR 100", "0,13", "0,015"):
            assert figure in annex.text, figure

    def test_unknown_subdivision_raises_rather_than_guessing(self, rts_sca_html: str) -> None:
        """Silently returning empty or nearest-match text is the dangerous failure."""
        with pytest.raises(ingest.SubdivisionNotFound):
            ingest.extract_subdivision(rts_sca_html, "art_999")

    def test_extraction_makes_no_network_call(self, rts_sca_html: str, no_network: None) -> None:
        """The no_network fixture raises on any outbound call; assert the real
        result too, so the test states what it checks rather than relying on a
        fixture side effect."""
        assert ingest.extract_subdivision(rts_sca_html, "art_16").text


class TestConsolidatedVersionSelection:
    CANDIDATES = (
        "02015L2366-20151223",
        "02015L2366-20240408",
        "02015L2366-20250117",
    )

    @pytest.mark.parametrize(
        ("as_of", "expected"),
        [
            (_dt.date(2024, 1, 1), "02015L2366-20151223"),
            (_dt.date(2024, 4, 8), "02015L2366-20240408"),
            (_dt.date(2024, 4, 7), "02015L2366-20151223"),
            (_dt.date(2026, 10, 3), "02015L2366-20250117"),
        ],
    )
    def test_selects_greatest_consolidation_at_or_before_date(
        self, as_of: _dt.date, expected: str
    ) -> None:
        assert ingest.select_consolidated_version(self.CANDIDATES, as_of) == expected

    def test_date_before_every_consolidation_raises(self) -> None:
        with pytest.raises(ingest.NoApplicableVersion):
            ingest.select_consolidated_version(self.CANDIDATES, _dt.date(2015, 1, 1))


class TestPinning:
    def test_hash_recorded_at_ingest_time(self, rts_sca_html: str) -> None:
        pinned = ingest.pin(
            celex="02018R0389-20230725",
            subdivision_id="art_16",
            html=rts_sca_html,
            retrieved_at=_dt.date(2026, 10, 3),
            source_url="https://publications.europa.eu/resource/celex/02018R0389-20230725",
        )
        assert pinned.sha256 == hashlib.sha256(pinned.text.encode("utf-8")).hexdigest()
        assert re.fullmatch(r"[0-9a-f]{64}", pinned.sha256)

    def test_consolidated_celex_records_no_legal_value(self, rts_sca_html: str) -> None:
        """Consolidated texts are editorial; only the OJ is authentic."""
        pinned = ingest.pin(
            celex="02018R0389-20230725",
            subdivision_id="art_16",
            html=rts_sca_html,
            retrieved_at=_dt.date(2026, 10, 3),
            source_url="https://example.invalid",
        )
        assert pinned.legal_value == "consolidated_no_legal_value"

    def test_base_act_celex_records_authentic(self, rts_sca_html: str) -> None:
        pinned = ingest.pin(
            celex="32018R0389",
            subdivision_id="art_16",
            html=rts_sca_html,
            retrieved_at=_dt.date(2026, 10, 3),
            source_url="https://example.invalid",
        )
        assert pinned.legal_value == "authentic"

    def test_roundtrip_through_json_preserves_text_and_hash(self, rts_sca_html: str) -> None:
        pinned = ingest.pin(
            celex="02018R0389-20230725",
            subdivision_id="art_16",
            html=rts_sca_html,
            retrieved_at=_dt.date(2026, 10, 3),
            source_url="https://example.invalid",
        )
        restored = ingest.PinnedProvision.from_dict(pinned.to_dict())
        assert restored == pinned
        assert restored.sha256 == pinned.sha256


class TestCorpusOnDisk:
    def test_notice_file_present_and_complete(self) -> None:
        """Reuse under Commission Decision 2011/833/EU carries conditions."""
        notice = (REPO_ROOT / "corpus" / "NOTICE").read_text()
        assert "2011/833/EU" in notice
        assert "European Union" in notice
        assert "no legal value" in notice
        assert "Official Journal" in notice

    def test_corpus_load_makes_no_network_call(self, no_network: None) -> None:
        provisions = ingest.load_corpus(REPO_ROOT / "corpus")
        assert provisions, "corpus is empty; run the pin step"

    def test_every_pinned_file_hash_matches_its_text(self, no_network: None) -> None:
        for key, provision in ingest.load_corpus(REPO_ROOT / "corpus").items():
            expected = hashlib.sha256(provision.text.encode("utf-8")).hexdigest()
            assert provision.sha256 == expected, key
