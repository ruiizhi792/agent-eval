"""OracleBackend — the grader's calibration instrument.

**This is not an agent and it is not a subject under test.** It replays each task's
reference :class:`~aeval.schema.Step` plan with textbook-good browser hygiene:

* waits for elements to be visible before touching them,
* waits for navigation to settle,
* switches into iframes when a step is frame-scoped,
* arms an explicit handler for native JS dialogs,
* retries a failed step once before giving up.

Its only job is to answer one question: *is the ``verify`` function correct?*

A healthy oracle run should be close to 100% on the starter set. Any task where the
oracle fails is a bug in the task (stale selector, wrong expected value, too-short
wait) or in the harness — **not** an agent failure. Run it first, every time, before
you believe any other number.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable, Optional

from aeval.backends.base import AgentBackend, BackendUnavailable, require_playwright
from aeval.schema import Step, TaskSpec, Trajectory

__all__ = ["OracleBackend"]

#: How long a single robust op will wait for its element / navigation.
OP_TIMEOUT_MS = 10_000


def _frame_name(selector: str) -> str:
    """Extract a frame's name from a selector such as ``frame[name="frame-left"]``.

    Args:
        selector: A frame selector, or a bare frame name / id.

    Returns:
        The frame name to pass to ``page.frame(name=...)``.
    """
    match = re.search(r"""name\s*=\s*["']?([^"'\]]+)""", selector)
    if match:
        return match.group(1).strip()
    return selector.strip().lstrip("#.")

#: How long to wait for the network to go quiet after a navigation.
NETWORK_IDLE_MS = 5_000

#: Extra attempts after the first failure of a step.
MAX_ATTEMPTS = 2


class OracleBackend(AgentBackend):
    """Robust executor of a task's reference plan (calibration, not an agent)."""

    name = "oracle"
    description = (
        "Robust executor of the reference plan. Not an agent: calibrates the grader. "
        "Expect ~100%."
    )
    is_reference = True

    def check_available(self) -> None:
        """Verify Playwright is importable.

        Raises:
            BackendUnavailable: If Playwright is missing.
        """
        require_playwright()

    def setup(self, page: Any) -> None:
        """Apply oracle-grade defaults to the page and arm the dialog handler.

        Args:
            page: The page the run will use.
        """
        page.set_default_timeout(OP_TIMEOUT_MS)
        page.set_default_navigation_timeout(OP_TIMEOUT_MS)
        page.on("dialog", self._on_dialog)

    # -- dialogs ----------------------------------------------------------- #
    def _on_dialog(self, dialog: Any) -> None:
        """Accept every native dialog, remembering the last message.

        Args:
            dialog: The Playwright dialog object.
        """
        self._last_dialog_message = getattr(dialog, "message", "")
        dialog.accept()

    def run(self, task: TaskSpec, page: Any) -> Trajectory:
        """Replay ``task.plan`` with robust waits.

        Args:
            task: The task; ``task.plan`` is the reference action sequence.
            page: A fresh Playwright page.

        Returns:
            The recorded trajectory.
        """
        self._last_dialog_message = ""
        traj = Trajectory()
        if not task.plan:
            traj.error = f"task {task.task_id!r} has no reference plan; oracle cannot calibrate it"
            return traj

        for step in task.plan:
            self._run_step(page, traj, step)

        traj.final_url = page.url
        return traj

    # -- execution --------------------------------------------------------- #
    def _run_step(self, page: Any, traj: Trajectory, step: Step) -> None:
        """Execute one step, retrying once, recording the outcome.

        Args:
            page: Playwright page.
            traj: Trajectory being built.
            step: The step to execute.
        """
        last_error = ""
        for attempt in range(MAX_ATTEMPTS):
            started = time.perf_counter()
            try:
                self._dispatch(page, step)
                elapsed = int((time.perf_counter() - started) * 1000)
                traj.record(
                    step.op,
                    target=step.target,
                    value=step.value,
                    frame=step.frame,
                    ok=True,
                    elapsed_ms=elapsed,
                )
                return
            except Exception as exc:  # noqa: BLE001 - retry, then record
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt + 1 < MAX_ATTEMPTS:
                    self._settle(page)
        elapsed = int((time.perf_counter() - started) * 1000)
        traj.record(
            step.op,
            target=step.target,
            value=step.value,
            frame=step.frame,
            ok=False,
            error=last_error,
            elapsed_ms=elapsed,
        )

    def _settle(self, page: Any) -> None:
        """Give the page a short grace period before a retry.

        Args:
            page: Playwright page.
        """
        try:
            page.wait_for_load_state("domcontentloaded", timeout=2_000)
        except Exception:  # noqa: BLE001 - best-effort only
            pass

    def _dispatch(self, page: Any, step: Step) -> None:
        """Run a single op with proper waits.

        Args:
            page: Playwright page.
            step: The step to execute.

        Raises:
            ValueError: If the step declares an unknown op.
        """
        handler: Optional[Callable[[Any, Step], None]] = getattr(self, f"_op_{step.op}", None)
        if handler is None:
            raise ValueError(f"oracle: unsupported step op {step.op!r}")
        handler(page, step)

    # -- ops --------------------------------------------------------------- #
    def _op_goto(self, page: Any, step: Step) -> None:
        """Navigate, then wait for the document and (best effort) the network."""
        url = step.target or step.value
        page.goto(url, wait_until="domcontentloaded", timeout=OP_TIMEOUT_MS)
        try:
            page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_MS)
        except Exception:  # noqa: BLE001 - networkidle is a nice-to-have
            pass

    def _op_click(self, page: Any, step: Step) -> None:
        """Wait for visibility, then click."""
        page.wait_for_selector(step.target, state="visible", timeout=OP_TIMEOUT_MS)
        page.click(step.target, timeout=OP_TIMEOUT_MS)

    def _op_fill(self, page: Any, step: Step) -> None:
        """Wait for visibility, clear, then type."""
        page.wait_for_selector(step.target, state="visible", timeout=OP_TIMEOUT_MS)
        page.fill(step.target, step.value, timeout=OP_TIMEOUT_MS)

    def _op_press(self, page: Any, step: Step) -> None:
        """Focus an optional target, then press a key."""
        if step.target:
            page.wait_for_selector(step.target, state="visible", timeout=OP_TIMEOUT_MS)
            page.focus(step.target, timeout=OP_TIMEOUT_MS)
        page.keyboard.press(step.value)

    def _op_select(self, page: Any, step: Step) -> None:
        """Select an option by value, falling back to label."""
        page.wait_for_selector(step.target, state="visible", timeout=OP_TIMEOUT_MS)
        try:
            page.select_option(step.target, value=step.value, timeout=OP_TIMEOUT_MS)
        except Exception:  # noqa: BLE001 - some sites expose labels only
            page.select_option(step.target, label=step.value, timeout=OP_TIMEOUT_MS)

    def _op_check(self, page: Any, step: Step) -> None:
        """Ensure a checkbox is checked."""
        page.wait_for_selector(step.target, state="attached", timeout=OP_TIMEOUT_MS)
        page.set_checked(step.target, True, timeout=OP_TIMEOUT_MS)

    def _op_uncheck(self, page: Any, step: Step) -> None:
        """Ensure a checkbox is unchecked."""
        page.wait_for_selector(step.target, state="attached", timeout=OP_TIMEOUT_MS)
        page.set_checked(step.target, False, timeout=OP_TIMEOUT_MS)

    def _op_wait_for(self, page: Any, step: Step) -> None:
        """Block until the selector is visible."""
        page.wait_for_selector(step.target, state="visible", timeout=OP_TIMEOUT_MS)

    def _op_sleep(self, page: Any, step: Step) -> None:
        """Pause for the requested number of milliseconds."""
        page.wait_for_timeout(int(step.value or 0))

    def _op_frame_click(self, page: Any, step: Step) -> None:
        """Click an element inside an iframe.

        ``force=True`` is deliberate: rich-text bodies and other contenteditable
        targets often fail Playwright's actionability checks even though a real user
        can click them perfectly well. The oracle is allowed to be pragmatic here
        because it is not the thing being measured.
        """
        page.frame_locator(step.frame).locator(step.target).click(
            timeout=OP_TIMEOUT_MS, force=True
        )

    def _op_frame_fill(self, page: Any, step: Step) -> None:
        """Type into an element inside an iframe, replacing any existing content.

        ``locator.fill`` only works on ``<input>``, ``<textarea>`` and
        ``[contenteditable]`` elements, and rich-text editors are frequently neither
        (the-internet's TinyMCE is currently served read-only, so its body has
        ``contenteditable="false"``). Fall back to focus + select-all + keystrokes so
        the step degrades to "typed nothing" rather than a 10-second hang.
        """
        locator = page.frame_locator(step.frame).locator(step.target)
        locator.wait_for(state="visible", timeout=OP_TIMEOUT_MS)
        try:
            locator.fill(step.value, timeout=4_000)
            return
        except Exception:  # noqa: BLE001 - expected for read-only / non-form targets
            pass
        locator.click(timeout=4_000, force=True, no_wait_after=True)
        page.keyboard.press("ControlOrMeta+a")
        page.keyboard.press("Delete")
        page.keyboard.type(step.value)

    def _op_frame_read(self, page: Any, step: Step) -> None:
        """Read text out of a frame, resolving it by name.

        ``frame_locator`` only sees iframes in the *host* document, so it cannot reach
        a frame nested inside another frame (the-internet's ``/nested_frames`` puts
        left/middle/right inside ``frame-top``). Resolving through ``page.frames``
        — which lists the whole frame tree — is the only approach that works, and it
        is also what the corresponding ``verify`` uses.
        """
        name = _frame_name(step.frame)
        frame = page.frame(name=name)
        if frame is None:
            raise ValueError(f"no frame named {name!r} (selector was {step.frame!r})")
        frame.locator(step.target).inner_text(timeout=OP_TIMEOUT_MS)

    def _op_dialog_accept(self, page: Any, step: Step) -> None:
        """No-op: :meth:`setup` already accepts every dialog."""

    def _op_dialog_dismiss(self, page: Any, step: Step) -> None:
        """Dismiss the next dialog instead of accepting it.

        Args:
            page: Playwright page.
            step: The step (unused).
        """
        page.off("dialog", self._on_dialog)
        page.on("dialog", lambda dialog: dialog.dismiss())
