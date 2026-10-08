#!/usr/bin/env python3
"""Audit every registry claim against the verbatim text it cites.

PLAN.md 5.4 designed this gate and it was never run, so the regulatory tags --
the core of the project -- are currently one person's unchecked reading.

    python3 tools/check_entailment.py --dry-run    # tokens and cost, no spend
    python3 tools/check_entailment.py              # run it, write verdicts
    python3 tools/check_entailment.py --check      # read committed verdicts only

``--check`` makes no network call and is the form CI would use: it fails if a
verdict is missing, stale, unsupported, or not unanimous.

Why a model is allowed to judge this at all
-------------------------------------------
Because it is not trusted, in three separate ways.

1. *The quote is the oracle.* The rubric requires every verdict to carry a span
   copied character-for-character from the provision, and ``verify_span``
   discards any verdict whose span is not an exact substring. A fluent verdict
   resting on a paraphrase is thrown away, not downgraded.

2. *One run is a coin toss.* Temperature does not buy determinism -- a thousand
   temperature-zero completions have been measured producing eighty distinct
   outputs, caused by batching in the serving stack and unfixable from here. So
   each claim is judged several times, and a single confirmation counts for
   nothing.

3. *One model's mistakes are correlated.* The same model erring the same way
   three times is indistinguishable from unanimity. A second model from a
   different line breaks that correlation. It is a screen and not a proof --
   both models share training data and can be wrong together -- so the span
   check remains the only real oracle, and a human remains the authority on
   everything flagged.

The asymmetry that sets the decision rule
-----------------------------------------
A false CONFIRMED certifies a wrong regulatory tag and ships it. A false
CLAIM_UNSUPPORTED costs somebody a few minutes' reading. So confirming is made
hard and flagging is made easy: a claim is confirmed only when every judgement
confirms it *and* every judgement quotes the same span. Agreement on the answer
without agreement on the evidence means the claim is vaguely supported rather
than specifically supported, which for a legal reading is the whole question.

Credentials
-----------
Reads ``ANTHROPIC_API_KEY`` from the environment. Never printed, never logged,
never written to the verdict file.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
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

API = "https://api.anthropic.com/v1"
API_VERSION = "2023-06-01"

DEFAULT_JUDGES = ("claude-opus-5", "claude-sonnet-5")
DEFAULT_RUNS = 3
WORKERS = 6

#: $/MTok (input, output), verified against the live pricing page 2026-10-04.
#: PLAN.md 5.4.1 quotes 4/20 for Opus, which is stale.
PRICES: dict[str, tuple[Decimal, Decimal]] = {
    "claude-opus-5": (Decimal("5"), Decimal("25")),
    "claude-sonnet-5": (Decimal("2"), Decimal("10")),
}
OUTPUT_PER_CALL = 2000

REVIEW = "REVIEW_REQUIRED"
MALFORMED = "MALFORMED"

#: Appended to each payload. Kept out of the rubric deliberately -- the rubric
#: is hashed into every verdict, and a transport detail changing that hash
#: would invalidate prior verdicts for no substantive reason.
OUTPUT_CONTRACT = """

Reply with a single JSON object and nothing else:
{"verdict": "CONFIRMED" or "CLAIM_UNSUPPORTED",
 "span": "<contiguous span copied character-for-character from PROVISION>",
 "reason": "<one or two sentences>"}
"""


# --------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------
def _post(path: str, body: dict[str, Any], *, attempts: int = 5) -> dict[str, Any]:
    """POST to the Messages API using the standard library.

    The vendor SDK's HTTP client cannot reach the API from this environment
    (``APIConnectionError``) while ``urllib`` and ``curl`` both can, with the
    same key against the same host. The stdlib is the better answer on
    principle too: this project is stdlib-only by design, and an audit of its
    regulatory core should not need a third-party package to be reproducible by
    whoever reads the repository.

    Retries the transient classes only -- 429 and 5xx, connection errors,
    timeouts -- with exponential backoff. A 400 or 401 is a bug or a bad key,
    and retrying would only spend five times as long failing.
    """
    import time
    import urllib.error
    import urllib.request

    last: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(
            f"{API}/{path}",
            data=json.dumps(body).encode(),
            headers={
                "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                "anthropic-version": API_VERSION,
                "content-type": "application/json",
            },
        )
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


def _count_tokens(model: str, system: str, payload: str) -> int:
    counted = _post(
        "messages/count_tokens",
        {
            "model": model,
            "system": system,
            "messages": [{"role": "user", "content": payload}],
        },
    )
    return int(counted["input_tokens"])


# --------------------------------------------------------------------------
# judging
# --------------------------------------------------------------------------
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


def _judge_once(case: Case, model: str, run: int) -> dict[str, Any]:
    """One judgement. Failures are recorded, never raised.

    A dead call must not lose the other judgements of the same claim, and must
    not be silently absent either: an absent judgement would make a two-of-three
    agreement look unanimous.
    """
    base: dict[str, Any] = {"model": model, "run": run}
    try:
        reply = _post(
            "messages",
            {
                "model": model,
                "max_tokens": OUTPUT_PER_CALL,
                "system": rubric(),
                "messages": [
                    {"role": "user", "content": case.payload() + OUTPUT_CONTRACT}
                ],
            },
        )
        text = "".join(
            block.get("text", "")
            for block in reply["content"]
            if block.get("type") == "text"
        )
        parsed = _parse(text)
        verdict = parsed.get("verdict", "")
        span = parsed.get("span", "")
        ok, why = verify_span(span, case.provision_text)
        return {
            **base,
            "verdict": verdict if verdict in (CONFIRMED, UNSUPPORTED) else MALFORMED,
            "span": span,
            "span_verified": ok,
            "span_problem": why,
            "reason": parsed.get("reason", ""),
            "input_tokens": reply["usage"]["input_tokens"],
            "output_tokens": reply["usage"]["output_tokens"],
        }
    except Exception as exc:
        return {
            **base,
            "verdict": MALFORMED,
            "span": "",
            "span_verified": False,
            "span_problem": f"{type(exc).__name__}: {exc}"[:200],
            "reason": "",
            "input_tokens": 0,
            "output_tokens": 0,
        }


def aggregate(judgements: list[dict[str, Any]]) -> dict[str, str]:
    """Collapse several judgements of one claim into one decision.

    Confirming requires unanimity *and* an identical span from every judge.
    Everything else is UNSUPPORTED or REVIEW_REQUIRED, both of which cost a
    human a few minutes; a wrongly confirmed claim costs a false compliance
    record in somebody's source. The rule is deliberately lopsided.

    Pure, so the decision rule is testable without the network.
    """
    if not judgements:
        return {"verdict": MALFORMED, "why": "no judgements"}

    total = len(judgements)
    verdicts = Counter(str(j["verdict"]) for j in judgements)
    spans = {str(j["span"]) for j in judgements if j["span_verified"]}
    unverified = [j for j in judgements if not j["span_verified"]]

    if verdicts[UNSUPPORTED]:
        return {
            "verdict": UNSUPPORTED,
            "why": f"{verdicts[UNSUPPORTED]} of {total} judgements found the text does not support the claim",
        }
    if verdicts[MALFORMED]:
        return {
            "verdict": REVIEW,
            "why": f"{verdicts[MALFORMED]} of {total} judgements were malformed or failed",
        }
    if unverified:
        return {
            "verdict": REVIEW,
            "why": f"{len(unverified)} of {total} judgements quoted evidence that is not in the text",
        }
    if len(spans) > 1:
        return {
            "verdict": REVIEW,
            "why": (
                f"all {total} judgements confirmed but cited {len(spans)} different spans; "
                "the claim is vaguely supported, not specifically supported"
            ),
        }
    return {
        "verdict": CONFIRMED,
        "why": f"unanimous across {total} judgements, all on one span",
    }


def cross_model(judgements: list[dict[str, Any]]) -> dict[str, str]:
    """Per-model verdicts for one claim, to expose correlated error.

    Reported rather than acted on: if two models split, that is worth a human's
    attention regardless of what the aggregate says.
    """
    by_model: dict[str, list[str]] = {}
    for j in judgements:
        by_model.setdefault(str(j["model"]), []).append(str(j["verdict"]))
    out: dict[str, str] = {}
    for model, seen in by_model.items():
        unique = sorted(set(seen))
        out[model] = unique[0] if len(unique) == 1 else "SPLIT:" + ",".join(unique)
    return out


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------
def _dry_run(found: list[Case], judges: tuple[str, ...], runs: int) -> int:
    rubric_text = rubric()
    total = Decimal(0)
    print(f"claims            {len(found)}")
    print(f"judges            {', '.join(judges)}")
    print(f"runs per judge    {runs}")
    print(f"calls             {len(found) * len(judges) * runs}\n")

    for model in judges:
        tokens = sum(
            _count_tokens(model, rubric_text, c.payload() + OUTPUT_CONTRACT)
            for c in found
        )
        in_price, out_price = PRICES[model]
        out_tokens = len(found) * OUTPUT_PER_CALL
        cost = (
            (Decimal(tokens) * in_price + Decimal(out_tokens) * out_price)
            * runs
            / Decimal(1_000_000)
        )
        total += cost
        print(f"  {model:<20} {tokens:>7,} in x{runs} runs    ceiling ${cost:.2f}")

    print(f"\nupper-bound cost  ${total:.2f}")
    print("  The output budget assumes every call uses its full 2,000 tokens.")
    print("  The rubric ends 'Be brief', so expect well under this.")
    print("\nNothing was generated and nothing was charged beyond token counting.")
    return 0


def _report(records: list[dict[str, Any]]) -> int:
    header = next((r for r in records if r.get("record") == "run_header"), None)
    claims = [r for r in records if r.get("record") != "run_header"]

    if header is None:
        print("verdict file has no run header; provenance unknown", file=sys.stderr)
        return 1

    problems: list[str] = []
    if header.get("rubric_sha256") != rubric_digest():
        problems.append("the rubric changed since these verdicts were produced")
    if header.get("corpus_digest") != corpus_digest():
        problems.append("the pinned corpus changed since these verdicts were produced")

    expected = {c.provision_id for c in cases()}
    judged = {str(r["provision_id"]) for r in claims}
    if unjudged := sorted(expected - judged):
        problems.append(f"{len(unjudged)} claim(s) never judged: {unjudged}")
    if extra := sorted(judged - expected):
        problems.append(f"{len(extra)} verdict(s) for claims not in the registry: {extra}")

    buckets: dict[str, list[dict[str, Any]]] = {}
    for record in claims:
        buckets.setdefault(str(record["verdict"]), []).append(record)

    judges = header.get("judges", [])
    print(f"judges            {', '.join(judges)}")
    print(f"runs per judge    {header.get('runs')}")
    print(f"ran at            {header.get('ran_at')}")
    print(f"claims            {len(claims)} of {len(expected)}\n")
    for name in (CONFIRMED, UNSUPPORTED, REVIEW, MALFORMED):
        print(f"  {name:<20} {len(buckets.get(name, []))}")

    splits = [
        r
        for r in claims
        if any("SPLIT" in str(v) for v in dict(r.get("cross_model", {})).values())
    ]
    disagreed = [
        r
        for r in claims
        if len({str(v) for v in dict(r.get("cross_model", {})).values()}) > 1
    ]
    print(f"\n  a model split with itself on   {len(splits)} claim(s)")
    print(f"  the two models disagreed on    {len(disagreed)} claim(s)")

    for label, name in (
        ("CLAIMS THE TEXT DOES NOT SUPPORT", UNSUPPORTED),
        ("NEEDS A HUMAN", REVIEW),
        ("MALFORMED", MALFORMED),
    ):
        rows = buckets.get(name, [])
        if not rows:
            continue
        print(f"\n{'=' * 78}\n{label}  ({len(rows)})\n")
        for record in rows:
            print(f"  {record['provision_id']}   [{record['corpus_key']}]")
            print(f"    {record['why']}")
            print(f"    per model: {record.get('cross_model')}")
            for j in record.get("judgements", []):
                if j["verdict"] != CONFIRMED or not j["span_verified"]:
                    note = str(j.get("reason") or j.get("span_problem") or "")
                    print(f"      {j['model']} run{j['run']}: {j['verdict']} - {note[:160]}")
            print()

    if buckets.get(UNSUPPORTED):
        problems.append(f"{len(buckets[UNSUPPORTED])} claim(s) unsupported by the cited text")
    if buckets.get(REVIEW):
        problems.append(f"{len(buckets[REVIEW])} claim(s) need a human")
    if buckets.get(MALFORMED):
        problems.append(f"{len(buckets[MALFORMED])} malformed")

    if problems:
        print("FAIL")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("\nPASS: every claim unanimously supported, each on a single agreed span.")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="count tokens and price; generate nothing")
    ap.add_argument("--check", action="store_true", help="read committed verdicts; no network")
    ap.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="judgements per model")
    ap.add_argument("--judges", default=",".join(DEFAULT_JUDGES), help="comma-separated models")
    args = ap.parse_args(argv)

    if args.check:
        if not VERDICT_PATH.exists():
            print(
                f"no verdicts at {VERDICT_PATH.relative_to(ROOT)}; run without --check first",
                file=sys.stderr,
            )
            return 1
        committed = [
            json.loads(line)
            for line in VERDICT_PATH.read_text().splitlines()
            if line.strip()
        ]
        return _report(committed)

    judges = tuple(j.strip() for j in str(args.judges).split(",") if j.strip())
    if unknown := [j for j in judges if j not in PRICES]:
        print(f"no price recorded for {unknown}; add it to PRICES first", file=sys.stderr)
        return 2
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set in the environment.", file=sys.stderr)
        return 2

    found = cases()
    if args.dry_run:
        return _dry_run(found, judges, args.runs)

    work = [
        (case, model, run)
        for case in found
        for model in judges
        for run in range(1, args.runs + 1)
    ]
    print(f"{len(work)} calls: {len(found)} claims x {len(judges)} judges x {args.runs} runs\n")

    collected: dict[str, list[dict[str, Any]]] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {
            pool.submit(_judge_once, case, model, run): case.provision_id
            for case, model, run in work
        }
        for future in as_completed(futures):
            collected.setdefault(futures[future], []).append(future.result())
            done += 1
            if done % 10 == 0 or done == len(work):
                print(f"  {done}/{len(work)}")

    records: list[dict[str, Any]] = [
        {
            "record": "run_header",
            "judges": list(judges),
            "runs": args.runs,
            "rubric_sha256": rubric_digest(),
            "corpus_digest": corpus_digest(),
            "ran_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        }
    ]
    for case in found:
        judgements = sorted(
            collected.get(case.provision_id, []),
            key=lambda j: (str(j["model"]), int(j["run"])),
        )
        decision = aggregate(judgements)
        records.append(
            {
                "provision_id": case.provision_id,
                "corpus_key": case.corpus_key,
                "celex": case.celex,
                "subdivision_id": case.subdivision_id,
                "verdict": decision["verdict"],
                "why": decision["why"],
                "cross_model": cross_model(judgements),
                "judgements": judgements,
            }
        )

    VERDICT_PATH.parent.mkdir(parents=True, exist_ok=True)
    VERDICT_PATH.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    )

    spend_in = sum(int(j["input_tokens"]) for r in records[1:] for j in r["judgements"])
    spend_out = sum(int(j["output_tokens"]) for r in records[1:] for j in r["judgements"])
    print(f"\nactual tokens: {spend_in:,} in / {spend_out:,} out")
    print(f"written to {VERDICT_PATH.relative_to(ROOT)}\n")
    return _report(records)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
