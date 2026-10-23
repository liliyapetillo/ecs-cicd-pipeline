# Turns a promptfoo output file into the results table in README.md.
# Appends the run to evals/results/history.json, then rewrites the README block
# between the <!-- evals:start --> and <!-- evals:end --> markers.
# CI runs this after the staging eval and commits both files back to main.
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

START, END = "<!-- evals:start -->", "<!-- evals:end -->"
HEAD_START, HEAD_END = "<!-- evals-headline:start -->", "<!-- evals-headline:end -->"
CATEGORIES = ["positive", "negative", "edge", "redteam"]
HISTORY_LIMIT = 20  # runs kept in history.json
HISTORY_SHOWN = 10  # runs shown in the README


def summarize(output, sha):
    """Reduce a promptfoo output file to the numbers worth keeping."""
    results = output["results"]["results"]
    by_category = defaultdict(lambda: {"passed": 0, "total": 0})
    failing = Counter()
    for r in results:
        category = r["vars"].get("category", "other")
        by_category[category]["total"] += 1
        if r["success"]:
            by_category[category]["passed"] += 1
        else:
            failing[(category, r["vars"]["question"])] += 1
    stats = output["results"]["stats"]
    timestamp = output["results"].get("timestamp") or datetime.now(UTC).isoformat()
    return {
        "date": timestamp[:10],
        "sha": sha[:7],
        "total": len(results),
        "passed": stats["successes"],
        "failed": stats["failures"],
        "errors": stats["errors"],
        "categories": dict(by_category),
        "failing": [{"category": c, "question": q, "times": n} for (c, q), n in failing.items()],
    }


def update_history(path, run):
    runs = json.loads(path.read_text()) if path.exists() else []
    runs.append(run)
    runs = runs[-HISTORY_LIMIT:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(runs, indent=2) + "\n")
    return runs


def pct(passed, total):
    return f"{round(100 * passed / total)}%" if total else "n/a"


def render(runs):
    latest = runs[-1]
    ordered = CATEGORIES + [c for c in latest["categories"] if c not in CATEGORIES]
    lines = [
        f"Latest run: commit `{latest['sha']}`, {latest['date']}, against staging. "
        f"**{latest['passed']}/{latest['total']} passed ({pct(latest['passed'], latest['total'])})**"
        + (f", {latest['errors']} errored (infrastructure, not model quality)." if latest["errors"] else "."),
        "",
        "| Category | Passed | Rate |",
        "|---|---|---|",
    ]
    for name in ordered:
        c = latest["categories"].get(name)
        if c:
            lines.append(f"| {name} | {c['passed']}/{c['total']} | {pct(c['passed'], c['total'])} |")
    if latest["failing"]:
        lines += ["", "Failed in the latest run:", ""]
        lines += [f"- ({f['category']}, {f['times']}x) {f['question']}" for f in latest["failing"]]
    lines += ["", "| Date | Commit | Passed | Rate |", "|---|---|---|---|"]
    for run in reversed(runs[-HISTORY_SHOWN:]):
        rate = pct(run["passed"], run["total"])
        lines.append(f"| {run['date']} | `{run['sha']}` | {run['passed']}/{run['total']} | {rate} |")
    return "\n".join(lines)


def headline(runs):
    latest = runs[-1]
    return (
        f"**Latest eval run: {latest['passed']}/{latest['total']} passed "
        f"({pct(latest['passed'], latest['total'])}) on commit `{latest['sha']}`, {latest['date']}.**"
    )


def replace_between(text, start, end, block):
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    if not pattern.search(text):
        raise ValueError(f"README is missing the {start} / {end} markers")
    return pattern.sub(lambda _: f"{start}\n{block}\n{end}", text)


def update_readme(readme_text, runs):
    readme_text = replace_between(readme_text, START, END, render(runs))
    return replace_between(readme_text, HEAD_START, HEAD_END, headline(runs))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path, help="promptfoo --output JSON file")
    parser.add_argument("--sha", required=True)
    parser.add_argument("--history", type=Path, default=Path("evals/results/history.json"))
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    args = parser.parse_args()

    run = summarize(json.loads(args.output.read_text()), args.sha)
    runs = update_history(args.history, run)
    args.readme.write_text(update_readme(args.readme.read_text(), runs))
    print(f"Recorded {run['passed']}/{run['total']} for {run['sha']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
