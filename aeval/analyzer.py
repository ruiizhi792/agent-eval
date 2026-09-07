"""Analyzer: turn ``results/**/*.json`` into a variance-aware markdown report + chart.

**This module never touches a browser.** It reads the JSON files written by the runner
and recomputes everything from them, which is what makes every number in the report
reproducible by someone else with the same ``results/`` directory.

Metrics produced
----------------

``pass_rate``
    ``passed_runs / n_runs`` for one task.
``flaky``
    ``0 < passed_runs < n_runs``. A task that passes 3/5 is *not* a 60% task, it is an
    unreliable task, and it deserves a different sentence in the write-up than one that
    passes 5/5 or 0/5.
``bernoulli_std``
    Treating every run as a Bernoulli trial, ``sqrt(p * (1 - p))`` is the standard
    deviation of a single trial and ``sqrt(p * (1 - p) / n)`` is the standard error of
    the pooled pass rate. Reporting the mean without it is how evaluation numbers get
    over-trusted.
``repetition_std``
    Pass rate computed *within* each repetition index (i.e. "how did the whole suite do
    on pass 1 vs pass 2 vs ..."), then the standard deviation across those. This is the
    run-to-run stability of the suite as a whole and is usually the number people
    actually mean when they say "is it reliable?".
``consistency``
    ``1 - flaky_tasks / n_tasks``. One scalar answer to "how much of this suite is
    trustworthy enough to average?".
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from aeval.schema import CATEGORY_ORDER, RunResult

__all__ = [
    "TaskStat",
    "CategoryStat",
    "Analysis",
    "load_results",
    "analyze",
    "render_markdown",
    "render_chart",
    "write_report",
    "REPORT_FILENAME",
    "CHART_FILENAME",
]

REPORT_FILENAME = "report.md"
CHART_FILENAME = "passrate_by_task.png"

#: Colours (light theme, colour-blind-tolerant enough for a bar chart).
COLOR_STABLE = "#2E7D32"  # green  — 5/5
COLOR_FLAKY = "#EF8A17"  # amber  — 1..N-1 / N
COLOR_FAIL = "#C62828"  # red    — 0/N
COLOR_CATEGORY = "#3D6FB4"  # blue   — category aggregate bars

STATUS_STABLE = "stable"
STATUS_FLAKY = "flaky"
STATUS_FAIL = "always-fail"


# --------------------------------------------------------------------------- #
# loading
# --------------------------------------------------------------------------- #
def load_results(
    results_dir: "str | Path",
    backend: Optional[str] = None,
    session: Optional[str] = None,
) -> Tuple[List[RunResult], List[Path]]:
    """Read every result file under ``results_dir``.

    Args:
        results_dir: Root of the results tree.
        backend: Optional backend filter; matched against the ``backend`` field and,
            as a fallback, the first directory component under ``results_dir``.
        session: Optional session filter; ``"latest"`` picks the most recent session.

    Returns:
        ``(results, skipped_files)`` — parsed results and the files that were not
        valid result documents (manifests, corrupt files).
    """
    root = Path(results_dir)
    results: List[RunResult] = []
    skipped: List[Path] = []
    if not root.exists():
        return results, skipped

    for path in sorted(root.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            skipped.append(path)
            continue
        if not isinstance(data, dict) or "task_id" not in data or "run_index" not in data:
            skipped.append(path)
            continue
        if backend and str(data.get("backend", "")) != backend:
            # Fall back to the on-disk layout (results/<backend>/<session>/...).
            try:
                relative = path.relative_to(root)
                on_disk_backend = relative.parts[0] if len(relative.parts) > 1 else ""
            except ValueError:  # pragma: no cover - defensive
                on_disk_backend = ""
            if on_disk_backend != backend:
                continue
        results.append(RunResult.from_dict(data))

    if session:
        results = _filter_session(results, session)
    return results, skipped


def _filter_session(results: List[RunResult], session: str) -> List[RunResult]:
    """Keep only the runs belonging to one session.

    Args:
        results: All loaded results.
        session: Session id, or the literal ``"latest"``.

    Returns:
        The filtered results (unchanged when no sessions are labelled).
    """
    sessions = sorted({r.session for r in results if r.session})
    if not sessions:
        return results
    if session == "latest":
        chosen = sessions[-1]
    else:
        chosen = session
    return [r for r in results if r.session == chosen]


# --------------------------------------------------------------------------- #
# statistics
# --------------------------------------------------------------------------- #
@dataclass
class TaskStat:
    """Per-task aggregate over the N repetitions.

    Attributes:
        task_id: Task identifier.
        category: Category label.
        difficulty: Difficulty label.
        n_runs: Number of trials observed.
        pass_count: Number of passing trials.
        pass_rate: ``pass_count / n_runs``.
        status: ``stable`` / ``flaky`` / ``always-fail``.
        mean_duration_ms: Mean wall-clock duration.
        last_error: Most recent failure reason, for the report.
        per_run: ``{run_index: passed}`` for the trials that were observed.
    """

    task_id: str = ""
    category: str = ""
    difficulty: str = ""
    n_runs: int = 0
    pass_count: int = 0
    pass_rate: float = 0.0
    status: str = STATUS_FAIL
    mean_duration_ms: float = 0.0
    last_error: str = ""
    per_run: Dict[int, bool] = field(default_factory=dict)

    @property
    def is_flaky(self) -> bool:
        """True when the task both passed and failed at least once."""
        return self.status == STATUS_FLAKY

    @property
    def label(self) -> str:
        """Compact ``k/N`` label used in the chart."""
        return f"{self.pass_count}/{self.n_runs}"

    @property
    def color(self) -> str:
        """Bar colour determined by reliability, not by pass rate."""
        return {
            STATUS_STABLE: COLOR_STABLE,
            STATUS_FLAKY: COLOR_FLAKY,
            STATUS_FAIL: COLOR_FAIL,
        }[self.status]


@dataclass
class CategoryStat:
    """Per-category aggregate.

    Attributes:
        category: Category label.
        n_tasks: Number of distinct tasks observed.
        n_runs: Number of trials observed.
        pass_count: Number of passing trials.
        pass_rate: ``pass_count / n_runs``.
        n_flaky: Tasks in this category that were flaky.
        n_always_fail: Tasks in this category that never passed.
    """

    category: str = ""
    n_tasks: int = 0
    n_runs: int = 0
    pass_count: int = 0
    pass_rate: float = 0.0
    n_flaky: int = 0
    n_always_fail: int = 0


@dataclass
class Analysis:
    """Everything the report needs.

    Attributes:
        backend: Backend label (``"all"`` when results are pooled).
        n_tasks: Number of distinct tasks.
        total_runs: Number of trials.
        passed_runs: Number of passing trials.
        overall_pass_rate: ``passed_runs / total_runs``.
        bernoulli_std: ``sqrt(p(1-p))`` over all trials.
        bernoulli_stderr: ``sqrt(p(1-p)/n)``.
        repetition_rates: Pass rate within each repetition index.
        repetition_mean: Mean of :attr:`repetition_rates`.
        repetition_std: Sample standard deviation of :attr:`repetition_rates`.
        task_stats: Per-task aggregates, sorted by pass rate then id.
        category_stats: Per-category aggregates, in canonical category order.
        consistency: ``1 - flaky/n_tasks``.
        sessions: Session ids present in the data.
        sources: Files the analysis was computed from.
        skipped_files: Files that could not be parsed as results.
    """

    backend: str = "all"
    n_tasks: int = 0
    total_runs: int = 0
    passed_runs: int = 0
    overall_pass_rate: float = 0.0
    bernoulli_std: float = 0.0
    bernoulli_stderr: float = 0.0
    repetition_rates: List[float] = field(default_factory=list)
    repetition_mean: float = 0.0
    repetition_std: float = 0.0
    task_stats: List[TaskStat] = field(default_factory=list)
    category_stats: List[CategoryStat] = field(default_factory=list)
    consistency: float = 0.0
    sessions: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    skipped_files: List[str] = field(default_factory=list)

    @property
    def flaky_tasks(self) -> List[TaskStat]:
        """Tasks that both passed and failed."""
        return [t for t in self.task_stats if t.is_flaky]

    @property
    def stable_tasks(self) -> List[TaskStat]:
        """Tasks that passed every trial."""
        return [t for t in self.task_stats if t.status == STATUS_STABLE]

    @property
    def failing_tasks(self) -> List[TaskStat]:
        """Tasks that never passed."""
        return [t for t in self.task_stats if t.status == STATUS_FAIL]

    @property
    def n_runs_per_task(self) -> int:
        """Maximum number of trials observed for any single task."""
        return max((t.n_runs for t in self.task_stats), default=0)


def _status(pass_count: int, n_runs: int) -> str:
    """Classify a task from its pass count.

    Args:
        pass_count: Number of passing runs.
        n_runs: Total runs.

    Returns:
        One of ``stable`` / ``flaky`` / ``always-fail``.
    """
    if n_runs <= 0:
        return STATUS_FAIL
    if pass_count == 0:
        return STATUS_FAIL
    if pass_count == n_runs:
        return STATUS_STABLE
    return STATUS_FLAKY


def _std(values: Sequence[float]) -> float:
    """Sample standard deviation (ddof=1), 0.0 for fewer than two values.

    Args:
        values: Observations.

    Returns:
        The sample standard deviation.
    """
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def analyze(results: Sequence[RunResult], backend: str = "all") -> Analysis:
    """Compute every statistic the report needs.

    Args:
        results: The trials to aggregate.
        backend: Label used in the report heading.

    Returns:
        The :class:`Analysis`.
    """
    analysis = Analysis(backend=backend)
    analysis.total_runs = len(results)
    analysis.passed_runs = sum(1 for r in results if r.passed)
    analysis.overall_pass_rate = (
        analysis.passed_runs / analysis.total_runs if analysis.total_runs else 0.0
    )
    p = analysis.overall_pass_rate
    analysis.bernoulli_std = math.sqrt(p * (1.0 - p))
    analysis.bernoulli_stderr = (
        analysis.bernoulli_std / math.sqrt(analysis.total_runs) if analysis.total_runs else 0.0
    )
    analysis.sessions = sorted({r.session for r in results if r.session})
    analysis.skipped_files = []

    # --- per task -------------------------------------------------------- #
    by_task: Dict[str, List[RunResult]] = {}
    for result in results:
        by_task.setdefault(result.task_id, []).append(result)

    stats: List[TaskStat] = []
    for task_id, runs in by_task.items():
        durations = [r.duration_ms for r in runs]
        stat = TaskStat(
            task_id=task_id,
            category=runs[-1].category or "unknown",
            difficulty=runs[-1].difficulty or "unknown",
            n_runs=len(runs),
            pass_count=sum(1 for r in runs if r.passed),
            mean_duration_ms=(sum(durations) / len(durations)) if durations else 0.0,
            per_run={r.run_index: r.passed for r in runs},
        )
        stat.pass_rate = stat.pass_count / stat.n_runs if stat.n_runs else 0.0
        stat.status = _status(stat.pass_count, stat.n_runs)
        failures = [r for r in runs if not r.passed and r.error]
        stat.last_error = failures[-1].error if failures else ""
        stats.append(stat)
    stats.sort(key=lambda s: (s.pass_rate, -s.n_runs, s.task_id))
    analysis.task_stats = stats
    analysis.n_tasks = len(stats)
    analysis.consistency = (
        1.0 - (len(analysis.flaky_tasks) / analysis.n_tasks) if analysis.n_tasks else 0.0
    )

    # --- per category ------------------------------------------------------ #
    cat_map: Dict[str, CategoryStat] = {}
    for stat in stats:
        entry = cat_map.setdefault(stat.category, CategoryStat(category=stat.category))
        entry.n_tasks += 1
        entry.n_runs += stat.n_runs
        entry.pass_count += stat.pass_count
        entry.n_flaky += 1 if stat.status == STATUS_FLAKY else 0
        entry.n_always_fail += 1 if stat.status == STATUS_FAIL else 0
    for entry in cat_map.values():
        entry.pass_rate = entry.pass_count / entry.n_runs if entry.n_runs else 0.0

    ordered: List[CategoryStat] = []
    for category in CATEGORY_ORDER:
        if str(category) in cat_map:
            ordered.append(cat_map[str(category)])
    for key in sorted(cat_map):
        if key not in {str(c) for c in CATEGORY_ORDER}:
            ordered.append(cat_map[key])
    analysis.category_stats = ordered

    # --- run-to-run stability ---------------------------------------------- #
    by_index: Dict[int, List[bool]] = {}
    for result in results:
        by_index.setdefault(result.run_index, []).append(result.passed)
    analysis.repetition_rates = [
        sum(flags) / len(flags) for _, flags in sorted(by_index.items()) if flags
    ]
    analysis.repetition_mean = (
        sum(analysis.repetition_rates) / len(analysis.repetition_rates)
        if analysis.repetition_rates
        else 0.0
    )
    analysis.repetition_std = _std(analysis.repetition_rates)

    return analysis


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def _pct(value: float) -> str:
    """Format a ratio as a percentage with one decimal."""
    return f"{value * 100:.1f}%"


def render_markdown(analysis: Analysis) -> str:
    """Render the analysis as a markdown report.

    Args:
        analysis: The computed statistics.

    Returns:
        The report, as a string.
    """
    lines: List[str] = []
    add = lines.append

    add("# AgentEval report")
    add("")
    add(f"- **backend**: `{analysis.backend}`")
    add(f"- **tasks**: {analysis.n_tasks}")
    add(f"- **runs per task**: {analysis.n_runs_per_task}")
    add(f"- **total runs**: {analysis.total_runs}")
    if analysis.sessions:
        add(f"- **sessions**: {', '.join(analysis.sessions)}")
    add("")

    add("> Read this first: a pass rate is only meaningful next to its variance. "
        "Every number below was computed from the JSON files in `results/` — no browser "
        "was involved, so you can recompute it with `python run_eval.py analyze`.")
    add("")

    add("## Headline")
    add("")
    add("| metric | value |")
    add("| --- | --- |")
    add(f"| overall pass rate | {_pct(analysis.overall_pass_rate)} "
        f"({analysis.passed_runs}/{analysis.total_runs}) |")
    add(f"| Bernoulli sd of a single run | {analysis.bernoulli_std:.3f} |")
    add(f"| standard error of the pass rate | {analysis.bernoulli_stderr:.3f} |")
    add(f"| run-to-run suite pass rate (mean) | {_pct(analysis.repetition_mean)} |")
    add(f"| run-to-run suite pass rate (sd) | {analysis.repetition_std:.3f} |")
    add(f"| stable tasks ({STATUS_STABLE}) | {len(analysis.stable_tasks)} |")
    add(f"| **flaky tasks** ({STATUS_FLAKY}) | **{len(analysis.flaky_tasks)}** |")
    add(f"| always-failing tasks ({STATUS_FAIL}) | {len(analysis.failing_tasks)} |")
    add(f"| consistency (1 - flaky/tasks) | {analysis.consistency:.3f} |")
    add("")

    if analysis.repetition_rates:
        per_run = ", ".join(f"run {i + 1}: {_pct(r)}" for i, r in enumerate(analysis.repetition_rates))
        add(f"Suite pass rate by repetition — {per_run}.")
        add("")

    add("## By category")
    add("")
    add("| category | tasks | runs | passed | pass rate | flaky | always-fail |")
    add("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for entry in analysis.category_stats:
        add(
            f"| `{entry.category}` | {entry.n_tasks} | {entry.n_runs} | {entry.pass_count} | "
            f"{_pct(entry.pass_rate)} | {entry.n_flaky} | {entry.n_always_fail} |"
        )
    add("")

    add("## Per task")
    add("")
    add("| task | category | difficulty | passes | pass rate | status | mean ms |")
    add("| --- | --- | --- | --- | ---: | --- | ---: |")
    for stat in analysis.task_stats:
        add(
            f"| `{stat.task_id}` | {stat.category} | {stat.difficulty} | {stat.label} | "
            f"{_pct(stat.pass_rate)} | {stat.status} | {stat.mean_duration_ms:.0f} |"
        )
    add("")

    add("## Flaky tasks (the point of the whole exercise)")
    add("")
    if not analysis.flaky_tasks:
        add("None observed. Either the backend is deterministic on this suite, or N is too "
            "small — raise `--runs` before concluding anything.")
    else:
        for stat in analysis.flaky_tasks:
            pattern = "".join(
                "P" if stat.per_run.get(i, False) else "."
                for i in range(stat.n_runs)
            )
            add(f"- `{stat.task_id}` ({stat.category}/{stat.difficulty}) — {stat.label}, "
                f"pattern `{pattern}`")
            if stat.last_error:
                add(f"  - last failure: {stat.last_error[:200]}")
    add("")

    add("## Always-failing tasks")
    add("")
    if not analysis.failing_tasks:
        add("None. Suspiciously good — check that `verify` is actually asserting something.")
    else:
        for stat in analysis.failing_tasks:
            add(f"- `{stat.task_id}` ({stat.category}/{stat.difficulty}) — 0/{stat.n_runs}")
            if stat.last_error:
                add(f"  - last failure: {stat.last_error[:200]}")
    add("")

    add("## How to reproduce")
    add("")
    add("```bash")
    add(f"python run_eval.py run --backend {analysis.backend} "
        f"--runs {analysis.n_runs_per_task or 5}")
    add(f"python run_eval.py analyze --backend {analysis.backend}")
    add("```")
    add("")
    add(f"Raw evidence: {analysis.total_runs} JSON files under `results/`. "
        f"Each file contains the task id, the run index, the backend, the seed, the full "
        f"action sequence, the final URL, the per-assertion grading result and the duration.")
    add("")
    return "\n".join(lines)


def render_chart(analysis: Analysis, out_path: "str | Path") -> Optional[Path]:
    """Draw the pass-rate chart.

    Left panel: one bar per task, sorted by pass rate, coloured by *reliability*
    (green = always passed, amber = flaky, red = never passed) so that flakiness is
    visible at a glance rather than hidden inside an average. Right panel: pass rate
    per category.

    Args:
        analysis: The computed statistics.
        out_path: Destination PNG.

    Returns:
        The path written, or ``None`` when matplotlib is unavailable.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")  # headless-safe; must precede pyplot import
        import matplotlib.pyplot as plt
    except ImportError:  # pragma: no cover - matplotlib is optional at runtime
        return None

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    tasks = analysis.task_stats
    n_tasks = max(len(tasks), 1)
    fig, (ax_left, ax_right) = plt.subplots(
        1, 2, figsize=(14.0, max(5.0, 0.32 * n_tasks + 1.6)), gridspec_kw={"width_ratios": [2.4, 1.0]}
    )
    fig.patch.set_facecolor("white")

    # ---- left: per-task pass rate, sorted, coloured by reliability ---- #
    labels = [t.task_id for t in tasks]
    rates = [t.pass_rate for t in tasks]
    colors = [t.color for t in tasks]
    ypos = list(range(n_tasks))
    ax_left.barh(ypos, rates, color=colors, height=0.68, edgecolor="white", linewidth=0.6)
    ax_left.set_yticks(ypos)
    ax_left.set_yticklabels(labels, fontsize=8)
    ax_left.invert_yaxis()
    ax_left.set_xlim(0.0, 1.0)
    ax_left.set_xlabel("pass rate", fontsize=9)
    ax_left.set_title(f"Per-task pass rate — backend `{analysis.backend}`", fontsize=11, pad=10)
    ax_left.grid(axis="x", color="#E6E6E6", linewidth=0.7)
    ax_left.set_axisbelow(True)
    for spine in ("top", "right"):
        ax_left.spines[spine].set_visible(False)

    # k/N at the end of each bar
    for y, stat in zip(ypos, tasks):
        ax_left.text(
            min(stat.pass_rate + 0.015, 0.985),
            y,
            stat.label,
            va="center",
            ha="left" if stat.pass_rate < 0.9 else "right",
            fontsize=7.5,
            color="#333333",
        )

    handles = [
        plt.Rectangle((0, 0), 1, 1, color=COLOR_STABLE),
        plt.Rectangle((0, 0), 1, 1, color=COLOR_FLAKY),
        plt.Rectangle((0, 0), 1, 1, color=COLOR_FAIL),
    ]
    ax_left.legend(
        handles,
        [
            f"stable ({len(analysis.stable_tasks)})",
            f"flaky ({len(analysis.flaky_tasks)})",
            f"always-fail ({len(analysis.failing_tasks)})",
        ],
        loc="lower right",
        fontsize=8,
        frameon=True,
        facecolor="white",
        edgecolor="#DDDDDD",
    )

    # ---- right: per-category pass rate ---- #
    cats = analysis.category_stats
    if cats:
        cat_labels = [c.category for c in cats]
        cat_rates = [c.pass_rate for c in cats]
        cat_y = list(range(len(cats)))
        ax_right.barh(cat_y, cat_rates, color=COLOR_CATEGORY, height=0.62, edgecolor="white")
        ax_right.set_yticks(cat_y)
        ax_right.set_yticklabels(cat_labels, fontsize=9)
        ax_right.invert_yaxis()
        ax_right.set_xlim(0.0, 1.0)
        ax_right.set_xlabel("pass rate", fontsize=9)
        ax_right.set_title("By category", fontsize=11, pad=10)
        ax_right.grid(axis="x", color="#E6E6E6", linewidth=0.7)
        ax_right.set_axisbelow(True)
        for spine in ("top", "right"):
            ax_right.spines[spine].set_visible(False)
        for y, entry in zip(cat_y, cats):
            ax_right.text(
                min(entry.pass_rate + 0.02, 0.97),
                y,
                f"{entry.pass_rate * 100:.0f}%",
                va="center",
                fontsize=8,
                color="#333333",
            )
    else:
        ax_right.axis("off")

    fig.tight_layout()
    fig.savefig(out, dpi=150, facecolor="white")
    plt.close(fig)
    return out


def write_report(
    analysis: Analysis,
    out_dir: "str | Path",
    with_chart: bool = True,
) -> Dict[str, Path]:
    """Write ``report.md`` (and the chart) to ``out_dir``.

    Args:
        analysis: The computed statistics.
        out_dir: Destination directory.
        with_chart: Whether to render the PNG.

    Returns:
        ``{"markdown": path, "chart": path|None}``.
    """
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    md_path = directory / REPORT_FILENAME
    md_path.write_text(render_markdown(analysis), encoding="utf-8")
    chart_path: Optional[Path] = None
    if with_chart:
        chart_path = render_chart(analysis, directory / CHART_FILENAME)
    return {"markdown": md_path, "chart": chart_path}
