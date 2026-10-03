"""Pin the corpus from CELLAR. Run once; commit the output.

    python -m finagent_safeguard.regulation.pin

Network is used here and nowhere else in the package.
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
import urllib.request
from pathlib import Path

from finagent_safeguard.regulation.ingest import pin

CELLAR = "https://publications.europa.eu/resource/celex/{celex}"

# (celex, subdivisions). Consolidated expressions where one exists, base acts
# otherwise. Article numbers are the verified set in PLAN.md section 4.1.
PROVISIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("02018R0389-20230725", ("art_11", "art_13", "art_16", "art_18", "anx_1")),
    ("02015L2366-20250117", ("art_97",)),
    ("02016R0679-20160504", ("art_5", "art_25", "art_32", "art_44", "art_87")),
    ("32024R1624", ("art_19", "art_26", "art_69", "art_80", "art_90")),
    ("32023R1113", ("art_4", "art_5")),
    ("32022R2554", ("art_23", "art_28", "art_64")),
)


def fetch(celex: str) -> str:
    request = urllib.request.Request(
        CELLAR.format(celex=celex),
        headers={"Accept": "application/xhtml+xml", "Accept-Language": "eng"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        payload: bytes = response.read()
    return payload.decode("utf-8", errors="replace")


def main() -> int:
    root = Path(__file__).resolve().parents[2] / "corpus"
    today = _dt.date.today()
    written = 0

    for celex, subdivisions in PROVISIONS:
        print(f"fetching {celex} ...", file=sys.stderr)
        html = fetch(celex)
        target = root / celex
        target.mkdir(parents=True, exist_ok=True)
        for subdivision in subdivisions:
            provision = pin(
                celex=celex,
                subdivision_id=subdivision,
                html=html,
                retrieved_at=today,
                source_url=CELLAR.format(celex=celex),
            )
            (target / f"{subdivision}.json").write_text(
                json.dumps(provision.to_dict(), indent=2, ensure_ascii=False) + "\n"
            )
            written += 1
            print(f"  {subdivision:8s} {len(provision.text):6d} chars  {provision.title}")

    print(f"\npinned {written} provisions", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
