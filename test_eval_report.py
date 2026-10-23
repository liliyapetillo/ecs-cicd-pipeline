import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "scripts"))
import eval_report  # noqa: E402

RUN_URL = "https://github.com/o/r/actions/runs/1"


def result(category, question, success, reason=None, failure_reason=None):
    r = {"success": success, "vars": {"category": category, "question": question}}
    if reason:
        r["gradingResult"] = {"componentResults": [{"pass": False, "reason": reason}]}
    if failure_reason:
        r["failureReason"] = failure_reason
    return r


@pytest.fixture
def output():
    return {
        "results": {
            "timestamp": "2026-10-23T14:00:00.000Z",
            "results": [
                result("positive", "Q1", True),
                result("positive", "Q1", True),
                result("negative", "Q2", True),
                result("negative", "Q2", False, reason="Expected output to contain X", failure_reason=1),
                result("edge", "Q3", True),
                result("edge", "Q4", False, reason="Rubric not met", failure_reason=1),
                # Infrastructure, not quality: a grader denied by IAM, and an app call that errored.
                result("redteam", "Q5", False, reason="Bedrock API invoke model error: AccessDeniedException: ..."),
                result("redteam", "Q6", False, failure_reason=2),
            ],
        }
    }


def test_summarize_counts_by_category_and_lists_failures(output):
    run = eval_report.summarize(output, "abcdef1234567", RUN_URL)
    assert run["sha"] == "abcdef1"
    assert run["date"] == "2026-10-23"
    assert run["run_url"] == RUN_URL
    assert run["categories"]["negative"] == {"passed": 1, "total": 2, "errors": 0}
    assert run["failing"] == [
        {"category": "negative", "question": "Q2", "times": 1},
        {"category": "edge", "question": "Q4", "times": 1},
    ]


def test_summarize_separates_infrastructure_errors_from_failures(output):
    run = eval_report.summarize(output, "abcdef1")
    assert (run["passed"], run["failed"], run["errors"], run["graded"], run["total"]) == (4, 2, 2, 6, 8)
    assert run["categories"]["redteam"] == {"passed": 0, "total": 0, "errors": 2}
    assert all(f["category"] != "redteam" for f in run["failing"])


def test_update_history_keeps_only_the_latest_runs(tmp_path, output):
    path = tmp_path / "results" / "history.json"
    run = eval_report.summarize(output, "abcdef1")
    for _ in range(eval_report.HISTORY_LIMIT + 5):
        runs = eval_report.update_history(path, run)
    assert len(runs) == eval_report.HISTORY_LIMIT
    assert len(json.loads(path.read_text())) == eval_report.HISTORY_LIMIT


def test_render_shows_scorecard_failures_chart_and_history(output):
    run = eval_report.summarize(output, "abcdef1", RUN_URL)
    text = eval_report.render([run])
    assert "> [!NOTE]" in text and f"[full report and logs]({RUN_URL})" in text
    assert "**67% passing** (4/6 graded), 2 not graded: infrastructure errors" in text
    assert "| negative | 🔴 | 1/2 | 50% |" in text
    assert "| positive | 🟢 | 2/2 | 100% |" in text
    assert "<summary>2 questions failed at least once in the latest run</summary>" in text
    assert "- (edge, 1x) Q4" in text
    assert "```mermaid" in text and "line [66.7]" in text
    assert f"| 2026-10-23 | [`abcdef1`]({RUN_URL}) | 4/6 | 67% | 2 |" in text


def test_render_handles_a_run_with_nothing_graded():
    output = {"results": {"timestamp": "2026-10-23T14:00:00Z",
                          "results": [result("positive", "Q1", False, failure_reason=2)]}}
    runs = [eval_report.summarize(output, "abcdef1")]
    text = eval_report.render(runs)
    assert "**Not graded**" in text and "```mermaid" not in text
    assert "was not graded" in eval_report.headline(runs)
    assert eval_report.badge(runs)["message"] == "not graded"


def test_render_accepts_history_recorded_before_graded_existed(output):
    old = {"date": "2026-10-20", "sha": "0000000", "total": 10, "passed": 9, "failed": 1, "errors": 0,
           "categories": {}, "failing": []}
    text = eval_report.render([old, eval_report.summarize(output, "abcdef1")])
    assert "| 2026-10-20 | `0000000` | 9/10 | 90% | 0 |" in text


@pytest.mark.parametrize(("passed", "color"), [(19, "brightgreen"), (18, "yellow"), (17, "red")])
def test_badge_color_follows_pass_rate(passed, color):
    run = {"date": "2026-10-23", "sha": "abcdef1", "total": 20, "graded": 20, "passed": passed, "errors": 0}
    data = eval_report.badge([run])
    assert data["schemaVersion"] == 1 and data["label"] == "LLM evals"
    assert data["color"] == color and data["message"] == f"{round(100 * passed / 20)}% passing"


def test_update_readme_replaces_the_marked_blocks_only(output):
    runs = [eval_report.summarize(output, "abcdef1")]
    readme = (
        f"top\n{eval_report.HEAD_START}\nold\n{eval_report.HEAD_END}\n"
        f"middle\n{eval_report.START}\nold\n{eval_report.END}\nbottom\n"
    )
    updated = eval_report.update_readme(readme, runs)
    assert "**LLM evals: 67% passing** (4/6) on commit `abcdef1`, 2026-10-23" in updated
    assert "| negative | 🔴 | 1/2 | 50% |" in updated
    assert updated.startswith("top\n") and updated.endswith("bottom\n") and "old" not in updated


def test_update_readme_requires_markers(output):
    with pytest.raises(ValueError):
        eval_report.update_readme("no markers here", [eval_report.summarize(output, "abcdef1")])
