"""AgentEval — a variance-first evaluation harness for browser-controlling agents.

Design goals (in priority order):

1. **Reproducibility.** Every run of every task writes one immutable JSON file under
   ``results/``. Nothing is ever overwritten, and every number in the report can be
   recomputed from those files alone (``python run_eval.py analyze`` needs no browser).
2. **Programmatic success criteria.** A task is only "passed" if its ``verify(page)``
   asserts concrete state (exact URL, exact element text, exact row count ...).
   No "looks right".
3. **Variance, not just averages.** Each task is run N times (default 5) so that
   *flaky* tasks — tasks that sometimes pass and sometimes fail — become visible.
4. **Pluggable agents.** The thing under test is an :class:`~aeval.backends.base.AgentBackend`.
   The harness does not care whether that is a hand-written script, a rule-based
   baseline, or an LLM-driven agent.

This package is intentionally import-light: ``import aeval`` must succeed even when
optional heavy dependencies (playwright, matplotlib, browser-use) are absent, so that
``run_eval.py --help``, the offline analyzer and the test suite always work.
"""

from __future__ import annotations

from aeval.schema import (
    Category,
    Difficulty,
    RunResult,
    TaskSpec,
    Trajectory,
    all_tasks,
    get_task,
)

# Importing ``aeval.tasks`` is what *populates* the task registry: the task modules
# call ``register_task()`` at import time. Without this line the registry is empty and
# the CLI would silently report "0 tasks" — a bug that happened during development and
# is now guarded by tests/test_schema.py. It costs nothing: task modules import only
# stdlib and aeval.schema, never playwright.
from aeval import tasks as _tasks  # noqa: F401

__version__ = "0.1.0"

__all__ = [
    "Category",
    "Difficulty",
    "RunResult",
    "TaskSpec",
    "Trajectory",
    "all_tasks",
    "get_task",
    "__version__",
]
