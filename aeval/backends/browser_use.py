"""BrowserUseBackend — the real LLM-driven agent backend.

This is the only shipped backend that is an actual agent: it hands the task's
natural-language ``instruction`` to ``browser-use``, which drives a real browser in a
perception/reasoning/action loop with a language model in the middle.

It is also the only backend with external dependencies beyond Playwright, so it must
fail *softly*: missing package, missing API key, or an unmapped model all raise
:class:`~aeval.backends.base.BackendUnavailable` with an actionable hint instead of
crashing the run.

.. note::

   **This backend has NOT been executed in this environment** (no ``browser-use``
   installed, no API key, and the ``browser-use`` package has moved its LLM classes
   between versions). The import shims below cover the two layouts we know about, but
   you should treat this module as the most likely place to need a fix. See
   ``SELF_REPORT.md``.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, List

from aeval.backends.base import AgentBackend, BackendUnavailable
from aeval.schema import TaskSpec, Trajectory

__all__ = ["BrowserUseBackend"]

#: Environment variable read when ``--llm-model`` is not given.
MODEL_ENV_VAR = "AEVAL_LLM_MODEL"

#: Default model when neither CLI nor environment specifies one.
DEFAULT_MODEL = "gpt-4o-mini"

#: Environment variables any one of which satisfies the "an API key exists" check.
API_KEY_ENV_VARS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "AZURE_OPENAI_API_KEY")


class BrowserUseBackend(AgentBackend):
    """A genuine LLM browser agent, via the ``browser-use`` package.

    This backend manages its own browser (``browser-use`` owns the lifecycle), so the
    ``page`` handed in by the runner is left untouched. That is a deliberate
    asymmetry: the page exists so that *all* backends share one interface, not because
    every backend must use it.
    """

    name = "browser-use"
    description = (
        "Real LLM-driven agent via browser-use. Requires the package and an API key; "
        "skipped gracefully when either is missing."
    )
    is_reference = False

    def __init__(self, headless: bool = True, seed: int = 0, timeout_ms: int = 60_000, **options: Any) -> None:
        """Resolve the model and step budget.

        Args:
            headless: Whether ``browser-use`` should run its browser headless.
            seed: Accepted for API parity; LLMs are not seeded in practice.
            timeout_ms: Overall run budget, forwarded as an asyncio timeout.
            **options: ``model`` overrides the LLM, ``max_steps`` overrides the budget.
        """
        super().__init__(headless=headless, seed=seed, timeout_ms=timeout_ms, **options)
        self.model: str = str(options.get("model") or os.environ.get(MODEL_ENV_VAR) or DEFAULT_MODEL)
        self.max_steps: int = int(options.get("max_steps", 30))

    # -- availability ------------------------------------------------------ #
    def check_available(self) -> None:
        """Verify the package and credentials exist.

        Raises:
            BackendUnavailable: If ``browser-use`` is missing, if no LLM class can be
                located in the installed version, or if no API key is configured.
        """
        try:
            import browser_use  # noqa: F401,PLC0415 - availability probe
        except ImportError as exc:
            raise BackendUnavailable(
                "the 'browser-use' package is not installed",
                hint="pip install browser-use  (marked optional in requirements.txt)",
            ) from exc

        try:
            self._load_llm_class()
        except BackendUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - any mapping failure is "unsupported version"
            raise BackendUnavailable(
                f"installed browser-use version does not expose a known LLM class: {exc}",
                hint="See aeval/backends/browser_use.py::_load_llm_class and add your version's import path.",
            ) from exc

        if not any(os.environ.get(var) for var in API_KEY_ENV_VARS):
            raise BackendUnavailable(
                "no LLM API key found in the environment",
                hint="set one of: " + ", ".join(API_KEY_ENV_VARS),
            )

    def _load_llm_class(self) -> Any:
        """Locate a Chat LLM class across the ``browser-use`` versions we know.

        Returns:
            The LLM class (not an instance).

        Raises:
            BackendUnavailable: If no known import path resolves.
        """
        candidates = (
            ("browser_use", "ChatBrowserUse"),
            ("browser_use.llm", "ChatOpenAI"),
            ("browser_use.llm.openai", "ChatOpenAI"),
            ("langchain_openai", "ChatOpenAI"),
        )
        import importlib

        errors: List[str] = []
        for module_path, class_name in candidates:
            try:
                module = importlib.import_module(module_path)
            except ImportError as exc:
                errors.append(f"{module_path}: {exc}")
                continue
            cls = getattr(module, class_name, None)
            if cls is not None:
                return cls
            errors.append(f"{module_path}.{class_name}: missing")
        raise BackendUnavailable(
            "could not locate an LLM class for browser-use",
            hint="tried: " + "; ".join(errors),
        )

    def _build_llm(self) -> Any:
        """Instantiate the LLM for the configured model.

        Returns:
            An LLM object accepted by ``browser_use.Agent(llm=...)``.
        """
        cls = self._load_llm_class()
        try:
            return cls(model=self.model)
        except TypeError:
            # Some classes take a model *name* positionally or use `model_name`.
            try:
                return cls(model_name=self.model)
            except TypeError:
                return cls(self.model)

    # -- execution --------------------------------------------------------- #
    def run(self, task: TaskSpec, page: Any) -> Trajectory:
        """Drive ``browser-use`` to attempt the task.

        Args:
            task: The task; only ``task.instruction`` (and ``start_url``) are used —
                never ``task.plan``, which would be cheating.
            page: Unused; ``browser-use`` launches and owns its own browser.

        Returns:
            A trajectory built from the agent's action history.
        """
        started = time.perf_counter()
        self.check_available()
        try:
            history = asyncio.run(self._run_agent(task))
        except BackendUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced as a failed run, not a crash
            return _trajectory_from_exception(task, exc, started)

        return self._trajectory_from_history(task, history, started)

    async def _run_agent(self, task: TaskSpec) -> Any:
        """Run the agent coroutine with a wall-clock cap.

        Args:
            task: The task to attempt.

        Returns:
            Whatever ``agent.run()`` returns (usually an ``AgentHistoryList``).
        """
        from browser_use import Agent  # noqa: PLC0415 - lazy, heavy import

        llm = self._build_llm()
        kwargs: Dict[str, Any] = {"task": task.instruction, "llm": llm}
        agent = Agent(**kwargs)
        return await asyncio.wait_for(
            agent.run(max_steps=self.max_steps),
            timeout=max(self.timeout_ms, 1) / 1000.0,
        )

    def _trajectory_from_history(self, task: TaskSpec, history: Any, started: float) -> Trajectory:
        """Convert a ``browser-use`` history object into our trajectory format.

        Args:
            task: The task that was attempted.
            history: The object returned by ``agent.run()``.
            started: ``perf_counter()`` value taken before the run.

        Returns:
            The recorded trajectory.
        """
        traj = Trajectory()
        urls: List[str] = []

        # browser-use exposes several shapes across versions; handle them defensively
        # and never let a shape mismatch lose the whole run.
        items: List[Any] = []
        for attr in ("history", "all_results", "extracted_content"):
            candidate = getattr(history, attr, None)
            if isinstance(candidate, list) and candidate:
                items = candidate
                break

        for index, item in enumerate(items):
            action = _safe_getattr_chain(item, ("model_output", "action"), default=None)
            action_repr = _first_jsonish(action) if action is not None else ""
            state = _safe_getattr_chain(item, ("state", "url"), default=None)
            if isinstance(state, str) and state:
                urls.append(state)
            error = getattr(item, "error", None)
            traj.record(
                "agent_step",
                target=urls[-1] if urls else "",
                value=str(action_repr)[:500],
                ok=error is None,
                error=str(error)[:500] if error else "",
            )
            _ = index

        final_url = urls[-1] if urls else ""
        traj.final_url = final_url or task.start_url
        traj.error = ""
        _ = started
        return traj


def _safe_getattr_chain(obj: Any, path: tuple, default: Any = None) -> Any:
    """Walk a chain of attributes, returning ``default`` if any link is missing.

    Args:
        obj: Root object.
        path: Attribute names to follow.
        default: Value returned when the chain breaks.

    Returns:
        The resolved attribute, or ``default``.
    """
    current = obj
    for name in path:
        current = getattr(current, name, None)
        if current is None:
            return default
    return current


def _first_jsonish(action: Any) -> Any:
    """Render an action object as something short and loggable.

    Args:
        action: A ``browser-use`` action object (often a pydantic model).

    Returns:
        A string or dict representation of the action.
    """
    for attr in ("model_dump", "dict"):
        method = getattr(action, attr, None)
        if callable(method):
            try:
                data = method()
                if isinstance(data, dict) and data:
                    # agents usually emit exactly one action per step
                    return next(iter(data.values()))
                return data
            except Exception:  # noqa: BLE001 - best-effort rendering
                pass
    return str(action)


def _trajectory_from_exception(task: TaskSpec, exc: BaseException, started: float) -> Trajectory:
    """Build a failed trajectory from an exception.

    Args:
        task: The task that was attempted.
        exc: The exception raised.
        started: ``perf_counter()`` value taken before the run.

    Returns:
        A single-step trajectory describing the failure.
    """
    traj = Trajectory()
    traj.record("agent_run", target=task.start_url, ok=False, error=f"{type(exc).__name__}: {exc}")
    traj.final_url = task.start_url
    traj.error = f"{type(exc).__name__}: {exc}"
    _ = started
    return traj
