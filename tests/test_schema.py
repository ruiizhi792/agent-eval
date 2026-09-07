"""Tests for the schema, the registry and the starter task set.

Everything here runs offline. The task-set tests are the important ones: they are the
guard rails that stop the STARTER_SET from silently rotting (duplicate ids, missing
categories, tasks without a plan, verify functions that assert nothing).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aeval import schema
from aeval.schema import (
    CATEGORY_ORDER,
    Category,
    Difficulty,
    RunResult,
    Step,
    TaskSpec,
    Trajectory,
    VerifyCheck,
    VerifyOutcome,
    all_tasks,
    evaluate_verify,
    get_task,
    normalize_url,
    register_task,
    task_ids,
    tasks_by_category,
)


# --------------------------------------------------------------------------- #
# enums
# --------------------------------------------------------------------------- #
def test_category_str_is_its_value() -> None:
    """str(Category.AUTH) must be 'auth', not 'Category.AUTH'."""
    assert str(Category.AUTH) == "auth"
    assert str(Difficulty.HARD) == "hard"


def test_all_six_categories_are_ordered() -> None:
    """The canonical order contains every category exactly once."""
    assert len(CATEGORY_ORDER) == 6
    assert len(set(CATEGORY_ORDER)) == 6


# --------------------------------------------------------------------------- #
# TaskSpec
# --------------------------------------------------------------------------- #
def _dummy_verify(page) -> bool:
    """A verify function used only in these tests."""
    return True


def _spec(**overrides) -> TaskSpec:
    """Build a valid TaskSpec with sensible defaults."""
    defaults = dict(
        task_id="unit_task",
        instruction="do the thing",
        category=Category.NAV,
        difficulty=Difficulty.EASY,
        start_url="https://example.test/",
        verify=_dummy_verify,
    )
    defaults.update(overrides)
    return TaskSpec(**defaults)


def test_task_spec_requires_id_and_instruction() -> None:
    """Empty ids and instructions are rejected at construction time."""
    with pytest.raises(ValueError):
        _spec(task_id="")
    with pytest.raises(ValueError):
        _spec(instruction="")


def test_task_spec_rejects_non_callable_verify() -> None:
    """A verify that is not callable fails immediately, not mid-sweep."""
    with pytest.raises(ValueError):
        _spec(verify="not callable")


def test_for_agent_strips_the_answer_key() -> None:
    """``for_agent()`` must remove the plan so a real agent cannot cheat."""
    spec = _spec(plan=(Step("goto", "https://example.test/"),))
    assert len(spec.plan) == 1
    assert spec.for_agent().plan == ()
    assert len(spec.plan) == 1  # original untouched


def test_task_spec_to_dict_is_json_serialisable() -> None:
    """The serialised spec must round-trip through JSON (no callables)."""
    spec = _spec(plan=(Step("click", "#go"),), tags=("demo",))
    payload = json.loads(json.dumps(spec.to_dict()))
    assert payload["task_id"] == "unit_task"
    assert payload["category"] == "nav"
    assert payload["plan"][0]["op"] == "click"


def test_duplicate_task_ids_are_rejected() -> None:
    """The registry refuses to shadow an existing task id."""
    register_task(_spec(task_id="dup_test_task"))
    with pytest.raises(ValueError):
        register_task(_spec(task_id="dup_test_task"))
    schema._TASK_REGISTRY.pop("dup_test_task", None)


# --------------------------------------------------------------------------- #
# VerifyOutcome
# --------------------------------------------------------------------------- #
def test_verify_outcome_passes_only_when_all_checks_pass() -> None:
    """An outcome is a conjunction, and an empty one is a failure."""
    assert VerifyOutcome([]).passed is False
    assert VerifyOutcome([VerifyCheck("a", True), VerifyCheck("b", True)]).passed is True
    partial = VerifyOutcome([VerifyCheck("a", True), VerifyCheck("b", False, "saw 2")])
    assert partial.passed is False
    assert partial.failed[0].name == "b"
    assert "b" in partial.summary()


def test_evaluate_verify_wraps_bool_and_catches_exceptions() -> None:
    """bool, VerifyOutcome and a raising verify are all handled without propagating."""
    ok = evaluate_verify(_spec(verify=lambda page: True), page=None)
    assert ok.passed is True

    def boom(page):
        raise RuntimeError("kaboom")

    bad = evaluate_verify(_spec(verify=boom), page=None)
    assert bad.passed is False
    assert "kaboom" in bad.checks[0].detail


def test_normalize_url_strips_trailing_slash() -> None:
    """Root URLs compare equal regardless of the trailing slash."""
    assert normalize_url("https://a.test/") == "https://a.test"
    assert normalize_url("https://a.test/x") == "https://a.test/x"


# --------------------------------------------------------------------------- #
# Trajectory
# --------------------------------------------------------------------------- #
def test_trajectory_records_steps_and_health() -> None:
    """Steps are indexed, failures are counted, and the dict form is serialisable."""
    traj = Trajectory()
    traj.record("goto", target="https://example.test/")
    traj.record("click", target="#go", ok=False, error="timeout")
    assert traj.n_steps == 2
    assert traj.n_failed_steps == 1
    assert traj.ok is False
    assert traj.to_dict()["steps"][1]["error"] == "timeout"


# --------------------------------------------------------------------------- #
# RunResult
# --------------------------------------------------------------------------- #
def test_run_result_round_trips_through_json(tmp_path: Path) -> None:
    """A result written to disk can be read back identically."""
    result = RunResult(
        task_id="t",
        run_index=2,
        backend="naive",
        passed=True,
        checks=[{"name": "x", "passed": True, "detail": ""}],
        steps=[{"index": 0, "op": "goto", "ok": True}],
    )
    path = tmp_path / "nested" / "run.json"
    result.write_json(path)
    reloaded = RunResult.read_json(path)
    assert reloaded.task_id == "t"
    assert reloaded.run_index == 2
    assert reloaded.passed is True
    assert reloaded.checks == result.checks


def test_run_result_from_dict_ignores_unknown_keys() -> None:
    """Forward compatibility: unknown keys are dropped, not fatal."""
    result = RunResult.from_dict({"task_id": "t", "some_future_field": 1})
    assert result.task_id == "t"


# --------------------------------------------------------------------------- #
# the STARTER_SET
# --------------------------------------------------------------------------- #
def test_starter_set_has_about_twenty_unique_tasks() -> None:
    """The starter set is ~20 tasks with unique ids."""
    ids = task_ids()
    assert len(ids) >= 18
    assert len(set(ids)) == len(ids)


def test_every_category_has_at_least_two_tasks() -> None:
    """No category may be a single data point."""
    for category in CATEGORY_ORDER:
        assert len(tasks_by_category(category)) >= 2, f"category {category} is under-covered"


def test_every_task_is_well_formed() -> None:
    """Each task has an instruction, a start url, a plan, a difficulty and a verifier."""
    for spec in all_tasks():
        assert spec.instruction.strip(), spec.task_id
        assert spec.start_url.startswith("https://"), spec.task_id
        assert spec.plan, f"{spec.task_id} has no reference plan"
        assert spec.difficulty in tuple(Difficulty), spec.task_id
        assert callable(spec.verify), spec.task_id
        for step in spec.plan:
            assert step.op, f"{spec.task_id} has a step with no op"


def test_only_automation_friendly_sites_are_targeted() -> None:
    """A guard rail: no task may target a site that forbids automation."""
    allowed_hosts = {"www.saucedemo.com", "the-internet.herokuapp.com"}
    for spec in all_tasks():
        assert spec.site in {"saucedemo.com", "the-internet.herokuapp.com"}, spec.task_id
        host = spec.start_url.split("//", 1)[-1].split("/", 1)[0]
        assert host in allowed_hosts, f"{spec.task_id} targets {host}"


def test_get_task_round_trip() -> None:
    """Registry lookup returns the same object that was registered."""
    spec = get_task("sauce_login_success")
    assert spec.task_id == "sauce_login_success"
    assert spec.category is Category.AUTH


def test_all_plan_ops_are_known_to_both_deterministic_backends() -> None:
    """Every op used by the starter set must have a handler in oracle and naive.

    This is the test that stops a typo like ``frame_fill`` vs ``fill_frame`` from
    becoming a silent "0% pass rate" in the report.
    """
    from aeval.backends.naive import NaiveBackend
    from aeval.backends.oracle import OracleBackend

    used_ops = {step.op for spec in all_tasks() for step in spec.plan}
    for op in used_ops:
        assert hasattr(OracleBackend, f"_op_{op}"), f"oracle has no handler for {op!r}"
        assert hasattr(NaiveBackend, f"_op_{op}"), f"naive has no handler for {op!r}"


def test_backend_registry_names() -> None:
    """The three documented backends are registered under their documented names."""
    from aeval.backends import backend_names

    assert set(backend_names()) == {"oracle", "naive", "browser-use"}
