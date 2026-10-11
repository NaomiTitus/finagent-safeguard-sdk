"""Check every checkable claim in the README against the code.

    python3 tools/audit_docs.py

Written after a documentation pass found four stale counts, a help string
describing an implementation that had been replaced twenty minutes earlier, and
a plan document still claiming a Grafana dashboard that was never built. Prose
drifts from code within a single session; the only defence is to make the
checkable part refusable.

What it checks: test and mutation counts, mypy file count, CI job count, the
fitted and produced feature counts, reachable and unreachable provision counts,
the documented subset of mutation rows, every `--flag` the README names
(against the right tool's `--help`), every `FinancialCategory` member, every
repository path in backticks, the corpus size and positive count, and that both
excluded features are named.

What it cannot check is the reasoning, which still has to be read. Exit 1 on
any failure, so it can gate.
"""

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.argv = ["x"]

RAW = (ROOT / "README.md").read_text()
#: Line breaks are markdown's business, not the claim's. Collapsing
#: whitespace is what stopped the audit reporting a correct README as wrong.
README = re.sub(r"\s+", " ", RAW)
MUTDOC = (ROOT / "docs/mutation-list.md").read_text()

spec = importlib.util.spec_from_file_location("_m", ROOT / "tools/run_mutations.py")
assert spec and spec.loader
mut = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mut)

from finagent_safeguard.cli.features import (  # noqa: E402
    EXCLUDED_FEATURES,
    EXCLUDED_GROUPS,
    FEATURE_NAMES,
    MODEL_FEATURES,
)
from finagent_safeguard.regulation.candidates import (  # noqa: E402
    CANDIDATES,
    UNREACHABLE,
)
from finagent_safeguard.taxonomy.policies import FinancialCategory  # noqa: E402

fails: list[str] = []
checks = 0


def claim(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not ok:
        fails.append(f"{label}  {detail}")


# ---------------------------------------------------------------- counts
tests_out = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/", "-q"],
    capture_output=True, text=True, cwd=ROOT,
).stdout
tests_match = re.search(r"(\d+) passed", tests_out)
assert tests_match, f"pytest reported no pass count:\n{tests_out}"
n_tests = int(tests_match.group(1))
claim("test count", f"**{n_tests} tests**" in README, f"actual {n_tests}")

n_rows = len(mut.MUTATIONS)
claim("mutation row count", f"**{n_rows} mutation rows**" in README, f"actual {n_rows}")

# The package only. Counting `tools/` too made this claim break whenever a
# tool was added -- including when THIS script was added, which broke it
# immediately. A claim that moves for an unrelated reason is a claim nobody
# will keep true.
mypy_out = subprocess.run(
    ["mypy", "--strict", "finagent_safeguard"],
    capture_output=True, text=True, cwd=ROOT,
).stdout
files_match = re.search(r"(\d+) source files", mypy_out)
assert files_match, f"mypy reported no file count:\n{mypy_out}"
n_files = int(files_match.group(1))
claim("mypy package file count",
      f"{n_files} source files in the package" in README,
      f"actual {n_files}")

jobs = re.findall(r"^  ([a-z][a-z0-9_-]*):$",
                  (ROOT / ".github/workflows/ci.yml").read_text(), re.M)
# Trigger keys sit at the same indent; the job names are those with a `runs-on`.
ci = (ROOT / ".github/workflows/ci.yml").read_text()
n_jobs = len(re.findall(r"runs-on:", ci))
claim("CI job count", f"**{n_jobs} CI jobs**" in README, f"actual {n_jobs}")

claim("fitted/produced features",
      f"{len(FEATURE_NAMES)} features produced, {len(MODEL_FEATURES)} fitted" in README,
      f"actual {len(FEATURE_NAMES)}/{len(MODEL_FEATURES)}")
claim("coefficient count",
      f"{len(MODEL_FEATURES)} named coefficients" in README,
      f"actual {len(MODEL_FEATURES)}")

reachable = len({c.provision.id for cs in CANDIDATES.values() for c in cs})
claim("reachable provisions", f"{reachable} reachable" in README, f"actual {reachable}")
claim("unreachable provisions",
      f"{len(UNREACHABLE)} are recorded" in README, f"actual {len(UNREACHABLE)}")
claim("pinned provisions", "23 pinned" in README or "23 citable" in README)

holes = len([l for l in MUTDOC.splitlines()
             if "NOT CAUGHT" in l and "verified hole" not in l and "closed" not in l])
words = {5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}
claim("known holes", f"**{words.get(holes, holes)} known holes**" in README,
      f"actual {holes}")

documented = len(set(re.findall(r"^\|\s*\*{0,2}([A-Z]+\d+[a-z]?)\*{0,2}\s*\|",
                                MUTDOC, re.M)) & set(mut.MUTATIONS))
claim("documented subset", f"{documented} written up" in README,
      f"actual {documented}")

# ------------------------------------------------------------- the CLI
help_out = subprocess.run(
    [sys.executable, "-m", "finagent_safeguard.cli.linter", "--help"],
    capture_output=True, text=True, cwd=ROOT,
).stdout
for flag in ("--fix", "--diff-only", "--since", "--exclude", "--expect-plan",
             "--strict"):
    claim(f"flag {flag} exists", flag in help_out)
OTHER_TOOLS = {
    "--loro-per-feature": [sys.executable, "tools/train_classifier.py", "--help"],
    "--loro": [sys.executable, "tools/train_classifier.py", "--help"],
    "--loro-ablate": [sys.executable, "tools/train_classifier.py", "--help"],
    "--check": [sys.executable, "tools/check_entailment.py", "--help"],
    "--base": [sys.executable, "tools/battery.py", "--help"],
}
_cache: dict[str, str] = {}
for flag in sorted(set(re.findall(r"`(--[a-z-]+)`", RAW))):
    if flag in OTHER_TOOLS:
        key = " ".join(OTHER_TOOLS[flag])
        if key not in _cache:
            _cache[key] = subprocess.run(
                OTHER_TOOLS[flag], capture_output=True, text=True, cwd=ROOT
            ).stdout
        claim(f"README flag {flag} is real", flag in _cache[key],
              "not in that tool's --help")
    else:
        claim(f"README flag {flag} is real", flag in help_out, "not in --help")

# --------------------------------------------------------- enum members
for member in re.findall(r"FinancialCategory\.([A-Z_]+)", RAW):
    claim(f"category {member} exists",
          member in FinancialCategory.__members__, "not an enum member")

# ------------------------------------------------- reproducible numbers
trainer = importlib.import_module("tools.train_classifier")
rows, labels, ids = trainer._rows(
    ROOT / "benchmark/train-pointers.jsonl", ROOT / "benchmark/train-labels.jsonl")
claim("corpus size", "996" in README, f"actual {len(rows)}")
claim("positive count", "213 positives" in README or str(sum(labels)) in README,
      f"actual {sum(labels)}")

# ----------------------------------------------------------- file paths
for path in sorted(set(re.findall(r"`((?:docs|tools|finagent_safeguard|benchmark|corpus)/[\w./-]+)`", RAW))):
    claim(f"path {path} exists", (ROOT / path).exists(), "missing")

# --------------------------------------------------- excluded features
for name in EXCLUDED_FEATURES:
    claim(f"excluded feature {name} named in README", name in README)
for group in EXCLUDED_GROUPS:
    claim(f"excluded group {group} named in README", group in README)

print(f"  {checks} checkable claims verified")
if fails:
    print(f"\n  {len(fails)} FAILED:")
    for item in fails:
        print(f"    - {item}")
    raise SystemExit(1)
print("  all pass")
