# Turns a promptfoo output file into the live eval section of README.md.
# Appends the run to evals/results/history.json, writes the shields.io badge data to
# evals/results/badge.json, then rewrites the README blocks between the
# <!-- evals:start --> / <!-- evals:end --> and headline markers.
# CI runs this after the staging eval and commits all three files back to main.
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
# A grader call that failed (no permission, throttled) is an infrastructure problem,
# not a wrong answer, but promptfoo reports it as a failed assertion with this text.
GRADER_ERROR = re.compile(r"API invoke model error|AccessDeniedException|ThrottlingException")


def is_infra_error(result):
    """True if the result failed for infrastructure reasons, not answer quality."""
    if result.get("failureReason") == 2:  # promptfoo's ERROR: the app call itself failed
        return True
    components = (result.get("gradingResult") or {}).get("componentResults", [])
    return any(not c.get("pass") and GRADER_ERROR.search(c.get("reason") or "") for c in components)


def summarize(output, sha, run_url=None):
    """Reduce a promptfoo output file to the numbers worth keeping."""
    results = output["results"]["results"]
    by_category = defaultdict(lambda: {"passed": 0, "total": 0, "errors": 0})
    failing = Counter()
    passed = errors = 0
    for r in results:
        category = r["vars"].get("category", "other")
        if not r["success"] and is_infra_error(r):
            by_category[category]["errors"] += 1
            errors += 1
            continue
        by_category[category]["total"] += 1
        if r["success"]:
            by_category[category]["passed"] += 1
            passed += 1
        else:
            failing[(category, r["vars"]["question"])] += 1
    timestamp = output["results"].get("timestamp") or datetime.now(UTC).isoformat()
    return {
        "date": timestamp[:10],
        "sha": sha[:7],
        "run_url": run_url,
        "total": len(results),
        "graded": len(results) - errors,
        "passed": passed,
        "failed": len(results) - errors - passed,
        "errors": errors,
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


def status(passed, total):
    if not total:
        return "⚪"
    rate = passed / total
    return "🟢" if rate >= 0.95 else "🟡" if rate >= 0.90 else "🔴"


def graded(run):
    # Runs recorded before infrastructure errors were split out have no "graded" key.
    return run.get("graded", run["total"] - run["errors"])


def commit_link(run):
    sha = f"`{run['sha']}`"
    return f"[{sha}]({run['run_url']})" if run.get("run_url") else sha


def score_line(run):
    if not graded(run):
        return f"**Not graded**: all {run['total']} results hit infrastructure errors (grader or network)."
    line = f"**{pct(run['passed'], graded(run))} passing** ({run['passed']}/{graded(run)} graded)"
    if run["errors"]:
        line += f", {run['errors']} not graded: infrastructure errors (grader or network), not model quality"
    return line + "."


def trend_chart(runs):
    shown = [r for r in runs[-HISTORY_SHOWN:] if graded(r)]
    if not shown:
        return []
    labels = ", ".join(f'"{r["date"][5:]} {r["sha"]}"' for r in shown)
    values = ", ".join(f"{round(100 * r['passed'] / graded(r), 1)}" for r in shown)
    return [
        "```mermaid",
        "xychart-beta",
        f'    title "Pass rate, last {len(shown)} graded run{"s" if len(shown) != 1 else ""}"',
        f"    x-axis [{labels}]",
        '    y-axis "Pass rate (%)" 0 --> 100',
        f"    line [{values}]",
        "```",
    ]


def render(runs):
    latest = runs[-1]
    report = f" · [full report and logs]({latest['run_url']})" if latest.get("run_url") else ""
    lines = [
        "> [!NOTE]",
        f"> **Auto-updated by CI on every deploy.** Latest run: commit `{latest['sha']}` on "
        f"{latest['date']}, against staging{report}",
        "",
        score_line(latest),
        "",
        "| Category | Status | Passed | Rate |",
        "|---|:---:|---|---|",
    ]
    ordered = CATEGORIES + [c for c in latest["categories"] if c not in CATEGORIES]
    for name in ordered:
        c = latest["categories"].get(name)
        if c:
            lines.append(f"| {name} | {status(c['passed'], c['total'])} | {c['passed']}/{c['total']} | "
                         f"{pct(c['passed'], c['total'])} |")
    if latest["failing"]:
        n = len(latest["failing"])
        lines += ["", "<details>",
                  f"<summary>{n} question{'s' if n != 1 else ''} failed at least once in the latest run</summary>",
                  ""]
        lines += [f"- ({f['category']}, {f['times']}x) {f['question']}" for f in latest["failing"]]
        lines += ["", "</details>"]
    chart = trend_chart(runs)
    if chart:
        lines += [""] + chart
    lines += ["", "<details>", "<summary>Run history</summary>", "",
              "| Date | Commit | Passed | Rate | Not graded |", "|---|---|---|---|---|"]
    for run in reversed(runs[-HISTORY_SHOWN:]):
        lines.append(f"| {run['date']} | {commit_link(run)} | {run['passed']}/{graded(run)} | "
                     f"{pct(run['passed'], graded(run))} | {run['errors']} |")
    lines += ["", "</details>"]
    return "\n".join(lines)


def headline(runs):
    latest = runs[-1]
    report = f" · [report]({latest['run_url']})" if latest.get("run_url") else ""
    if not graded(latest):
        return (f"**Latest eval run (commit `{latest['sha']}`, {latest['date']}) was not graded**: "
                f"infrastructure errors{report}.")
    return (f"{status(latest['passed'], graded(latest))} **LLM evals: {pct(latest['passed'], graded(latest))} "
            f"passing** ({latest['passed']}/{graded(latest)}) on commit `{latest['sha']}`, {latest['date']}{report}")


def badge(runs):
    """shields.io endpoint badge data: https://shields.io/badges/endpoint-badge"""
    latest = runs[-1]
    if not graded(latest):
        return {"schemaVersion": 1, "label": "LLM evals", "message": "not graded", "color": "lightgrey"}
    rate = latest["passed"] / graded(latest)
    color = "brightgreen" if rate >= 0.95 else "yellow" if rate >= 0.90 else "red"
    return {"schemaVersion": 1, "label": "LLM evals", "message": f"{pct(latest['passed'], graded(latest))} passing",
            "color": color}


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
    parser.add_argument("--run-url", help="link to the CI run that holds the full report")
    parser.add_argument("--history", type=Path, default=Path("evals/results/history.json"))
    parser.add_argument("--badge", type=Path, default=Path("evals/results/badge.json"))
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    args = parser.parse_args()

    run = summarize(json.loads(args.output.read_text()), args.sha, args.run_url)
    runs = update_history(args.history, run)
    args.badge.write_text(json.dumps(badge(runs)) + "\n")
    args.readme.write_text(update_readme(args.readme.read_text(), runs))
    print(f"Recorded {run['passed']}/{run['graded']} graded ({run['errors']} infrastructure errors) for {run['sha']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
