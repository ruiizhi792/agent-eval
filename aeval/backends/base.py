"""The pluggable-agent contract.

Anything that can drive a browser can be evaluated by AgentEval, as long as it
subclasses :class:`AgentBackend` and implements :meth:`AgentBackend.run`.

The three shipped backends are deliberately at different points on the
"is this a real agent?" axis:

=====================  ==========================================================
``oracle``             Deterministic, robust executor of the reference plan.
                       **Not an agent.** It exists to calibrate the grader: a
                       healthy oracle run should be ~100%, and any task where it
                       is not has a broken ``verify`` (or a broken plan).
``naive``              Deterministic, *fragile* executor of the same plan.
                       **Also not an agent** — see the long warning in
                       :mod:`aeval.backends.naive`. It exists so the harness can be
                       exercised end-to-end and can emit a real variance report with
                       no API key and no network LLM calls.
``browser-use``        A genuine LLM-driven browser agent, via the ``browser-use``
                       package. Missing dependency or missing API key must degrade
                       to :class:`BackendUnavailable`, never to a crash.
=====================  ==========================================================
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict

from aeval.schema import TaskSpec, Trajectory

__all__ = ["BackendUnavailable", "AgentBackend", "require_playwright"]


class BackendUnavailable(RuntimeError):
    """Raised when a backend cannot run in the current environment.

    Attributes:
        message: What is missing or misconfigured.
        hint: The exact command the user should run to fix it.
    """

    def __init__(self, message: str, hint: str = "") -> None:
        """Store the reason and an actionable hint.

        Args:
            message: Human-readable reason.
            hint: Remediation command or note.
        """
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        """Return the message plus the hint, when there is one."""
        if self.hint:
            return f"{self.message}\n  hint: {self.hint}"
        return self.message


def require_playwright() -> Any:
    """Import ``playwright.sync_api`` or explain how to install it.

    Returns:
        The ``playwright.sync_api`` module.

    Raises:
        BackendUnavailable: If Playwright is not installed.
    """
    try:
        from playwright import sync_api  # noqa: PLC0415 - intentional lazy import
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise BackendUnavailable(
            "playwright is not installed in this interpreter",
            hint=(
                "pip install playwright  (pinned in requirements.txt) "
                "then: playwright install chromium"
            ),
        ) from exc
    return sync_api


class AgentBackend(ABC):
    """Abstract base class for everything AgentEval can evaluate.

    Subclasses must set :attr:`name` and implement :meth:`run`. Everything else has
    a safe default so that adding a backend stays cheap.
    """

    #: Registry key used on the CLI (``--backend oracle``).
    name: str = "base"

    #: One-line human description, shown by ``run_eval.py list-backends``.
    description: str = ""

    #: True for backends that are calibration tools rather than subjects under test.
    is_reference: bool = False

    def __init__(
        self,
        headless: bool = True,
        seed: int = 0,
        timeout_ms: int = 60_000,
        **options: Any,
    ) -> None:
        """Store the common configuration.

        Args:
            headless: Whether the browser should run headless.
            seed: Per-run seed; write it into the result so runs are reproducible.
            timeout_ms: Wall-clock budget for a single run.
            **options: Backend-specific options, kept on ``self.options``.
        """
        self.headless = headless
        self.seed = seed
        self.timeout_ms = timeout_ms
        self.options: Dict[str, Any] = dict(options)

    @abstractmethod
    def run(self, task: TaskSpec, page: Any) -> Trajectory:
        """Drive the browser to attempt ``task``.

        Implementations should never raise: catch, record the error on the
        :class:`~aeval.schema.Trajectory`, and return. A raised exception is
        recorded by the runner as ``error_type="backend"`` and the run is scored
        as failed, so raising is survivable but loses trajectory detail.

        Args:
            task: The task to attempt. Use ``task.instruction`` as the prompt; the
                deterministic backends additionally use ``task.plan``.
            page: A fresh Playwright page already positioned at ``about:blank``.

        Returns:
            The :class:`~aeval.schema.Trajectory` describing what happened.
        """

    def setup(self, page: Any) -> None:
        """Hook called once before :meth:`run`, e.g. to register dialog handlers.

        Args:
            page: The page the run will use.
        """

    def teardown(self) -> None:
        """Hook called after :meth:`run`, e.g. to close a private browser."""

    def check_available(self) -> None:
        """Raise :class:`BackendUnavailable` if this backend cannot run here.

        The default implementation does nothing (the backend only needs a page).
        """

    def is_available(self) -> bool:
        """Return whether :meth:`check_available` passes.

        Returns:
            True when the backend is usable in this environment.
        """
        try:
            self.check_available()
        except BackendUnavailable:
            return False
        return True

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{type(self).__name__}(name={self.name!r}, headless={self.headless})"
