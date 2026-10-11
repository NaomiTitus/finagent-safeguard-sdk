#!/usr/bin/env python3
"""Have several models label the benchmark, and score them against the human.

    python3 tools/llm_label.py --dry-run   # tokens and cost, no spend
    python3 tools/llm_label.py             # run it, write the judgements
    python3 tools/llm_label.py --score     # score committed judgements, no network

The question this answers is not "can a model do this". It is **where** a model
can do this, which is a different and more useful question. If the items a model
is unanimous about agree with the human far better than the items it splits on,
then a model can label the unanimous ones and a human takes the rest -- which is
what would make a larger corpus defensible rather than merely hopeful. Typilus
reached 70% coverage at 95% precision on exactly that shape, and 70-at-95 is
worth more than 100-at-70.

Three runs per model per item, not two. Two runs can detect disagreement but
cannot resolve it: a binary label ties half the time it differs. Three is the
smallest number with a mode, and the 3-0 versus 2-1 split is a confidence
signal that costs nothing extra.

The human labels are the gold standard
--------------------------------------
Models are scored against ``benchmark/labels.jsonl``, never the reverse, and the
ceiling is the human's own self-agreement -- Cohen's kappa 0.762. A model
reaching 0.76 is indistinguishable from a second competent annotator. One
reaching 0.95 would be suspicious rather than excellent, and the first thing to
check would be contamination.

Cross-model agreement is reported but is the *least* informative figure here:
three Claude models share training lineage, so agreeing with each other is
cheap. Agreement with the human is the measure.

Contamination
-------------
The rubric sent to the models is
``finagent_safeguard/regulation/prompts/labelling_rubric.txt``, which states the
rule in the abstract and names no example functions. ``docs/labelling-
decisions.md`` names about eight worked examples *with their answers*, and every
one is in the corpus -- sending it would leak the answers to precisely the items
that discriminate. So the models get a weaker brief than the human had, which
makes this a harder test and a stronger result if they pass.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

API = "https://api.anthropic.com/v1"
API_VERSION = "2023-06-01"

RUBRIC = ROOT / "finagent_safeguard/regulation/prompts/labelling_rubric.txt"
POINTERS = ROOT / "benchmark/pointers.jsonl"
LABELS = ROOT / "benchmark/labels.jsonl"
OUT = ROOT / "benchmark/llm-labels.jsonl"

SOURCES = Path(
    "/private/tmp/claude-501/-Users-naomi-Documents-projects-finagent-safeguard-sdk"
    "/821d6a65-f513-48f1-b0c4-309ae435eb16/scratchpad/corpus-src"
)

JUDGES = ("claude-opus-5", "claude-sonnet-5", "claude-haiku-5-5")
RUNS = 3
WORKERS = 8

#: $/MTok (input, output). Opus and Sonnet verified against the pricing page
#: 2026-10-04. Haiku 5.5 is UNVERIFIED -- released 2026-10-07, the models
#: endpoint exposes no pricing, and the web search budget is spent; this is
#: Haiku 4.5's rate as a placeholder. Token counts reported are exact.
PRICES: dict[str, tuple[Decimal, Decimal]] = {
    "claude-opus-5": (Decimal("5"), Decimal("25")),
    "claude-sonnet-5": (Decimal("2"), Decimal("10")),
    "claude-haiku-5-5": (Decimal("1"), Decimal("5")),
}
#: Generous because the first run set this to 300 and lost 96 of 1,440
#: judgements to truncation -- valid JSON cut off before its closing brace. The
#: loss was not random: longer explanations mean harder items, so the dropped
#: judgements were biased towards exactly the cases that discriminate.
MAX_TOKENS = 700

#: Landis & Koch bands, as elsewhere in this repository.
BANDS = ((0.81, "near-perfect"), (0.61, "substantial"), (0.41, "moderate"),
         (0.21, "fair"), (0.00, "slight"))

#: The human's agreement with themselves, from tools/label_stats.py. Printed
#: beside every figure, because the same number means opposite things above and
#: below it.
HUMAN_CEILING = 0.762


def band(value: float) -> str:
    for floor, name in BANDS:
        if value >= floor:
            return name
    return "worse than chance"


def kappa(pairs: list[tuple[bool, bool]]) -> tuple[float, float]:
    """Cohen's kappa and raw agreement over (a, b) pairs."""
    n = len(pairs)
    if not n:
        return 0.0, 0.0
    observed = sum(a == b for a, b in pairs) / n
    pa = sum(a for a, _ in pairs) / n
    pb = sum(b for _, b in pairs) / n
    expected = pa * pb + (1 - pa) * (1 - pb)
    if expected == 1.0:
        # Both sides answered one way throughout: nothing was demonstrated.
        return 0.0, observed
    return (observed - expected) / (1 - expected), observed


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------
def _items() -> list[dict[str, Any]]:
    import sysconfig

    stdlib = Path(sysconfig.get_paths()["stdlib"])
    out: list[dict[str, Any]] = []
    for line in POINTERS.read_text().splitlines():
        if not line.strip():
            continue
        p = json.loads(line)
        base = stdlib if p["repo"] == "cpython-stdlib" else SOURCES / p["repo"]
        path = base / p["path"]
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            source = "\n".join(lines[p["first_line"] - 1 : p["last_line"]])
        except OSError:
            continue
        out.append({**p, "source": source})
    return out


def _human() -> dict[str, bool]:
    return {
        str(r["item_id"]): bool(r["regulated"])
        for r in (
            json.loads(line)
            for line in LABELS.read_text().splitlines()
            if line.strip()
        )
        if r.get("pass") != 2
    }


# --------------------------------------------------------------------------
# transport (stdlib; the vendor SDK cannot reach the API from here)
# --------------------------------------------------------------------------
def _post(body: dict[str, Any], *, attempts: int = 5) -> dict[str, Any]:
    import time
    import urllib.error
    import urllib.request

    last: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(
            f"{API}/messages",
            data=json.dumps(body).encode(),
            headers={
                "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                "anthropic-version": API_VERSION,
                "content-type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                loaded = json.load(response)
            if not isinstance(loaded, dict):
                raise ValueError("response is not an object")
            return loaded
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 529):
                raise RuntimeError(
                    f"HTTP {exc.code}: {exc.read()[:200].decode('utf-8', 'replace')}"
                ) from exc
            last = exc
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            last = exc
        if attempt < attempts - 1:
            time.sleep(2**attempt)
    raise RuntimeError(f"gave up after {attempts}: {last}")


def _ask(item: dict[str, Any], model: str, run: int) -> dict[str, Any]:
    """One judgement. Failures are recorded, never raised.

    A dropped call must not vanish: an absent judgement would turn a 2-1 split
    into a 2-0 unanimity and inflate the confidence signal.
    """
    base = {"item_id": item["item_id"], "model": model, "run": run}
    payload = (
        f"Function: {item['function']}\n"
        f"File: {item['path']}\n\n"
        f"```python\n{item['source'][:6000]}\n```"
    )
    try:
        reply = _post(
            {
                "model": model,
                "max_tokens": MAX_TOKENS,
                "system": RUBRIC.read_text(),
                "messages": [{"role": "user", "content": payload}],
            }
        )
        text = "".join(
            b.get("text", "") for b in reply["content"] if b.get("type") == "text"
        )
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            parsed = json.loads(text[start : end + 1])
            regulated = bool(parsed["regulated"])
            why = str(parsed.get("why", ""))
        else:
            # Truncated before the closing brace. The verdict is the first
            # field the rubric asks for, so it survives even when the
            # explanation does not -- and discarding a judgement we can read
            # would throw away the hard items preferentially.
            import re

            found = re.search(r'"regulated"\s*:\s*(true|false)', text)
            if not found:
                raise ValueError(f"no verdict in reply: {text[:160]!r}")
            regulated = found.group(1) == "true"
            why = "(truncated) " + text[text.find('"why"') :][:200]
        return {
            **base,
            "regulated": regulated,
            "why": why[:300],
            "input_tokens": reply["usage"]["input_tokens"],
            "output_tokens": reply["usage"]["output_tokens"],
        }
    except Exception as exc:
        return {
            **base,
            "regulated": None,
            "why": f"FAILED {type(exc).__name__}: {exc}"[:200],
            "input_tokens": 0,
            "output_tokens": 0,
        }


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------
def _score(records: list[dict[str, Any]]) -> int:
    human = _human()
    by_model: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    for r in records:
        if r.get("regulated") is None:
            continue
        by_model[str(r["model"])][str(r["item_id"])].append(bool(r["regulated"]))

    failures = sum(1 for r in records if r.get("regulated") is None)
    print(f"judgements          {len(records)}   failed {failures}")
    print(f"human ceiling       kappa {HUMAN_CEILING:.3f}  (the number to read everything against)\n")

    print(f"{'model':<20} {'kappa vs human':>15} {'raw':>7} {'self-kappa':>11} {'prec':>7} {'rec':>7}")
    print("-" * 74)
    modes: dict[str, dict[str, bool]] = {}
    for model in JUDGES:
        votes = by_model.get(model)
        if not votes:
            continue
        mode = {i: sum(v) * 2 > len(v) for i, v in votes.items()}
        modes[model] = mode
        shared = [i for i in mode if i in human]
        k, raw = kappa([(human[i], mode[i]) for i in shared])

        # Self-agreement: run 1 against run 2, the same comparison the human's
        # second pass made, so the two ceilings are directly comparable.
        pairs = [
            (v[0], v[1]) for v in votes.values() if len(v) >= 2
        ]
        self_k, _ = kappa(pairs)

        tp = sum(1 for i in shared if human[i] and mode[i])
        fp = sum(1 for i in shared if not human[i] and mode[i])
        fn = sum(1 for i in shared if human[i] and not mode[i])
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        print(
            f"{model:<20} {k:>15.3f} {raw:>6.0%} {self_k:>11.3f} {prec:>6.0%} {rec:>6.0%}"
        )

    # The question that decides whether a larger corpus is defensible.
    print("\nselective prediction -- agreement where the model is unanimous")
    print(f"{'model':<20} {'unanimous':>10} {'agree':>7} {'split':>7} {'agree':>7}")
    print("-" * 56)
    for model in JUDGES:
        votes = by_model.get(model)
        if not votes:
            continue
        unanimous = [i for i, v in votes.items() if len(set(v)) == 1 and i in human]
        split = [i for i, v in votes.items() if len(set(v)) > 1 and i in human]
        ua = sum(1 for i in unanimous if human[i] == votes[i][0]) / len(unanimous) if unanimous else 0.0
        sa = (
            sum(1 for i in split if human[i] == (sum(votes[i]) * 2 > len(votes[i])))
            / len(split)
            if split
            else 0.0
        )
        print(f"{model:<20} {len(unanimous):>10} {ua:>6.0%} {len(split):>7} {sa:>6.0%}")

    if len(modes) > 1:
        print("\ncross-model (reported, but the least informative figure here)")
        names = list(modes)
        for position, a in enumerate(names):
            for b in names[position + 1 :]:
                shared = [x for x in modes[a] if x in modes[b]]  # noqa: E501
                k, _ = kappa([(modes[a][x], modes[b][x]) for x in shared])
                print(f"  {a} vs {b}: kappa {k:.3f} ({band(k)})")

    # Where every model disagrees with the human is where the question is hard,
    # not where the models are weak.
    if len(modes) == len(JUDGES):
        all_wrong = [
            i
            for i in human
            if all(i in modes[m] and modes[m][i] != human[i] for m in JUDGES)
        ]
        print(f"\nall three disagreed with the human on {len(all_wrong)} item(s)")
        pointers = {
            str(json.loads(l)["item_id"]): json.loads(l)
            for l in POINTERS.read_text().splitlines()
            if l.strip()
        }
        flagged = {
            str(r["item_id"])
            for r in (
                json.loads(l) for l in LABELS.read_text().splitlines() if l.strip()
            )
            if r.get("flagged")
        }
        for i in sorted(all_wrong):
            mark = " [human flagged this as hard]" if i in flagged else ""
            print(f"  human={human[i]!s:<5} {i:<22} {pointers[i]['function'][:40]}{mark}")
    return 0


# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--runs", type=int, default=RUNS)
    args = ap.parse_args(argv)

    if args.score:
        if not OUT.exists():
            print(f"no judgements at {OUT.relative_to(ROOT)}", file=sys.stderr)
            return 1
        return _score(
            [json.loads(l) for l in OUT.read_text().splitlines() if l.strip()]
        )

    items = _items()
    if len(items) < 150:
        print(
            f"only resolved {len(items)} items; run tools/harvest_corpus.py --clone",
            file=sys.stderr,
        )
        return 2

    if args.dry_run:
        rubric = len(RUBRIC.read_text()) // 4
        body = sum(len(i["source"][:6000]) for i in items) // 4
        total = Decimal(0)
        print(f"items {len(items)}   judges {len(JUDGES)}   runs {args.runs}")
        print(f"calls {len(items) * len(JUDGES) * args.runs}\n")
        for model in JUDGES:
            pin, pout = PRICES[model]
            tin = (rubric * len(items) + body) * args.runs
            tout = len(items) * args.runs * 60
            cost = (Decimal(tin) * pin + Decimal(tout) * pout) / Decimal(1_000_000)
            total += cost
            print(f"  {model:<20} ~{tin:>8,} in  ~{tout:>6,} out   ~${cost:.2f}")
        print(f"\nestimate  ~${total:.2f}  (token counts approximate; nothing spent)")
        return 0

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 2

    work = [
        (item, model, run)
        for item in items
        for model in JUDGES
        for run in range(1, args.runs + 1)
    ]
    print(f"{len(work)} calls\n")
    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(_ask, i, m, r) for i, m, r in work]
        for n, future in enumerate(as_completed(futures), 1):
            records.append(future.result())
            if n % 100 == 0 or n == len(work):
                print(f"  {n}/{len(work)}")

    records.sort(key=lambda r: (str(r["item_id"]), str(r["model"]), int(r["run"])))
    OUT.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    )
    spend: Counter[str] = Counter()
    for r in records:
        spend[str(r["model"]) + ":in"] += int(r["input_tokens"])
        spend[str(r["model"]) + ":out"] += int(r["output_tokens"])
    actual = sum(
        (Decimal(spend[m + ":in"]) * PRICES[m][0]
         + Decimal(spend[m + ":out"]) * PRICES[m][1]) / Decimal(1_000_000)
        for m in JUDGES
    )
    print(f"\nwritten to {OUT.relative_to(ROOT)}")
    print(f"actual cost ~${actual:.2f} (Haiku rate unverified)\n")
    return _score(records)


if __name__ == "__main__":
    raise SystemExit(main())
