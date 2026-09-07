"""The runner: N repetitions per task, one immutable JSON file per run, hard timeouts.

Design notes
------------

**One process per run.** Each (task, repetition) pair is executed in a fresh
``python -m aeval.worker`` subprocess. This is the only way to get a *true* hard
timeout: a Playwright call can block forever (a navigation that never commits, a
dialog nobody dismisses), and no in-process watchdog can safely interrupt a
synchronous Playwright call. With a subprocess, :func:`subprocess.run` gives us
``timeout=`` plus ``kill()``, and a crashed run costs one file, not the evaluation.

**Nothing is overwritten.** Result paths embed a session id, the task id, the
repetition index, a timestamp and a random suffix::

    results/<backend>/<session>/<task_id>__run03__20260831T104512Z__a1b2c3d4.json

**Timeouts are failures, not crashes.** A run that hits the wall clock is recorded as
``passed=False, error_type="timeout"`` so it shows up in the statistics like any other
failure. Hanging is not allowed to be invisible.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from aeval.backends import create_backend, UnknownBackend
from aeval.backends.base import BackendUnavailable
from aeval.schema import Category, RunResult, TaskSpec, all_tasks, get_task

__all__ = ["RunnerConfig", "Runner", "RunOutcome", "new_session_id", "DEFAULT_RESULTS_DIR"]

#: Repository root (the directory that contains ``aeval/``).
PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: Where results go unless ``--results-dir`` says otherwise.
DEFAULT_RESULTS_DIR = PROJECT_ROOT / "results"

#: Default number of repetitions per task. The protocol: never report a single run.
DEFAULT_RUNS = 5

#: Default wall-clock budget for a single run, in seconds.
DEFAULT_TIMEOUT_S = 60.0

#: Truncation limit for captured worker stdout/stderr stored in the result.
LOG_TAIL_CHARS = 4000


def new_session_id() -> str:
    """Build a filesystem-safe, chronologically sortable session id.

    Returns:
        A string like ``20260831T104512Z``.
    """
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@dataclass
class RunnerConfig:
    """Everything the runner needs to know, in one place.

    Attributes:
        backend: Registry name of the backend under test.
        runs: Repetitions per task.
        results_dir: Root of the results tree.
        per_run_timeout_s: Hard wall-clock budget for one run, in seconds.
        headless: Whether the browser runs headless.
        task_ids: Optional whitelist of task ids.
        categories: Optional whitelist of categories.
        seed: Base seed; each repetition uses ``seed + run_index``.
        session: Session id; generated when empty.
        tag: Free-form label recorded in every result.
        extra_backend_options: Forwarded to the backend constructor.
    """

    backend: str = "naive"
    runs: int = DEFAULT_RUNS
    results_dir: Path = DEFAULT_RESULTS_DIR
    per_run_timeout_s: float = DEFAULT_TIMEOUT_S
    headless: bool = True
    task_ids: Optional[Sequence[str]] = None
    categories: Optional[Sequence[Union[Category, str]]] = None
    seed: int = 20260831
    session: str = ""
    tag: str = ""
    extra_backend_options: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalise paths, defaults and whitelists.

        Raises:
            ValueError: If ``runs`` is not positive or the timeout is not positive.
        """
        self.results_dir = Path(self.results_dir)
        if self.runs < 1:
            raise ValueError(f"runs must be >= 1, got {self.runs}")
        if self.per_run_timeout_s <= 0:
            raise ValueError(f"per_run_timeout_s must be > 0, got {self.per_run_timeout_s}")
        if not self.session:
            self.session = new_session_id()
        if self.categories is not None:
            self.categories = [Category(c) if isinstance(c, str) else c for c in self.categories]


@dataclass
class RunOutcome:
    """The runner's view of one repetition.

    Attributes:
        result: The parsed result (always present — a timeout still yields one).
        path: Where the JSON lives.
        timed_out: Whether the hard timeout fired.
        returncode: Worker exit code, or ``None`` if the process was killed.
    """

    result: RunResult
    path: Path
    timed_out: bool = False
    returncode: Optional[int] = None


class Runner:
    """Executes ``tasks x repetitions`` trials and persists one JSON file per trial."""

    def __init__(self, config: RunnerConfig) -> None:
        """Validate the configuration and resolve the task list.

        Args:
            config: Runner configuration.

        Raises:
            UnknownBackend: If the requested backend is not registered.
            BackendUnavailable: If the backend cannot run in this environment.
        """
        self.config = config
        # Fail fast and loudly on a bad/missing backend, before any browser starts.
        probe = create_backend(config.backend, headless=config.headless, seed=config.seed)
        probe.check_available()
        self.backend = probe
        self.tasks: List[TaskSpec] = self._select_tasks()
        self.outcomes: List[RunOutcome] = []
        self.session_dir: Path = config.results_dir / config.backend / config.session
        self.session_dir.mkdir(parents=True, exist_ok=True)

    # -- task selection ---------------------------------------------------- #
    def _select_tasks(self) -> List[TaskSpec]:
        """Filter the registry by the configured whitelists.

        Returns:
            The tasks to run, in canonical order.
        """
        tasks = all_tasks()
        if self.config.categories:
            wanted = set(self.config.categories)
            tasks = [t for t in tasks if t.category in wanted]
        if self.config.task_ids:
            wanted_ids = set(self.config.task_ids)
            unknown = wanted_ids - set(t.task_id for t in tasks)
            if unknown:
                raise ValueError(f"unknown task ids: {sorted(unknown)}")
            tasks = [t for t in tasks if t.task_id in wanted_ids]
        return tasks

    @property
    def total_runs(self) -> int:
        """Number of trials the full sweep will execute."""
        return len(self.tasks) * self.config.runs

    def describe(self) -> str:
        """Return a one-paragraph description of the planned sweep."""
        return (
            f"backend={self.config.backend}  tasks={len(self.tasks)}  "
            f"runs_per_task={self.config.runs}  total_runs={self.total_runs}  "
            f"timeout={self.config.per_run_timeout_s:g}s  session={self.config.session}"
        )

    # -- execution ---------------------------------------------------------- #
    def run_all(self, verbose: bool = True) -> List[RunOutcome]:
        """Execute the whole sweep.

        Args:
            verbose: Whether to print one line per run.

        Returns:
            Every :class:`RunOutcome`, in execution order.
        """
        self.outcomes = []
        started = time.perf_counter()
        for task in self.tasks:
            for index in range(self.config.runs):
                outcome = self.run_once(task, index)
                self.outcomes.append(outcome)
                if verbose:
                    flag = "PASS" if outcome.result.passed else "FAIL"
                    extra = " (timeout)" if outcome.timed_out else ""
                    print(
                        f"[{flag}] {task.task_id} run {index + 1}/{self.config.runs}"
                        f"  {outcome.result.duration_ms}ms{extra}"
                    )
        elapsed = time.perf_counter() - started
        self._write_manifest(elapsed)
        if verbose:
            n_passed = sum(1 for o in self.outcomes if o.result.passed)
            print(
                f"\n{self.config.backend}: {n_passed}/{len(self.outcomes)} runs passed "
                f"in {elapsed:.1f}s -> {self.session_dir}"
            )
        return self.outcomes

    def run_once(self, task: TaskSpec, run_index: int) -> RunOutcome:
        """Execute one (task, repetition) trial in a subprocess.

        Args:
            task: The task to attempt.
            run_index: Zero-based repetition index.

        Returns:
            The :class:`RunOutcome`, always with a persisted JSON file.
        """
        path = self._result_path(task, run_index)
        seed = self.config.seed + run_index
        cmd: List[str] = [
            sys.executable,
            "-m",
            "aeval.worker",
            "--backend",
            self.config.backend,
            "--task-id",
            task.task_id,
            "--run-index",
            str(run_index),
            "--out",
            str(path),
            "--headless",
            "1" if self.config.headless else "0",
            "--timeout-ms",
            str(int(task.timeout_ms)),
            "--seed",
            str(seed),
            "--session",
            self.config.session,
            "--tag",
            self.config.tag,
        ]
        for key, value in (self.config.extra_backend_options or {}).items():
            cmd += ["--option", f"{key}={value}"]

        started = time.perf_counter()
        timed_out = False
        returncode: Optional[int] = None
        logs = ""
        try:
            completed = subprocess.run(
                cmd,
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=max(self.config.per_run_timeout_s, 1.0),
                check=False,
            )
            returncode = completed.returncode
            logs = ((completed.stdout or "") + (completed.stderr or ""))[-LOG_TAIL_CHARS:]
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = exc.stdout or b""
            stderr = exc.stderr or b""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            logs = (stdout + stderr)[-LOG_TAIL_CHARS:]
        except OSError as exc:
            logs = f"failed to start worker: {exc}"

        duration_ms = int((time.perf_counter() - started) * 1000)

        result = self._load_or_synthesise(task, run_index, path, seed, duration_ms, logs, timed_out)
        return RunOutcome(result=result, path=path, timed_out=timed_out, returncode=returncode)

    def _load_or_synthesise(
        self,
        task: TaskSpec,
        run_index: int,
        path: Path,
        seed: int,
        duration_ms: int,
        logs: str,
        timed_out: bool,
    ) -> RunResult:
        """Read the worker's JSON, or fabricate one when the worker could not write it.

        Args:
            task: The task attempted.
            run_index: Repetition index.
            path: Expected JSON location.
            seed: Seed used for the run.
            duration_ms: Wall-clock duration in milliseconds.
            logs: Captured worker output.
            timed_out: Whether the hard timeout fired.

        Returns:
            A :class:`RunResult`, always persisted to ``path``.
        """
        if path.exists():
            try:
                result = RunResult.read_json(path)
            except Exception as exc:  # noqa: BLE001 - corrupt file must not lose the run
                result = self._blank_result(task, run_index, seed)
                result.error = f"unreadable result file: {exc}"
                result.error_type = "harness"
            result.duration_ms = max(result.duration_ms, 0) or duration_ms
            result.logs = logs
            if timed_out:
                result.error_type = "timeout"
                result.error = result.error or "hard timeout: worker exceeded its wall-clock budget"
                result.passed = False
            result.write_json(path)
            return result

        result = self._blank_result(task, run_index, seed)
        result.duration_ms = duration_ms
        result.logs = logs
        result.passed = False
        if timed_out:
            result.error = (
                f"hard timeout: worker was killed after {self.config.per_run_timeout_s:g}s"
            )
            result.error_type = "timeout"
        else:
            result.error = "worker produced no result file (crashed before writing JSON)"
            result.error_type = "harness"
        result.write_json(path)
        return result

    def _blank_result(self, task: TaskSpec, run_index: int, seed: int) -> RunResult:
        """Build a result skeleton populated from the task metadata.

        Args:
            task: The task.
            run_index: Repetition index.
            seed: Seed used for the run.

        Returns:
            A :class:`RunResult` with ``passed=False``.
        """
        return RunResult(
            session=self.config.session,
            task_id=task.task_id,
            instruction=task.instruction,
            category=str(task.category),
            difficulty=str(task.difficulty),
            start_url=task.start_url,
            site=task.site,
            backend=self.config.backend,
            run_index=run_index,
            seed=seed,
            passed=False,
        )

    def _result_path(self, task: TaskSpec, run_index: int) -> Path:
        """Compute a unique, never-reused result path.

        Args:
            task: The task.
            run_index: Repetition index.

        Returns:
            The destination path (parent directories are created by the writer).
        """
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        suffix = f"{run_index:02d}"
        # The random suffix makes an overwrite structurally impossible even if the
        # same session somehow re-runs the same (task, index) within one second.
        name = f"{task.task_id}__run{suffix}__{stamp}__{uuid.uuid4().hex[:6]}.json"
        return self.session_dir / name

    def _write_manifest(self, elapsed_s: float) -> Path:
        """Persist a small session manifest describing the sweep.

        Args:
            elapsed_s: Wall-clock duration of the sweep.

        Returns:
            The manifest path.
        """
        manifest = {
            "session": self.config.session,
            "backend": self.config.backend,
            "tag": self.config.tag,
            "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "runs_per_task": self.config.runs,
            "per_run_timeout_s": self.config.per_run_timeout_s,
            "headless": self.config.headless,
            "seed": self.config.seed,
            "n_tasks": len(self.tasks),
            "n_runs": len(self.outcomes),
            "n_passed": sum(1 for o in self.outcomes if o.result.passed),
            "elapsed_s": round(elapsed_s, 2),
            "python": sys.version.split()[0],
            "tasks": [t.task_id for t in self.tasks],
            "results": [str(o.path.relative_to(self.config.results_dir)) for o in self.outcomes],
        }
        path = self.session_dir / "session_manifest.json"
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return path
