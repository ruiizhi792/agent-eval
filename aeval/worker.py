"""Worker process: executes exactly one (task, repetition) trial and writes one JSON.

Invoked by :class:`aeval.runner.Runner` as::

    python -m aeval.worker --backend naive --task-id sauce_login_success \
        --run-index 0 --out results/naive/<session>/....json

The worker owns the browser lifecycle (launch -> context -> page -> close) and is
responsible for producing a result file even when everything goes wrong. It exits 0
when a verdict was reached, 1 when the backend or grader blew up — the runner records
either way, and a missing file plus a kill is interpreted as a timeout.

Keeping this in its own module (rather than in ``runner.py``) is what makes the hard
timeout possible: the runner can ``kill()`` this process without corrupting its own
state.
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from typing import Any, Optional, Sequence

from aeval.backends import create_backend
from aeval.backends.base import BackendUnavailable
from aeval.schema import RunResult, Trajectory, evaluate_verify, get_task

#: Page default timeout while the *backend* is driving.
BACKEND_PHASE_TIMEOUT_MS = 15_000

#: Page default timeout while the *grader* is inspecting.
VERIFY_PHASE_TIMEOUT_MS = 4_000

__all__ = ["main", "build_parser", "execute"]


def build_parser() -> argparse.ArgumentParser:
    """Build the worker CLI.

    Returns:
        The configured argument parser.
    """
    parser = argparse.ArgumentParser(
        prog="python -m aeval.worker",
        description="Execute one AgentEval trial and write its JSON result.",
    )
    parser.add_argument("--backend", required=True, help="registered backend name")
    parser.add_argument("--task-id", required=True, help="task to attempt")
    parser.add_argument("--run-index", type=int, required=True, help="0-based repetition index")
    parser.add_argument("--out", required=True, help="destination JSON path")
    parser.add_argument("--headless", default="1", help="'1' for headless, '0' for headed")
    parser.add_argument("--timeout-ms", type=int, default=60_000, help="run budget in ms")
    parser.add_argument("--seed", type=int, default=0, help="run seed (recorded verbatim)")
    parser.add_argument("--session", default="", help="session id from the runner")
    parser.add_argument("--tag", default="", help="free-form label from the runner")
    parser.add_argument(
        "--option",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="backend-specific option; repeatable",
    )
    return parser


def _parse_options(raw: Sequence[str]) -> dict:
    """Turn ``["model=gpt-4o", "max_steps=20"]`` into a dict.

    Args:
        raw: ``KEY=VALUE`` strings.

    Returns:
        The parsed mapping.
    """
    options: dict = {}
    for item in raw or []:
        if "=" not in item:
            continue
        key, _, value = item.partition("=")
        options[key.strip()] = value.strip()
    return options


def execute(
    *,
    backend_name: str,
    task_id: str,
    run_index: int,
    out_path: str,
    headless: bool = True,
    timeout_ms: int = 60_000,
    seed: int = 0,
    session: str = "",
    tag: str = "",
    options: Optional[dict] = None,
) -> RunResult:
    """Run one trial end to end and persist it.

    This function never raises: every failure mode is written into the result so the
    statistics stay honest.

    Args:
        backend_name: Registered backend name.
        task_id: Task to attempt.
        run_index: Zero-based repetition index.
        out_path: Destination JSON path.
        headless: Whether to launch Chromium headless.
        timeout_ms: Run budget, forwarded to the backend.
        seed: Run seed, recorded verbatim.
        session: Session id, recorded verbatim.
        tag: Free-form label, recorded verbatim.
        options: Backend-specific options.

    Returns:
        The persisted :class:`RunResult`.
    """
    options = options or {}
    started = time.perf_counter()
    result = RunResult(
        session=session,
        task_id=task_id,
        backend=backend_name,
        run_index=run_index,
        seed=seed,
        tag=tag,
    )
    try:
        task = get_task(task_id)
    except KeyError:
        result.passed = False
        result.error_type = "harness"
        result.error = f"unknown task id {task_id!r}"
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        result.write_json(out_path)
        return result

    result.instruction = task.instruction
    result.category = str(task.category)
    result.difficulty = str(task.difficulty)
    result.start_url = task.start_url
    result.site = task.site

    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415 - heavy, lazy
    except ImportError:
        result.passed = False
        result.error_type = "harness"
        result.error = "playwright is not installed; cannot execute browser runs"
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        result.write_json(out_path)
        return result

    playwright_cm: Any = None
    browser: Any = None
    backend: Any = None
    try:
        backend = create_backend(
            backend_name,
            headless=headless,
            seed=seed,
            timeout_ms=timeout_ms,
            **options,
        )
        backend.check_available()

        playwright_cm = sync_playwright()
        playwright = playwright_cm.__enter__()
        page: Any = None
        if backend.uses_runner_page:
            browser = playwright.chromium.launch(headless=headless)
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            page.set_default_timeout(BACKEND_PHASE_TIMEOUT_MS)
            page.set_default_navigation_timeout(BACKEND_PHASE_TIMEOUT_MS)
            backend.setup(page)
        trajectory: Trajectory = backend.run(task, page)

        result.steps = [step.to_dict() for step in trajectory.steps]
        result.n_steps = trajectory.n_steps
        result.n_failed_steps = trajectory.n_failed_steps
        verification_page = backend.page_for_verification(page, playwright)
        result.final_url = trajectory.final_url or verification_page.url
        if trajectory.error:
            result.error = trajectory.error
            result.error_type = "backend"

        verification_page.set_default_timeout(VERIFY_PHASE_TIMEOUT_MS)
        outcome = evaluate_verify(task, verification_page)
        result.checks = [check.to_dict() for check in outcome.checks]
        result.checks_summary = outcome.summary()
        result.passed = outcome.passed
        if not outcome.passed and not result.error:
            result.error = outcome.summary()
            result.error_type = "verify"
    except BackendUnavailable as exc:
        result.passed = False
        result.error_type = "unavailable"
        result.error = str(exc)
    except Exception as exc:  # noqa: BLE001 - a crashed run is still a data point
        result.passed = False
        result.error_type = result.error_type or "harness"
        result.error = f"{type(exc).__name__}: {exc}"
        result.logs = (result.logs + "\n" + traceback.format_exc()).strip()
    finally:
        try:
            if backend is not None:
                backend.teardown()
        except Exception:  # noqa: BLE001 - teardown must not mask the real result
            pass
        try:
            if browser is not None:
                browser.close()
        except Exception:  # noqa: BLE001 - teardown must not mask the real result
            pass
        try:
            if playwright_cm is not None:
                playwright_cm.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass

    result.duration_ms = int((time.perf_counter() - started) * 1000)
    result.write_json(out_path)
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument vector; defaults to ``sys.argv[1:]``.

    Returns:
        ``0`` when a verdict was reached, ``1`` when the backend or grader failed.
    """
    args = build_parser().parse_args(argv)
    result = execute(
        backend_name=args.backend,
        task_id=args.task_id,
        run_index=args.run_index,
        out_path=args.out,
        headless=args.headless != "0",
        timeout_ms=args.timeout_ms,
        seed=args.seed,
        session=args.session,
        tag=args.tag,
        options=_parse_options(args.option),
    )
    status = "PASS" if result.passed else "FAIL"
    print(f"[{status}] {result.task_id} run {result.run_index} backend={result.backend}")
    if result.error:
        print(f"    error_type={result.error_type} error={result.error}")
    if result.checks_summary:
        print(f"    checks: {result.checks_summary}")
    return 0 if result.passed else 1


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess
    sys.exit(main())
