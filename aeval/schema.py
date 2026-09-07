"""Core data model for AgentEval.

Contents
--------
* :class:`Category` / :class:`Difficulty` — the two label axes of a task.
* :class:`ActionStep` / :class:`Trajectory` — what the agent did.
* :class:`VerifyCheck` / :class:`VerifyOutcome` — what the grader concluded.
* :class:`Step` — one primitive instruction of the *reference action plan*.
* :class:`TaskSpec` — the full definition of a task.
* :class:`RunResult` — one (task, repetition) trial, serialised to JSON.
* the module-level task registry (``register_task`` / ``get_task`` / ``all_tasks``).

Two conventions worth knowing before you read further:

**Plan vs. instruction.** A :class:`TaskSpec` carries *both* a natural-language
``instruction`` (what an LLM-driven agent is told) and an optional ``plan`` (a
sequence of primitive browser steps). The plan is consumed by the deterministic
backends (``oracle`` = robust executor, ``naive`` = fragile executor) so that we can
calibrate the harness without an API key. :meth:`TaskSpec.for_agent` returns a copy
with the plan removed — that is the object you should hand to a real agent, otherwise
you are leaking the answer key.

**Grading is explicit.** ``verify`` returns a :class:`VerifyOutcome`, i.e. a list of
named boolean checks, so a failure always says *which* assertion broke. A plain
``bool`` return value is still accepted for convenience and wrapped automatically.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

__all__ = [
    "SCHEMA_VERSION",
    "Category",
    "Difficulty",
    "CATEGORY_ORDER",
    "ActionStep",
    "Trajectory",
    "VerifyCheck",
    "VerifyOutcome",
    "Step",
    "TaskSpec",
    "RunResult",
    "VerifyFn",
    "register_task",
    "get_task",
    "all_tasks",
    "tasks_by_category",
    "task_ids",
    "clear_registry",
    "evaluate_verify",
    "normalize_url",
]

#: Bumped whenever the on-disk RunResult shape changes incompatibly.
SCHEMA_VERSION = "1.0.0"


def _utc_now_iso() -> str:
    """Return the current UTC time as a second-precision ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class Category(str, Enum):
    """Coarse capability a task exercises."""

    AUTH = "auth"
    NAV = "nav"
    FORM = "form"
    TABLE = "table"
    DYNAMIC = "dynamic"
    FRAME = "frame"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


#: Canonical display / iteration order for categories.
CATEGORY_ORDER: Tuple[Category, ...] = (
    Category.AUTH,
    Category.NAV,
    Category.FORM,
    Category.TABLE,
    Category.DYNAMIC,
    Category.FRAME,
)


class Difficulty(str, Enum):
    """Expected difficulty, assigned by hand when the task is written."""

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# --------------------------------------------------------------------------- #
# Trajectory
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ActionStep:
    """One action the backend claims to have taken.

    Attributes:
        index: Zero-based position within the trajectory.
        op: Action verb, e.g. ``goto`` / ``click`` / ``fill`` / ``select``.
        target: Selector, URL or element description the op was applied to.
        value: Payload (text to type, option to select, key to press).
        frame: Selector of the iframe the op was scoped to, if any.
        ok: Whether the op completed without raising.
        error: Human-readable failure reason when ``ok`` is False.
        elapsed_ms: Wall-clock duration of this single op.
    """

    index: int = 0
    op: str = ""
    target: str = ""
    value: str = ""
    frame: str = ""
    ok: bool = True
    error: str = ""
    elapsed_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable copy of this step."""
        return {
            "index": self.index,
            "op": self.op,
            "target": self.target,
            "value": self.value,
            "frame": self.frame,
            "ok": self.ok,
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class Trajectory:
    """Ordered record of what a backend did during one run.

    Attributes:
        steps: The action sequence, in order.
        final_url: URL of the page when the backend handed control back.
        error: Backend-level error message (empty when the run completed).
    """

    steps: List[ActionStep] = field(default_factory=list)
    final_url: str = ""
    error: str = ""

    def record(
        self,
        op: str,
        target: str = "",
        value: str = "",
        frame: str = "",
        ok: bool = True,
        error: str = "",
        elapsed_ms: int = 0,
    ) -> ActionStep:
        """Append one step and return it.

        Args:
            op: Action verb.
            target: Selector / URL the op applied to.
            value: Payload of the op.
            frame: Iframe selector, when the op was frame-scoped.
            ok: Whether the op succeeded.
            error: Failure reason, if any.
            elapsed_ms: Duration of the op.

        Returns:
            The newly created :class:`ActionStep`.
        """
        step = ActionStep(
            index=len(self.steps),
            op=op,
            target=target,
            value=value,
            frame=frame,
            ok=ok,
            error=error,
            elapsed_ms=elapsed_ms,
        )
        self.steps.append(step)
        return step

    @property
    def n_steps(self) -> int:
        """Number of recorded steps."""
        return len(self.steps)

    @property
    def n_failed_steps(self) -> int:
        """Number of recorded steps that raised."""
        return sum(1 for step in self.steps if not step.ok)

    @property
    def ok(self) -> bool:
        """True when the backend reported no error and no step raised."""
        return not self.error and self.n_failed_steps == 0

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable copy of this trajectory."""
        return {
            "steps": [step.to_dict() for step in self.steps],
            "final_url": self.final_url,
            "error": self.error,
        }


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #
@dataclass
class VerifyCheck:
    """A single named assertion made by a ``verify`` function.

    Attributes:
        name: Short machine-friendly assertion name, e.g. ``cart_badge_is_3``.
        passed: Outcome of the assertion.
        detail: Observed value — this is what makes a failure debuggable.
    """

    name: str = ""
    passed: bool = False
    detail: str = ""

    @classmethod
    def of(cls, name: str, condition: bool, detail: str = "") -> "VerifyCheck":
        """Build a check from a boolean plus the value that produced it.

        Args:
            name: Assertion name.
            condition: Result of the assertion.
            detail: Observed value, recorded for debugging.

        Returns:
            The constructed :class:`VerifyCheck`.
        """
        return cls(name=name, passed=bool(condition), detail=detail)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable copy of this check."""
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass
class VerifyOutcome:
    """Result of grading a page: a conjunction of named checks.

    Attributes:
        checks: The individual assertions.
    """

    checks: List[VerifyCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True when at least one check exists and all of them passed."""
        return bool(self.checks) and all(check.passed for check in self.checks)

    @property
    def failed(self) -> List[VerifyCheck]:
        """The checks that did not pass."""
        return [check for check in self.checks if not check.passed]

    def summary(self) -> str:
        """Return a one-line summary, naming the first failed check if any."""
        if self.passed:
            return f"all {len(self.checks)} checks passed"
        if not self.checks:
            return "no checks were produced"
        first = self.failed[0]
        return f"{len(self.failed)}/{len(self.checks)} checks failed (first: {first.name} -> {first.detail})"

    @classmethod
    def from_bool(cls, value: bool, name: str = "verify") -> "VerifyOutcome":
        """Wrap a plain boolean in a single-check outcome.

        Args:
            value: Grading result.
            name: Name to give the synthetic check.

        Returns:
            A :class:`VerifyOutcome` with exactly one check.
        """
        return cls(checks=[VerifyCheck(name=name, passed=bool(value), detail=str(value))])

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable copy of this outcome."""
        return {"passed": self.passed, "checks": [c.to_dict() for c in self.checks]}


#: Signature every ``verify`` function must have. ``bool`` is accepted and wrapped.
VerifyFn = Callable[[Any], Union[bool, VerifyOutcome]]


# --------------------------------------------------------------------------- #
# Plans
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Step:
    """One primitive instruction of a task's reference action plan.

    Supported ``op`` values (all are executed by :class:`OracleBackend`; the fragile
    :class:`NaiveBackend` deliberately mishandles a subset of them):

    ======================  ==================================================
    op                      meaning
    ======================  ==================================================
    ``goto``                navigate to ``target`` (or the task start URL)
    ``click``               click ``target``
    ``fill``                set ``target`` to ``value``
    ``press``               press key ``value``, optionally focused on ``target``
    ``select``              select option with value ``value`` in ``target``
    ``check``/``uncheck``   set checkbox ``target``
    ``wait_for``            block until ``target`` is visible
    ``sleep``               pause for ``int(value)`` milliseconds
    ``frame_click``         click ``target`` inside iframe ``frame``
    ``frame_fill``          type ``value`` into ``target`` inside iframe ``frame``
    ``frame_read``          read ``target`` inside iframe ``frame``
    ``dialog_accept``       arm a handler that accepts the next JS dialog
    ``dialog_dismiss``      arm a handler that dismisses the next JS dialog
    ======================  ==================================================
    """

    op: str = ""
    target: str = ""
    value: str = ""
    frame: str = ""
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable copy of this step."""
        return {
            "op": self.op,
            "target": self.target,
            "value": self.value,
            "frame": self.frame,
            "note": self.note,
        }


# --------------------------------------------------------------------------- #
# TaskSpec
# --------------------------------------------------------------------------- #
@dataclass(eq=False)
class TaskSpec:
    """The complete definition of one evaluation task.

    Attributes:
        task_id: Stable unique identifier, e.g. ``sauce_add_to_cart_three``.
        instruction: Natural-language instruction handed to the agent under test.
        category: Capability bucket.
        difficulty: Human-assigned expected difficulty.
        start_url: Where the browser should start.
        verify: Grading function ``page -> bool | VerifyOutcome``.
        plan: Reference action plan used by the deterministic backends.
        site: Hostname, for grouping and for README sanity checks.
        timeout_ms: Hard wall-clock budget for a single run of this task.
        tags: Free-form labels.
        notes: Anything a reviewer should know about this task.
    """

    task_id: str = ""
    instruction: str = ""
    category: Category = Category.NAV
    difficulty: Difficulty = Difficulty.EASY
    start_url: str = ""
    verify: VerifyFn = lambda page: False  # type: ignore[assignment]
    plan: Tuple[Step, ...] = ()
    site: str = ""
    timeout_ms: int = 60_000
    tags: Tuple[str, ...] = ()
    notes: str = ""

    def __post_init__(self) -> None:
        """Validate the fields that are cheap to validate.

        Raises:
            ValueError: If ``task_id`` is empty or ``verify`` is not callable.
        """
        if not self.task_id:
            raise ValueError("TaskSpec.task_id must be a non-empty string")
        if not callable(self.verify):
            raise ValueError(f"TaskSpec.verify must be callable (task={self.task_id})")
        if not self.instruction:
            raise ValueError(f"TaskSpec.instruction must be non-empty (task={self.task_id})")

    def for_agent(self) -> "TaskSpec":
        """Return a copy with the answer key (``plan``) removed.

        Hand this to any agent that is *not* one of the deterministic backends,
        otherwise the agent can trivially cheat by reading the reference plan.

        Returns:
            A shallow copy whose ``plan`` is empty.
        """
        return replace(self, plan=())

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable description (never includes callables)."""
        return {
            "task_id": self.task_id,
            "instruction": self.instruction,
            "category": str(self.category),
            "difficulty": str(self.difficulty),
            "start_url": self.start_url,
            "site": self.site,
            "timeout_ms": self.timeout_ms,
            "tags": list(self.tags),
            "notes": self.notes,
            "n_plan_steps": len(self.plan),
            "plan": [step.to_dict() for step in self.plan],
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"TaskSpec(task_id={self.task_id!r}, category={self.category.value!r}, "
            f"difficulty={self.difficulty.value!r}, steps={len(self.plan)})"
        )


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
_TASK_REGISTRY: Dict[str, TaskSpec] = {}


def register_task(spec: TaskSpec) -> TaskSpec:
    """Add a task to the global registry.

    Args:
        spec: The task to register.

    Returns:
        The same spec, so ``register_task(TaskSpec(...))`` can be used inline.

    Raises:
        ValueError: If a task with the same id is already registered.
    """
    if spec.task_id in _TASK_REGISTRY:
        raise ValueError(f"Duplicate task_id: {spec.task_id!r}")
    _TASK_REGISTRY[spec.task_id] = spec
    return spec


def get_task(task_id: str) -> TaskSpec:
    """Look a task up by id.

    Args:
        task_id: Identifier of the task.

    Returns:
        The registered :class:`TaskSpec`.

    Raises:
        KeyError: If no such task is registered.
    """
    return _TASK_REGISTRY[task_id]


def all_tasks() -> List[TaskSpec]:
    """Return every registered task, sorted by category then id."""
    return sorted(
        _TASK_REGISTRY.values(),
        key=lambda spec: (CATEGORY_ORDER.index(spec.category), spec.task_id),
    )


def task_ids() -> List[str]:
    """Return every registered task id, sorted."""
    return sorted(_TASK_REGISTRY)


def tasks_by_category(category: Union[Category, str]) -> List[TaskSpec]:
    """Return the tasks in one category.

    Args:
        category: A :class:`Category` or its string value.

    Returns:
        Matching tasks, sorted by id.
    """
    wanted = Category(category) if isinstance(category, str) else category
    return [spec for spec in all_tasks() if spec.category is wanted]


def clear_registry() -> None:
    """Drop every registered task. Intended for tests."""
    _TASK_REGISTRY.clear()


# --------------------------------------------------------------------------- #
# RunResult
# --------------------------------------------------------------------------- #
@dataclass
class RunResult:
    """One (task, repetition) trial — the atomic unit of evidence in AgentEval.

    Every run writes exactly one of these to ``results/`` and nothing is ever
    overwritten, so the report can always be recomputed from disk.
    """

    schema_version: str = SCHEMA_VERSION
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    session: str = ""
    task_id: str = ""
    instruction: str = ""
    category: str = ""
    difficulty: str = ""
    start_url: str = ""
    site: str = ""
    backend: str = ""
    run_index: int = 0
    seed: int = 0
    tag: str = ""
    timestamp: str = field(default_factory=_utc_now_iso)
    passed: bool = False
    final_url: str = ""
    error: str = ""
    error_type: str = ""
    duration_ms: int = 0
    checks: List[Dict[str, Any]] = field(default_factory=list)
    checks_summary: str = ""
    steps: List[Dict[str, Any]] = field(default_factory=list)
    n_steps: int = 0
    n_failed_steps: int = 0
    logs: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable copy of this result."""
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "session": self.session,
            "task_id": self.task_id,
            "instruction": self.instruction,
            "category": self.category,
            "difficulty": self.difficulty,
            "start_url": self.start_url,
            "site": self.site,
            "backend": self.backend,
            "run_index": self.run_index,
            "seed": self.seed,
            "tag": self.tag,
            "timestamp": self.timestamp,
            "passed": self.passed,
            "final_url": self.final_url,
            "error": self.error,
            "error_type": self.error_type,
            "duration_ms": self.duration_ms,
            "checks": self.checks,
            "checks_summary": self.checks_summary,
            "steps": self.steps,
            "n_steps": self.n_steps,
            "n_failed_steps": self.n_failed_steps,
            "logs": self.logs,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RunResult":
        """Rebuild a result from a parsed JSON object.

        Unknown keys are ignored so that older/newer files stay readable.

        Args:
            data: Parsed JSON mapping.

        Returns:
            The reconstructed :class:`RunResult`.
        """
        known = {f: data[f] for f in cls().__dict__.keys() if f in data}
        return cls(**known)

    def write_json(self, path: "Path | str") -> Path:
        """Serialise to ``path``, creating parent directories.

        Args:
            path: Destination file.

        Returns:
            The path written, as a :class:`~pathlib.Path`.
        """
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return out

    @classmethod
    def read_json(cls, path: "Path | str") -> "RunResult":
        """Load a result previously written by :meth:`write_json`."""
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def normalize_url(url: str) -> str:
    """Strip a trailing slash so root URLs compare equal across sites.

    Args:
        url: Any absolute URL.

    Returns:
        The URL without a trailing slash.
    """
    return url.rstrip("/")


def evaluate_verify(task: TaskSpec, page: Any) -> VerifyOutcome:
    """Run a task's ``verify`` function defensively.

    A ``verify`` that raises is recorded as a failed check rather than crashing the
    run, because a crash in the grader is itself a finding worth reporting.

    Args:
        task: Task whose ``verify`` should run.
        page: Live Playwright page.

    Returns:
        A :class:`VerifyOutcome`. Never raises.
    """
    try:
        outcome = task.verify(page)
    except Exception as exc:  # noqa: BLE001 - grader bugs must not kill the run
        return VerifyOutcome(
            checks=[
                VerifyCheck(
                    name="verify_raised",
                    passed=False,
                    detail=f"{type(exc).__name__}: {exc}",
                )
            ]
        )
    if isinstance(outcome, bool):
        return VerifyOutcome.from_bool(outcome)
    if isinstance(outcome, VerifyOutcome):
        return outcome
    return VerifyOutcome(
        checks=[
            VerifyCheck(
                name="verify_bad_return_type",
                passed=False,
                detail=f"verify returned {type(outcome).__name__}, expected bool or VerifyOutcome",
            )
        ]
    )
