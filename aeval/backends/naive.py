"""NaiveBackend — a *hand-written fragile baseline*, deliberately not an LLM agent.

.. warning::

   **READ THIS BEFORE QUOTING ANY NUMBER PRODUCED BY THIS BACKEND.**

   ``NaiveBackend`` is a deterministic, rule-based script. It is not a language model,
   it makes no inference, and it never sees the page — it replays the task's reference
   plan (:class:`~aeval.schema.TaskSpec.plan`) with deliberately bad browser hygiene.
   Its failures are **engineered**, not observed.

   Consequences you must respect:

   * Its pass rate says **nothing** about how often an LLM browser agent would succeed.
     A real agent fails at *planning* (wrong action, wrong element) as well as at
     *execution*; the naive backend always knows the right plan and only botches the
     execution. It is therefore neither an upper nor a lower bound on LLM performance.
   * Its variance is **real** in the sense that it comes from genuine races against
     real page-load timing on real network, but the *magnitude* of that variance is
     a function of the time constants chosen below, which were guessed, not measured.

   **Why it exists.** It lets the harness be exercised end to end and produce a real,
   reproducible variance report with no API key, no LLM cost and no network dependency
   beyond the two test sites. It is a *harness fixture*, not a subject. Replace it with
   ``--backend browser-use`` (or your own backend) before you make any claim about
   agents.
"""

from __future__ import annotations

import random
import time
from typing import Any, Callable, Optional

from aeval.backends.base import AgentBackend, BackendUnavailable, require_playwright
from aeval.schema import Step, TaskSpec, Trajectory

__all__ = ["NaiveBackend"]

#: Selector timeout used by the fragile executor. Short on purpose: a real agent's
#: "impatient" behaviour is modelled as "give up quickly instead of waiting".
#:
#: GUESSED CONSTANT — see SELF_REPORT.md. It is also the single biggest lever on the
#: reported variance: too large and every race is won (85%, zero flaky tasks); too
#: small and every race is lost. Override with
#: ``--option op_timeout_ms=900`` and re-run until the races are genuinely undecided.
OP_TIMEOUT_MS = 900

#: How long the backend waits after its last action before declaring itself done.
#: This is the main driver of run-to-run variance: the grader snapshots the page the
#: instant the backend returns, so this window decides whether in-flight work has
#: landed yet. Override with ``--option settle_min_s=0.05 --option settle_max_s=2.5``.
#:
#: CALIBRATED, NOT GUESSED (see SELF_REPORT.md §3.6): the one genuinely slow deadline
#: in the starter set was measured at 5.43 s +/- 20 ms (the-internet's
#: /dynamic_loading/1). The grader then allows a further
#: ``worker.VERIFY_PHASE_TIMEOUT_MS`` = 4 s. A settle window of 0.05-2.5 s therefore
#: puts the total wait for that task at 4.05-6.55 s, straddling 5.43 s - which is what
#: makes it come out *flaky* rather than reliably passing or reliably failing.
FINAL_SETTLE_MIN_S = 0.05
FINAL_SETTLE_MAX_S = 2.50

#: Navigation wait strategy. "domcontentloaded" arrives before SPA hydration, which is
#: exactly the window in which impatient agents fire their first click.
NAV_WAIT_UNTIL = "domcontentloaded"
NAV_TIMEOUT_MS = 15_000

#: Randomised pause injected before every step, in seconds.
JITTER_MIN_S = 0.0
JITTER_MAX_S = 0.18

#: Probability of an extra "the agent is thinking" stall before a step.
THINKING_PAUSE_PROB = 0.12
THINKING_PAUSE_MIN_S = 0.20
THINKING_PAUSE_MAX_S = 0.60


class NaiveBackend(AgentBackend):
    """Perfect planner, fragile executor. A harness fixture, not an agent.

    The following real-world agent pathologies are modelled explicitly:

    * does **not** wait for deferred content (``wait_for`` is skipped),
    * does **not** switch into iframes (frame-scoped ops are run against the main DOM),
    * does **not** handle native JS dialogs (Playwright then auto-dismisses them),
    * clicks with ``force=True`` / ``no_wait_after=True``, so it fires before elements
      are stable and never waits for the navigation it triggers,
    * uses short, unforgiving timeouts and never retries,
    * injects randomised timing jitter so repeated runs are not identical.
    """

    name = "naive"
    description = (
        "Hand-written fragile baseline (NOT an LLM agent). Exercises the harness and "
        "produces real variance without an API key. Do not read its pass rate as an "
        "LLM pass rate."
    )
    is_reference = False

    def __init__(self, headless: bool = True, seed: int = 0, timeout_ms: int = 60_000, **options: Any) -> None:
        """Initialise the backend and its run-local RNG.

        Args:
            headless: Unused directly (the runner owns the browser) but kept for API parity.
            seed: Seed for the timing jitter; recorded in the result for reproducibility.
            timeout_ms: Overall run budget.
            **options: Extra options; ``op_timeout_ms`` overrides :data:`OP_TIMEOUT_MS`.
        """
        super().__init__(headless=headless, seed=seed, timeout_ms=timeout_ms, **options)
        self.op_timeout_ms = int(options.get("op_timeout_ms", OP_TIMEOUT_MS))
        self.settle_min_s = float(options.get("settle_min_s", FINAL_SETTLE_MIN_S))
        self.settle_max_s = float(options.get("settle_max_s", FINAL_SETTLE_MAX_S))
        self.rng = random.Random(seed)

    def check_available(self) -> None:
        """Verify Playwright is importable.

        Raises:
            BackendUnavailable: If Playwright is missing.
        """
        require_playwright()

    def setup(self, page: Any) -> None:
        """Apply impatient defaults.

        Args:
            page: The page the run will use.
        """
        page.set_default_timeout(self.op_timeout_ms)
        page.set_default_navigation_timeout(NAV_TIMEOUT_MS)

    # -- helpers ----------------------------------------------------------- #
    def _jitter(self, page: Any) -> None:
        """Pause for a small random interval, occasionally longer.

        Args:
            page: Playwright page used for the wait.
        """
        delay = self.rng.uniform(JITTER_MIN_S, JITTER_MAX_S)
        if self.rng.random() < THINKING_PAUSE_PROB:
            delay += self.rng.uniform(THINKING_PAUSE_MIN_S, THINKING_PAUSE_MAX_S)
        if delay > 0:
            page.wait_for_timeout(int(delay * 1000))

    # -- main entry point --------------------------------------------------- #
    def run(self, task: TaskSpec, page: Any) -> Trajectory:
        """Replay ``task.plan`` without waiting, without retries, without frames.

        Args:
            task: The task; ``task.plan`` provides the action sequence.
            page: A fresh Playwright page.

        Returns:
            The recorded trajectory, including every step that raised.
        """
        traj = Trajectory()
        if not task.plan:
            traj.error = f"task {task.task_id!r} has no reference plan; naive backend cannot act"
            return traj

        self._jitter(page)
        for step in task.plan:
            self._run_step(page, traj, step)

        # The agent "looks at the page" before declaring itself done. Randomised, so
        # repeated runs land at different points of the page's load timeline.
        settle = self.rng.uniform(self.settle_min_s, self.settle_max_s)
        page.wait_for_timeout(int(settle * 1000))
        traj.record("settle", value=f"{settle:.2f}s")

        traj.final_url = page.url
        return traj

    def _run_step(self, page: Any, traj: Trajectory, step: Step) -> None:
        """Execute one step fragily; never propagate an exception.

        Args:
            page: Playwright page.
            traj: Trajectory being built.
            step: The step to execute.
        """
        self._jitter(page)
        started = time.perf_counter()
        try:
            self._dispatch(page, step)
            error = ""
            ok = True
        except Exception as exc:  # noqa: BLE001 - fragile agents don't retry, they move on
            error = f"{type(exc).__name__}: {exc}"
            ok = False
        elapsed = int((time.perf_counter() - started) * 1000)
        traj.record(
            step.op,
            target=step.target,
            value=step.value,
            frame=step.frame,
            ok=ok,
            error=error,
            elapsed_ms=elapsed,
        )

    def _dispatch(self, page: Any, step: Step) -> None:
        """Route a step to its (deliberately imperfect) handler.

        Args:
            page: Playwright page.
            step: The step to execute.

        Raises:
            ValueError: If the step declares an unknown op.
        """
        handler: Optional[Callable[[Any, Step], None]] = getattr(self, f"_op_{step.op}", None)
        if handler is None:
            raise ValueError(f"naive: unsupported step op {step.op!r}")
        handler(page, step)

    # -- ops --------------------------------------------------------------- #
    def _op_goto(self, page: Any, step: Step) -> None:
        """Navigate and return as soon as the DOM exists, before hydration settles."""
        page.goto(step.target or step.value, wait_until=NAV_WAIT_UNTIL, timeout=NAV_TIMEOUT_MS)

    def _op_click(self, page: Any, step: Step) -> None:
        """Click without waiting for stability and without waiting for navigation."""
        page.click(step.target, timeout=self.op_timeout_ms, force=True, no_wait_after=True)

    def _op_fill(self, page: Any, step: Step) -> None:
        """Type into the first match without checking it is ready."""
        page.fill(step.target, step.value, timeout=self.op_timeout_ms)

    def _op_press(self, page: Any, step: Step) -> None:
        """Press a key, optionally after focusing the target."""
        if step.target:
            page.focus(step.target, timeout=self.op_timeout_ms)
        page.keyboard.press(step.value)

    def _op_select(self, page: Any, step: Step) -> None:
        """Change a <select> without waiting for the page to react."""
        page.select_option(step.target, step.value, timeout=self.op_timeout_ms, no_wait_after=True)

    def _op_check(self, page: Any, step: Step) -> None:
        """Check a checkbox by force."""
        page.set_checked(step.target, True, timeout=self.op_timeout_ms, force=True)

    def _op_uncheck(self, page: Any, step: Step) -> None:
        """Uncheck a checkbox by force."""
        page.set_checked(step.target, False, timeout=self.op_timeout_ms, force=True)

    def _op_wait_for(self, page: Any, step: Step) -> None:
        """Pathology #1: never wait for deferred content.

        The wait is replaced by a token pause so the trajectory still shows that the
        agent *intended* to wait and didn't.

        Args:
            page: Playwright page.
            step: The ignored step.
        """
        page.wait_for_timeout(self.rng.randint(0, 120))

    def _op_sleep(self, page: Any, step: Step) -> None:
        """Pathology #1b: truncate any explicit sleep; patience is not modelled."""
        page.wait_for_timeout(min(int(step.value or 0), 300))

    def _op_frame_click(self, page: Any, step: Step) -> None:
        """Pathology #2: never switch frames — the click is aimed at the main DOM."""
        page.click(step.target, timeout=self.op_timeout_ms, force=True, no_wait_after=True)

    def _op_frame_fill(self, page: Any, step: Step) -> None:
        """Pathology #2: never switch frames — the typing is aimed at the main DOM."""
        page.fill(step.target, step.value, timeout=self.op_timeout_ms)

    def _op_frame_read(self, page: Any, step: Step) -> None:
        """Pathology #2: never switch frames — the read is aimed at the main DOM."""
        page.inner_text(step.target, timeout=self.op_timeout_ms)

    def _op_dialog_accept(self, page: Any, step: Step) -> None:
        """Pathology #3: no dialog handler is armed, so Playwright auto-dismisses.

        Normally this is silent; instrumented here so the trajectory records the gap.
        """

    def _op_dialog_dismiss(self, page: Any, step: Step) -> None:
        """Pathology #3: identical to ``dialog_accept`` — no handler is ever armed."""
