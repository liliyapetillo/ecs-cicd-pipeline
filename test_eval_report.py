import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "scripts"))
import eval_report  # noqa: E402


def result(category, question, success):
    return {"success": success, "vars": {"category": category, "question": question}}


@pytest.fixture
def output():
    return {
        "results": {
            "timestamp": "2026-10-23T14:00:00.000Z",
            "stats": {"successes": 4, "failures": 1, "errors": 1},
            "results": [
                result("positive", "Q1", True),
                result("positive", "Q1", True),
                result("negative", "Q2", True),
                result("negative", "Q2", False),
                result("edge", "Q3", True),
                result("edge", "Q4", False),
            ],
        }
    }


def test_summarize_counts_by_category_and_lists_failures(output):
    run = eval_report.summarize(output, "abcdef1234567")
    assert run["sha"] == "abcdef1"
    assert run["date"] == "2026-10-23"
    assert (run["passed"], run["total"], run["errors"]) == (4, 6, 1)
    assert run["categories"]["negative"] == {"passed": 1, "total": 2}
    assert run["failing"] == [
        {"category": "negative", "question": "Q2", "times": 1},
        {"category": "edge", "question": "Q4", "times": 1},
    ]


def test_update_history_keeps_only_the_latest_runs(tmp_path, output):
    path = tmp_path / "results" / "history.json"
    run = eval_report.summarize(output, "abcdef1")
    for _ in range(eval_report.HISTORY_LIMIT + 5):
        runs = eval_report.update_history(path, run)
    assert len(runs) == eval_report.HISTORY_LIMIT
    assert len(json.loads(path.read_text())) == eval_report.HISTORY_LIMIT


def test_render_shows_rates_failures_and_history(output):
    run = eval_report.summarize(output, "abcdef1")
    text = eval_report.render([run])
    assert "**4/6 passed (67%)**" in text
    assert "1 errored" in text
    assert "| negative | 1/2 | 50% |" in text
    assert "- (edge, 1x) Q4" in text
    assert "| 2026-10-23 | `abcdef1` | 4/6 | 67% |" in text


def test_update_readme_replaces_the_marked_blocks_only(output):
    runs = [eval_report.summarize(output, "abcdef1")]
    readme = (
        f"top\n{eval_report.HEAD_START}\nold\n{eval_report.HEAD_END}\n"
        f"middle\n{eval_report.START}\nold\n{eval_report.END}\nbottom\n"
    )
    updated = eval_report.update_readme(readme, runs)
    assert "**Latest eval run: 4/6 passed (67%) on commit `abcdef1`, 2026-10-23.**" in updated
    assert "| negative | 1/2 | 50% |" in updated
    assert updated.startswith("top\n") and updated.endswith("bottom\n") and "old" not in updated


def test_update_readme_requires_markers(output):
    with pytest.raises(ValueError):
        eval_report.update_readme("no markers here", [eval_report.summarize(output, "abcdef1")])
