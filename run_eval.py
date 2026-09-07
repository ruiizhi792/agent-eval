#!/usr/bin/env python
"""AgentEval CLI — run the sweep, analyse it, list tasks and backends.

Every command is thin: parse arguments, then delegate to :mod:`aeval.runner`,
:mod:`aeval.analyzer` or the registries. Imports of the heavy/optional modules are
lazy so that ``python run_eval.py --help`` works in a bare interpreter with no
Playwright and no matplotlib installed.

Examples
--------
    python run_eval.py                       # full sweep: naive backend, 5 runs per task
    python run_eval.py run --backend oracle --runs 1      # calibrate the grader
    python run_eval.py analyze --backend naive            # recompute the report offline
    python run_eval.py list-tasks --category frame
    python run_eval.py list-backends
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Sequence

#: Repository root (the directory containing this file).
PROJECT_ROOT = Path(__file__).resolve().parent

if str(PROJECT_ROOT) not in sys.path:  # pragma: no cover - makes `python run_eval.py` work anywhere
    sys.path.insert(0, str(PROJECT_ROOT))

EXAMPLES = """\
examples:
  python run_eval.py                              full sweep (naive, 5 runs/task)
  python run_eval.py run --backend oracle --runs 1   calibrate: check every verify() works
  python run_eval.py run --backend naive --runs 5 --category frame
  python run_eval.py analyze --backend naive       rebuild report.md from results/*.json
  python run_eval.py list-tasks --category frame
  python run_eval.py list-backends
"""


# --------------------------------------------------------------------------- #
# argument parsing
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    """Build the top-level CLI parser.

    Returns:
        The configured parser, with one subcommand per verb.
    """
    parser = argparse.ArgumentParser(
        prog="run_eval.py",
        description=(
            "AgentEval — measure how reliably a browser-controlling agent completes "
            "real web tasks, by running every task N times and reporting the variance."
        ),
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version="AgentEval 0.1.0")
    subparsers = parser.add_subparsers(dest="command", metavar="{run,analyze,list-tasks,list-backends}")

    # ---- run ------------------------------------------------------------ #
    run_p = subparsers.add_parser(
        "run",
        help="run every task N times and write one JSON per run",
        description="Execute the (tasks x runs) sweep in isolated worker processes.",
    )
    run_p.add_argument("--backend", default="naive", help="backend under test (default: naive)")
    run_p.add_argument("--runs", type=int, default=5, help="repetitions per task (default: 5)")
    run_p.add_argument(
        "--results-dir",
        default=str(PROJECT_ROOT / "results"),
        help="root of the results tree (default: ./results)",
    )
    run_p.add_argument(
        "--timeout-s",
        type=float,
        default=60.0,
        help="hard wall-clock budget for ONE run, in seconds (default: 60)",
    )
    run_p.add_argument("--task", action="append", default=[], metavar="TASK_ID",
                       help="run only this task; repeatable")
    run_p.add_argument("--category", action="append", default=[], metavar="CATEGORY",
                       help="run only this category; repeatable")
    run_p.add_argument("--seed", type=int, default=20260831, help="base seed (default: 20260831)")
    run_p.add_argument("--tag", default="", help="free-form label stored in every result")
    run_p.add_argument("--headed", action="store_true", help="show the browser window")
    run_p.add_argument("--quiet", action="store_true", help="suppress per-run output")
    run_p.add_argument("--no-analyze", action="store_true", help="skip the automatic report build")
    run_p.add_argument("--no-chart", action="store_true", help="build the report without the PNG")
    run_p.add_argument("--option", action="append", default=[], metavar="KEY=VALUE",
                       help="backend-specific option; repeatable")
    run_p.add_argument("--dry-run", action="store_true",
                       help="print the sweep that would run, then exit")

    # ---- analyze --------------------------------------------------------- #
    analyze_p = subparsers.add_parser(
        "analyze",
        help="rebuild the report from results/*.json (no browser required)",
        description="Recompute every number from the JSON files on disk.",
    )
    analyze_p.add_argument("--backend", default=None,
                           help="only analyse runs from this backend (default: all)")
    analyze_p.add_argument("--session", default=None,
                           help="only analyse one session id, or 'latest'")
    analyze_p.add_argument("--results-dir", default=str(PROJECT_ROOT / "results"),
                           help="root of the results tree (default: ./results)")
    analyze_p.add_argument("--out-dir", default=None,
                           help="where to write the report (default: <results-dir>/reports/<backend>)")
    analyze_p.add_argument("--no-chart", action="store_true", help="skip the PNG")

    # ---- list-tasks ------------------------------------------------------ #
    tasks_p = subparsers.add_parser("list-tasks", help="print the task registry")
    tasks_p.add_argument("--category", default=None, help="filter by category")
    tasks_p.add_argument("--verbose", action="store_true", help="also print the instruction")

    # ---- list-backends --------------------------------------------------- #
    subparsers.add_parser("list-backends", help="print the backend registry and its availability")

    return parser


def _parse_kv(pairs: Sequence[str]) -> dict:
    """Parse ``KEY=VALUE`` CLI options.

    Args:
        pairs: Raw ``KEY=VALUE`` strings.

    Returns:
        The parsed mapping.
    """
    options: dict = {}
    for pair in pairs or []:
        if "=" not in pair:
            continue
        key, _, value = pair.partition("=")
        options[key.strip()] = value.strip()
    return options


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_run(args: argparse.Namespace) -> int:
    """Execute the sweep.

    Args:
        args: Parsed ``run`` arguments.

    Returns:
        Process exit code.
    """
    from aeval.backends import UnknownBackend, backend_names
    from aeval.backends.base import BackendUnavailable
    from aeval.runner import Runner, RunnerConfig

    try:
        config = RunnerConfig(
            backend=args.backend,
            runs=args.runs,
            results_dir=Path(args.results_dir),
            per_run_timeout_s=args.timeout_s,
            headless=not args.headed,
            task_ids=args.task or None,
            categories=args.category or None,
            seed=args.seed,
            tag=args.tag,
            extra_backend_options=_parse_kv(args.option),
        )
        runner = Runner(config)
    except UnknownBackend as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(f"available backends: {', '.join(backend_names())}", file=sys.stderr)
        return 2
    except BackendUnavailable as exc:
        print(f"error: backend '{args.backend}' is not available here: {exc}", file=sys.stderr)
        print("skipping gracefully — nothing was run, and nothing was reported as a result.",
              file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(runner.describe())
    if args.dry_run:
        for task in runner.tasks:
            print(f"  {task.task_id:34s} {task.category.value:8s} {task.difficulty.value:6s} "
                  f"{len(task.plan)} steps")
        return 0

    runner.run_all(verbose=not args.quiet)

    if not args.no_analyze:
        from aeval.analyzer import analyze, load_results, write_report

        results, skipped = load_results(config.results_dir, backend=config.backend,
                                        session=config.session)
        if skipped:
            print(f"note: skipped {len(skipped)} non-result JSON file(s)")
        if results:
            analysis = analyze(results, backend=config.backend)
            out_dir = Path(config.results_dir) / config.backend / config.session / "reports"
            written = write_report(analysis, out_dir, with_chart=not args.no_chart)
            print(f"report: {written['markdown']}")
            if written["chart"]:
                print(f"chart:  {written['chart']}")
            print(f"overall pass rate: {analysis.overall_pass_rate * 100:.1f}% "
                  f"({analysis.passed_runs}/{analysis.total_runs}), "
                  f"flaky tasks: {len(analysis.flaky_tasks)}")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    """Rebuild the report from disk.

    Args:
        args: Parsed ``analyze`` arguments.

    Returns:
        Process exit code.
    """
    from aeval.analyzer import analyze, load_results, write_report

    results_dir = Path(args.results_dir)
    results, skipped = load_results(results_dir, backend=args.backend, session=args.session)
    if not results:
        print(f"no result files found under {results_dir}")
        print("run `python run_eval.py run --backend naive` first.")
        return 1

    backend_label = args.backend or "all"
    analysis = analyze(results, backend=backend_label)
    # Grouped by backend so that `analyze` on two backends does not overwrite the
    # previous report.
    out_dir = Path(args.out_dir) if args.out_dir else results_dir / "reports" / backend_label
    written = write_report(analysis, out_dir, with_chart=not args.no_chart)

    print(f"analysed {len(results)} run(s) across {analysis.n_tasks} task(s)")
    if skipped:
        print(f"skipped {len(skipped)} non-result JSON file(s)")
    print(f"report: {written['markdown']}")
    if written["chart"]:
        print(f"chart:  {written['chart']}")
    else:
        print("chart:  skipped (matplotlib not installed)")
    print(f"overall pass rate: {analysis.overall_pass_rate * 100:.1f}% "
          f"({analysis.passed_runs}/{analysis.total_runs})")
    print(f"flaky tasks: {len(analysis.flaky_tasks)}  "
          f"stable: {len(analysis.stable_tasks)}  always-fail: {len(analysis.failing_tasks)}")
    return 0


def cmd_list_tasks(args: argparse.Namespace) -> int:
    """Print the task registry.

    Args:
        args: Parsed ``list-tasks`` arguments.

    Returns:
        Process exit code.
    """
    from aeval.tasks import all_tasks  # noqa: PLC0415 - populates the registry

    tasks = all_tasks()
    if args.category:
        tasks = [t for t in tasks if str(t.category) == args.category]
    print(f"{len(tasks)} task(s)\n")
    print(f"{'task_id':36s} {'category':9s} {'difficulty':10s} {'steps':>5s}  site")
    print("-" * 90)
    for task in tasks:
        print(f"{task.task_id:36s} {str(task.category):9s} {str(task.difficulty):10s} "
              f"{len(task.plan):5d}  {task.site}")
        if args.verbose:
            print(f"    {task.instruction}")
    return 0


def cmd_list_backends(args: argparse.Namespace) -> int:
    """Print the backend registry and probe availability.

    Args:
        args: Parsed ``list-backends`` arguments.

    Returns:
        Process exit code.
    """
    from aeval.backends import describe_backends

    print(f"{'backend':14s} {'available':10s} description")
    print("-" * 100)
    for name, description, available in describe_backends():
        flag = "yes" if available else "NO"
        print(f"{name:14s} {flag:10s} {description}")
    print("\nA 'NO' means a missing dependency or credential. See README.md -> Backends.")
    return 0


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument vector; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    # Bare `python run_eval.py` must run the default sweep — no manual steps. Global
    # flags (--help/--version) still belong to the top-level parser.
    known = {"run", "analyze", "list-tasks", "list-backends"}
    passthrough = {"-h", "--help", "--version"}
    if not argv:
        argv = ["run"]
    elif argv[0] not in known and argv[0] not in passthrough:
        argv = ["run"] + argv
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return cmd_run(args)
    if args.command == "analyze":
        return cmd_analyze(args)
    if args.command == "list-tasks":
        return cmd_list_tasks(args)
    if args.command == "list-backends":
        return cmd_list_backends(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
