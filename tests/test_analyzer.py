"""Tests for :mod:`aeval.analyzer` — offline, JSON-fixture based, no browser.

These tests are the reason the analysis path can be trusted: they prove that every
headline number in the report is a pure function of the JSON files on disk, so a
reviewer can recompute them without running a single browser.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aeval.analyzer import (
    CHART_FILENAME,
    STATUS_FLAKY,
    STATUS_FAIL,
    STATUS_STABLE,
    analyze,
    load_results,
    render_chart,
    render_markdown,
    write_report,
)
from aeval.schema import RunResult


# --------------------------------------------------------------------------- #
# fixture factory
# --------------------------------------------------------------------------- #
def make_result(
    task_id: str,
    run_index: int,
    passed: bool,
    category: str = "nav",
    difficulty: str = "easy",
    backend: str = "naive",
    session: str = "s1",
    duration_ms: int = 1000,
    error: str = "",
) -> RunResult:
    """Build one synthetic result.

    Args:
        task_id: Task identifier.
        run_index: Repetition index.
        passed: Verdict.
        category: Category label.
        difficulty: Difficulty label.
        backend: Backend label.
        session: Session id.
        duration_ms: Duration in milliseconds.
        error: Failure reason.

    Returns:
        The constructed :class:`RunResult`.
    """
    return RunResult(
        task_id=task_id,
        run_index=run_index,
        passed=passed,
        category=category,
        difficulty=difficulty,
        backend=backend,
        session=session,
        duration_ms=duration_ms,
        error=error,
        final_url="https://example.test/",
        checks=[{"name": "check_one", "passed": passed, "detail": "observed"}],
        checks_summary="all 1 checks passed" if passed else "1/1 checks failed",
        steps=[{"index": 0, "op": "goto", "ok": True}],
        n_steps=1,
    )


def write_fixture_tree(root: Path, results) -> Path:
    """Lay results out the way the runner does and return the tree root.

    Args:
        root: Temporary directory.
        results: Iterable of :class:`RunResult`.

    Returns:
        The ``results`` directory.
    """
    results_dir = root / "results"
    for result in results:
        path = results_dir / result.backend / result.session / f"{result.task_id}__run{result.run_index:02d}.json"
        result.write_json(path)
    # A manifest that must be ignored by the loader.
    manifest = results_dir / "naive" / "s1" / "session_manifest.json"
    manifest.write_text(json.dumps({"session": "s1", "n_runs": len(results)}), encoding="utf-8")
    return results_dir


# --------------------------------------------------------------------------- #
# load_results
# --------------------------------------------------------------------------- #
def test_load_results_reads_every_run_and_ignores_manifests(tmp_path: Path) -> None:
    """Only real result documents are loaded; manifests are reported as skipped."""
    results = [
        make_result("task_a", 0, True),
        make_result("task_a", 1, False),
        make_result("task_b", 0, False, category="frame"),
    ]
    results_dir = write_fixture_tree(tmp_path, results)

    loaded, skipped = load_results(results_dir)

    assert len(loaded) == 3
    assert len(skipped) == 1
    assert skipped[0].name == "session_manifest.json"


def test_load_results_filters_by_backend(tmp_path: Path) -> None:
    """The ``backend`` filter selects a subtree."""
    results = [
        make_result("task_a", 0, True, backend="naive"),
        make_result("task_a", 0, True, backend="oracle"),
    ]
    results_dir = write_fixture_tree(tmp_path, results)

    loaded, _ = load_results(results_dir, backend="oracle")

    assert len(loaded) == 1
    assert loaded[0].backend == "oracle"


def test_load_results_missing_directory_returns_empty(tmp_path: Path) -> None:
    """A non-existent results dir yields nothing instead of raising."""
    loaded, skipped = load_results(tmp_path / "does-not-exist")
    assert loaded == []
    assert skipped == []


# --------------------------------------------------------------------------- #
# analyze
# --------------------------------------------------------------------------- #
def test_analyze_classifies_stable_flaky_and_failing() -> None:
    """5/5 -> stable, 3/5 -> flaky, 0/5 -> always-fail."""
    results = []
    for i in range(5):
        results.append(make_result("stable_task", i, True))
        results.append(make_result("flaky_task", i, i < 3))
        results.append(make_result("dead_task", i, False))

    analysis = analyze(results, backend="naive")

    by_id = {s.task_id: s for s in analysis.task_stats}
    assert by_id["stable_task"].status == STATUS_STABLE
    assert by_id["flaky_task"].status == STATUS_FLAKY
    assert by_id["dead_task"].status == STATUS_FAIL
    assert by_id["flaky_task"].pass_count == 3
    assert by_id["flaky_task"].n_runs == 5
    assert by_id["flaky_task"].pass_rate == pytest.approx(0.6)
    assert len(analysis.flaky_tasks) == 1
    assert len(analysis.stable_tasks) == 1
    assert len(analysis.failing_tasks) == 1


def test_analyze_overall_rate_and_bernoulli_std() -> None:
    """Overall rate is 6/12 = 0.5, so the Bernoulli sd is sqrt(0.25) = 0.5."""
    results = []
    for i in range(5):
        results.append(make_result("stable_task", i, True))
        results.append(make_result("dead_task", i, False))
        if i == 0:
            results.append(make_result("one_off", 0, True))

    analysis = analyze(results, backend="naive")

    assert analysis.total_runs == 11
    assert analysis.passed_runs == 6
    assert analysis.overall_pass_rate == pytest.approx(6 / 11)
    p = 6 / 11
    assert analysis.bernoulli_std == pytest.approx((p * (1 - p)) ** 0.5)
    assert analysis.bernoulli_stderr == pytest.approx(((p * (1 - p)) ** 0.5) / (11 ** 0.5))


def test_analyze_repetition_std_is_zero_when_deterministic() -> None:
    """If every repetition behaves identically, run-to-run sd is 0."""
    results = [make_result("t", i, True) for i in range(5)]
    analysis = analyze(results)
    assert analysis.repetition_rates == [1.0] * 5
    assert analysis.repetition_std == pytest.approx(0.0)
    assert analysis.repetition_mean == pytest.approx(1.0)


def test_analyze_category_breakdown(tmp_path: Path) -> None:
    """Category rows aggregate runs and count flaky / dead tasks."""
    results = []
    for i in range(4):
        results.append(make_result("nav_ok", i, True, category="nav"))
        results.append(make_result("nav_flaky", i, i < 2, category="nav"))
        results.append(make_result("frame_dead", i, False, category="frame"))
    results_dir = write_fixture_tree(tmp_path, results)

    loaded, _ = load_results(results_dir)
    analysis = analyze(loaded, backend="naive")

    nav = next(c for c in analysis.category_stats if c.category == "nav")
    frame = next(c for c in analysis.category_stats if c.category == "frame")
    assert nav.n_tasks == 2
    assert nav.n_runs == 8
    assert nav.pass_count == 6
    assert nav.pass_rate == pytest.approx(0.75)
    assert nav.n_flaky == 1
    assert frame.pass_rate == pytest.approx(0.0)
    assert frame.n_always_fail == 1


def test_analyze_consistency_score() -> None:
    """consistency = 1 - flaky/tasks = 1 - 1/3."""
    results = []
    for i in range(3):
        results.append(make_result("a", i, True))
        results.append(make_result("b", i, i < 2))
        results.append(make_result("c", i, False))
    analysis = analyze(results)
    assert analysis.consistency == pytest.approx(1 - 1 / 3)


def test_analyze_empty_input_is_safe() -> None:
    """An empty result set produces an empty analysis, not a ZeroDivisionError."""
    analysis = analyze([])
    assert analysis.total_runs == 0
    assert analysis.overall_pass_rate == 0.0
    assert analysis.task_stats == []
    assert analysis.consistency == 0.0


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #
def test_render_markdown_contains_the_variance_story() -> None:
    """The report must carry the rate, the flaky list and the category table."""
    results = []
    for i in range(5):
        results.append(make_result("stable_task", i, True, category="nav"))
        results.append(make_result("flaky_task", i, i < 3, category="dynamic"))
        results.append(make_result("dead_task", i, False, category="frame"))
    analysis = analyze(results, backend="naive")

    markdown = render_markdown(analysis)

    assert "# AgentEval report" in markdown
    assert "flaky_task" in markdown
    assert "## By category" in markdown
    assert "| `frame` |" in markdown
    # 5 (stable) + 3 (flaky) + 0 (dead) = 8 of 15 runs passed.
    assert "8/15" in markdown
    assert "53.3%" in markdown
    assert "python run_eval.py" in markdown


def test_write_report_creates_markdown_and_chart(tmp_path: Path) -> None:
    """``write_report`` writes report.md, plus a PNG when matplotlib is available."""
    results = [make_result("t", i, i % 2 == 0, category="nav") for i in range(4)]
    analysis = analyze(results, backend="naive")

    written = write_report(analysis, tmp_path / "reports")

    assert written["markdown"].exists()
    assert "AgentEval report" in written["markdown"].read_text(encoding="utf-8")
    if written["chart"] is not None:
        assert written["chart"].name == CHART_FILENAME
        assert written["chart"].exists()
        assert written["chart"].stat().st_size > 0


def test_render_chart_returns_none_without_matplotlib(tmp_path: Path, monkeypatch) -> None:
    """A missing matplotlib degrades to 'no chart' instead of an exception."""
    import builtins

    results = [make_result("t", i, True) for i in range(3)]
    analysis = analyze(results)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("matplotlib"):
            raise ImportError("matplotlib disabled for this test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert render_chart(analysis, tmp_path / "chart.png") is None


def test_end_to_end_from_json_files(tmp_path: Path) -> None:
    """The whole offline path: write JSON, reload it, rebuild the report."""
    results = []
    for i in range(5):
        results.append(make_result("sauce_login_success", i, i < 4, category="auth"))
        results.append(make_result("inet_iframe_type_text", i, False, category="frame"))
    results_dir = write_fixture_tree(tmp_path, results)

    loaded, _ = load_results(results_dir, backend="naive")
    analysis = analyze(loaded, backend="naive")
    written = write_report(analysis, tmp_path / "reports")

    assert analysis.n_tasks == 2
    assert len(analysis.flaky_tasks) == 1
    assert analysis.flaky_tasks[0].task_id == "sauce_login_success"
    assert len(analysis.failing_tasks) == 1
    assert written["markdown"].exists()
