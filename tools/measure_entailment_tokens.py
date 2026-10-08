#!/usr/bin/env python3
"""Measure real token counts for the entailment check, and price them.

Replaces the planning estimate in PLAN.md 5.4.1 with a measurement. Prints
aggregates only -- no credentials, no provision text, nothing sensitive.

    ANTHROPIC_API_KEY=... python3 tools/measure_entailment_tokens.py

Uses the count_tokens endpoint, which does not generate output.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from finagent_safeguard.regulation.ingest import load_corpus  # noqa: E402
from finagent_safeguard.regulation.registry import REGISTRY  # noqa: E402

MODEL = "claude-opus-5"
RUBRIC = (ROOT / "finagent_safeguard/regulation/prompts/entailment_rubric.txt").read_text()

# $/MTok, verified against the live pricing page 2026-10-04.
PRICES = {
    "claude-opus-5": (Decimal("5"), Decimal("25"), Decimal("0.1")),
    "claude-opus-5-5": (Decimal("4"), Decimal("20"), Decimal("0.05")),
    "claude-sonnet-5": (Decimal("2"), Decimal("10"), Decimal("0.1")),
    "claude-haiku-4-5": (Decimal("1"), Decimal("5"), Decimal("0.1")),
}
OUTPUT_PER_CALL = 2000  # thinking + verdict, at effort=high. A judgement call.


from tools.entailment import render_claim  # single source of truth


def main() -> int:
    import anthropic

    client = anthropic.Anthropic()
    corpus = load_corpus(ROOT / "corpus")

    rubric_tokens = client.messages.count_tokens(
        model=MODEL, system=RUBRIC, messages=[{"role": "user", "content": "x"}]
    ).input_tokens

    per_call: list[tuple[int, str]] = []
    for provision in REGISTRY.provisions():
        span = corpus.get(provision.corpus_key)
        if span is None:
            continue
        payload = f"PROVISION:\n{span.text}\n\nCLAIM:\n{render_claim(provision)}"
        n = client.messages.count_tokens(
            model=MODEL, system=RUBRIC, messages=[{"role": "user", "content": payload}]
        ).input_tokens
        per_call.append((n, provision.id))

    per_call.sort(reverse=True)
    calls = len(per_call)
    total_in = sum(n for n, _ in per_call)
    total_out = calls * OUTPUT_PER_CALL
    variable_in = total_in - calls * rubric_tokens

    print(f"model                {MODEL}")
    print(f"rubric (fixed)       {rubric_tokens:,} tokens, re-sent {calls}x")
    print(f"calls                {calls}")
    print(f"input  total         {total_in:,}")
    print(f"  of which rubric    {calls * rubric_tokens:,} "
          f"({calls * rubric_tokens * 100 // total_in}%)")
    print(f"  of which variable  {variable_in:,}")
    print(f"per call  min/mean/max  {per_call[-1][0]:,} / {total_in // calls:,} / "
          f"{per_call[0][0]:,}")
    print(f"largest              {per_call[0][1]}")
    print(f"output assumption    {OUTPUT_PER_CALL:,}/call -> {total_out:,} total\n")

    print(f"{'model':<20}{'uncached':>10}{'cached':>10}{'batch+cached':>14}")
    for name, (pin, pout, read) in PRICES.items():
        inp = Decimal(total_in) * pin / Decimal(10**6)
        out = Decimal(total_out) * pout / Decimal(10**6)
        # cached: one write at 1.25x, (calls-1) reads at `read`x, variable at 1x
        cached_in = (
            Decimal(rubric_tokens) * pin * Decimal("1.25")
            + Decimal(rubric_tokens * (calls - 1)) * pin * read
            + Decimal(variable_in) * pin
        ) / Decimal(10**6)
        print(f"{name:<20}{'$' + f'{inp + out:.2f}':>10}"
              f"{'$' + f'{cached_in + out:.2f}':>10}"
              f"{'$' + f'{(cached_in + out) / 2:.2f}':>14}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
