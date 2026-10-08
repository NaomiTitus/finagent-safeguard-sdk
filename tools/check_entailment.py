#!/usr/bin/env python3
"""Audit every registry claim against the verbatim text it cites.

PLAN.md 5.4 designed this gate and it was never run, so the regulatory tags --
the core of the project -- are currently one person's unchecked reading. This
runs it.

    python3 tools/check_entailment.py --dry-run    # tokens and cost, no spend
    python3 tools/check_entailment.py              # run it, write verdicts
    python3 tools/check_entailment.py --check      # read committed verdicts only

``--check`` makes no network call and is the form CI would use: it fails if a
verdict is missing, stale, or unsupported.

Why a model is allowed to judge this at all
-------------------------------------------
Because it is not trusted. The rubric requires every verdict to quote a span
copied character-for-character from the provision, and ``verify_span`` rejects
any verdict whose span is not an exact substring. The quote is the evidence and
the substring test is the oracle, so a fluent verdict resting on a paraphrase
is discarded rather than believed. That is the only reason this is a check and
not a second opinion.

Credentials
-----------
Reads ``ANTHROPIC_API_KEY`` from the environment. It is never printed, never
logged, and never written to the verdict file.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.entailment import (  # noqa: E402
    CONFIRMED,
    UNSUPPORTED,
    VERDICT_PATH,
    Case,
    cases,
    corpus_digest,
    rubric,
    rubric_digest,
    verify_span,
)

MODEL = "claude-opus-5"
API = "https://api.anthropic.com/v1"
API_VERSION = "2023-06-01"


def _post(path: str, body: dict[str, Any], *, attempts: int = 4) -> dict[str, Any]:
    """POST to the Messages API using the standard library.

    The vendor SDK's HTTP client cannot reach the API from this environment
    (``APIConnectionError``) while ``urllib`` and ``curl`` both can. Using the
    stdlib is the better answer regardless: this project is stdlib-only by
    design, and the audit should not depend on a package to be reproducible by
    whoever reads the repository.

    Retries on the transient classes only -- 429 and 5xx, plus connection
    errors. A 400 or 401 is a bug or a bad key and retrying would just spend
    four times as long failing.
    """
    import time
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        f"{API}/{path}",
        data=json.dumps(body).encode(),
        headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        },
    )

    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                loaded = json.load(response)
            if not isinstance(loaded, dict):
                raise ValueError("API response is not an object")
            return loaded
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 529):
                detail = exc.read()[:400].decode("utf-8", "replace")
                raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
            last = exc
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            last = exc
        if attempt < attempts - 1:
            time.sleep(2**attempt)

    raise RuntimeError(f"giving up after {attempts} attempts: {last}")


def _create(system: str, payload: str, max_tokens: int) -> dict[str, Any]:
    return _post(
        "messages",
        {
            "model": MODEL,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": payload}],
        },
    )


def _count_tokens(system: str, payload: str) -> int:
    counted = _post(
        "messages/count_tokens",
        {
            "model": MODEL,
            "system": system,
            "messages": [{"role": "user", "content": payload}],
        },
    )
    return int(counted["input_tokens"])

# $/MTok (input, output, cache-read), verified against the live pricing page
# 2026-10-04. PLAN.md 5.4.1 quotes 4/20 for this model, which is stale.
PRICES: dict[str, tuple[Decimal, Decimal, Decimal]] = {
    "claude-opus-5": (Decimal("5"), Decimal("25"), Decimal("0.1")),
    "claude-sonnet-5": (Decimal("2"), Decimal("10"), Decimal("0.1")),
}
OUTPUT_PER_CALL = 2000

#: Appended to each payload. Kept out of the rubric deliberately -- the rubric
#: is hashed into every verdict, and a transport detail changing that hash
#: would invalidate prior verdicts for no substantive reason.
OUTPUT_CONTRACT = """

Reply with a single JSON object and nothing else:
{"verdict": "CONFIRMED" or "CLAIM_UNSUPPORTED",
 "span": "<contiguous span copied character-for-character from PROVISION>",
 "reason": "<one or two sentences>"}
"""


def _parse(text: str) -> dict[str, str]:
    """Pull the JSON object out of a reply, tolerating code fences."""
    body = text.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1] if "\n" in body else body
        body = body.rsplit("```", 1)[0]
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in reply: {text[:160]!r}")
    loaded = json.loads(body[start : end + 1])
    if not isinstance(loaded, dict):
        raise ValueError("reply JSON is not an object")
    return {str(k): str(v) for k, v in loaded.items()}


def _judge(case: Case) -> dict[str, Any]:
    reply = _create(rubric(), case.payload() + OUTPUT_CONTRACT, OUTPUT_PER_CALL)
    text = "".join(
        block.get("text", "") for block in reply["content"] if block.get("type") == "text"
    )
    parsed = _parse(text)

    verdict = parsed.get("verdict", "")
    span = parsed.get("span", "")
    ok, why = verify_span(span, case.provision_text)

    return {
        "provision_id": case.provision_id,
        "corpus_key": case.corpus_key,
        "celex": case.celex,
        "subdivision_id": case.subdivision_id,
        "verdict": verdict if verdict in (CONFIRMED, UNSUPPORTED) else "MALFORMED",
        "span": span,
        "span_verified": ok,
        "span_problem": why,
        "reason": parsed.get("reason", ""),
        "usage": {
            "input_tokens": reply["usage"]["input_tokens"],
            "output_tokens": reply["usage"]["output_tokens"],
        },
    }


def _header() -> dict[str, str]:
    """Provenance stamped on the run, so a stale file cannot pass as fresh."""
    return {
        "record": "run_header",
        "model": MODEL,
        "rubric_sha256": rubric_digest(),
        "corpus_digest": corpus_digest(),
        "ran_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
    }


def _dry_run(found: list[Case]) -> int:
    rubric_text = rubric()
    fixed = _count_tokens(rubric_text, "x")

    totals: list[tuple[int, str]] = []
    for case in found:
        n = _count_tokens(rubric_text, case.payload() + OUTPUT_CONTRACT)
        totals.append((n, case.provision_id))

    calls = len(totals)
    total_in = sum(n for n, _ in totals)
    total_out = calls * OUTPUT_PER_CALL
    in_price, out_price, _ = PRICES[MODEL]
    cost = (
        Decimal(total_in) * in_price + Decimal(total_out) * out_price
    ) / Decimal(1_000_000)

    totals.sort(reverse=True)
    print(f"model              {MODEL}")
    print(f"claims to judge    {calls}")
    print(f"rubric (re-sent)   {fixed:,} tokens x {calls}")
    print(f"input  total       {total_in:,}")
    print(f"output budget      {total_out:,}  ({OUTPUT_PER_CALL:,}/call, an upper bound)")
    print(f"largest claim      {totals[0][1]} at {totals[0][0]:,} tokens")
    print(f"\nupper-bound cost   ${cost:.2f}")
    print("\nNothing was generated and nothing was charged beyond token counting.")
    return 0


def _report(records: list[dict[str, Any]]) -> int:
    """Print the audit and decide the exit code."""
    header = next((r for r in records if r.get("record") == "run_header"), None)
    verdicts = [r for r in records if r.get("record") != "run_header"]

    if header is None:
        print("verdict file has no run header; provenance unknown", file=sys.stderr)
        return 1

    stale = []
    if header.get("rubric_sha256") != rubric_digest():
        stale.append("the rubric has changed since these verdicts were produced")
    if header.get("corpus_digest") != corpus_digest():
        stale.append("the pinned corpus has changed since these verdicts were produced")

    expected = {c.provision_id for c in cases()}
    judged = {r["provision_id"] for r in verdicts}
    unjudged = sorted(expected - judged)
    extra = sorted(judged - expected)

    confirmed = [r for r in verdicts if r["verdict"] == CONFIRMED and r["span_verified"]]
    unsupported = [r for r in verdicts if r["verdict"] == UNSUPPORTED]
    discarded = [r for r in verdicts if not r["span_verified"]]
    malformed = [r for r in verdicts if r["verdict"] == "MALFORMED"]

    print(f"model              {header.get('model')}")
    print(f"ran at             {header.get('ran_at')}")
    print(f"claims judged      {len(verdicts)} of {len(expected)}\n")
    print(f"  confirmed, span verified   {len(confirmed)}")
    print(f"  CLAIM_UNSUPPORTED          {len(unsupported)}")
    print(f"  discarded, span unverified {len(discarded)}")
    print(f"  malformed verdict          {len(malformed)}")

    for label, rows in (
        ("CLAIMS THE TEXT DOES NOT SUPPORT", unsupported),
        ("VERDICTS DISCARDED FOR UNVERIFIED EVIDENCE", discarded),
        ("MALFORMED", malformed),
    ):
        if rows:
            print(f"\n{label}:")
            for r in rows:
                print(f"  {r['provision_id']}  ({r['corpus_key']})")
                if r.get("reason"):
                    print(f"    reason: {r['reason']}")
                if r.get("span_problem"):
                    print(f"    span:   {r['span_problem']}")

    problems = []
    if stale:
        problems += stale
    if unjudged:
        problems.append(f"{len(unjudged)} claim(s) never judged: {unjudged}")
    if extra:
        problems.append(f"{len(extra)} verdict(s) for claims no longer in the registry: {extra}")
    if unsupported:
        problems.append(f"{len(unsupported)} claim(s) unsupported by the cited text")
    if discarded:
        problems.append(f"{len(discarded)} verdict(s) discarded for unverified evidence")
    if malformed:
        problems.append(f"{len(malformed)} malformed verdict(s)")

    if problems:
        print("\nFAIL")
        for p in problems:
            print(f"  - {p}")
        return 1

    print("\nPASS: every claim is supported by the text it cites.")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="count tokens and price; generate nothing")
    ap.add_argument("--check", action="store_true", help="read committed verdicts; no network")
    args = ap.parse_args(argv)

    if args.check:
        if not VERDICT_PATH.exists():
            print(f"no verdicts at {VERDICT_PATH.relative_to(ROOT)}; run without --check first",
                  file=sys.stderr)
            return 1
        committed = [
            json.loads(line)
            for line in VERDICT_PATH.read_text().splitlines()
            if line.strip()
        ]
        return _report(committed)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set in the environment.", file=sys.stderr)
        return 2

    found = cases()

    if args.dry_run:
        return _dry_run(found)

    records: list[dict[str, Any]] = [_header()]
    for i, case in enumerate(found, 1):
        print(f"  [{i:>2}/{len(found)}] {case.provision_id} ... ", end="", flush=True)
        try:
            record = _judge(case)
        except Exception as exc:  # one bad call must not lose the whole run
            record = {
                "provision_id": case.provision_id,
                "corpus_key": case.corpus_key,
                "celex": case.celex,
                "subdivision_id": case.subdivision_id,
                "verdict": "MALFORMED",
                "span": "",
                "span_verified": False,
                "span_problem": f"call failed: {type(exc).__name__}",
                "reason": "",
            }
        mark = "ok" if record["verdict"] == CONFIRMED and record["span_verified"] else record["verdict"]
        print(mark)
        records.append(record)

    VERDICT_PATH.parent.mkdir(parents=True, exist_ok=True)
    VERDICT_PATH.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
    print(f"\nwritten to {VERDICT_PATH.relative_to(ROOT)}\n")
    return _report(records)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
