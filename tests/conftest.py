"""Shared fixtures. See docs/test-matrix.md section 0."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

# Recorded from CELLAR on 2026-10-03. Not synthetic: a hand-built fixture would
# encode our assumptions about the markup rather than its actual structure.
RTS_SCA_CELEX = "02018R0389-20230725"


@pytest.fixture(scope="session")
def rts_sca_html() -> str:
    return (FIXTURES / "cellar" / f"{RTS_SCA_CELEX}.html").read_text(errors="replace")


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any outbound HTTP attempt an immediate, loud failure."""
    import urllib.request

    def _forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(urllib.request, "urlopen", _forbidden)


@pytest.fixture
def frozen_today() -> _dt.date:
    return _dt.date(2026, 10, 3)


@pytest.fixture(scope="session")
def corpus() -> dict[str, object]:
    from finagent_safeguard.regulation.ingest import load_corpus

    return load_corpus(Path(__file__).resolve().parents[1] / "corpus")  # type: ignore[return-value]
