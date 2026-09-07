"""Regression tests for the page that reaches the verifier.

These tests use fake Playwright objects; no browser process or network access is
required. Their purpose is to prevent an external-browser backend from accidentally
being graded against the runner's untouched page.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aeval.backends.base import AgentBackend, BackendUnavailable
from aeval.backends.browser_use import BrowserUseBackend
from aeval import schema
from aeval.schema import Category, Difficulty, TaskSpec, Trajectory, register_task
from aeval.worker import execute


class _Page:
    def __init__(self, url: str) -> None:
        self.url = url
        self.timeouts: list[int] = []

    def set_default_timeout(self, timeout: int) -> None:
        self.timeouts.append(timeout)

    def set_default_navigation_timeout(self, timeout: int) -> None:
        self.timeouts.append(timeout)


class _Context:
    def __init__(self, page: _Page) -> None:
        self.pages = [page]

    def new_page(self) -> _Page:
        return self.pages[0]


class _Browser:
    def __init__(self, context: _Context) -> None:
        self.contexts = [context]

    def new_context(self, **_kwargs: object) -> _Context:
        return self.contexts[0]

    def close(self) -> None:
        pass


class _Chromium:
    def __init__(self, browser: _Browser) -> None:
        self.browser = browser

    def launch(self, **_kwargs: object) -> _Browser:
        return self.browser

    def connect_over_cdp(self, _url: str) -> _Browser:
        return self.browser


class _Playwright:
    def __init__(self, browser: _Browser) -> None:
        self.chromium = _Chromium(browser)


class _PlaywrightContextManager:
    def __init__(self, playwright: _Playwright) -> None:
        self.playwright = playwright

    def __enter__(self) -> _Playwright:
        return self.playwright

    def __exit__(self, *_args: object) -> None:
        pass


class _ExternalPageBackend(AgentBackend):
    """A backend whose verifier page differs from the runner-owned page."""

    name = "external-test"

    def __init__(self, verification_page: _Page, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.verification_page = verification_page

    def run(self, task: TaskSpec, page: _Page) -> Trajectory:
        assert page.url == "https://runner.test/"
        return Trajectory(final_url=self.verification_page.url)

    def page_for_verification(self, page: _Page, playwright: object) -> _Page:
        assert page.url == "https://runner.test/"
        _ = playwright
        return self.verification_page


def test_worker_grades_the_backend_selected_page(monkeypatch, tmp_path: Path) -> None:
    """The runner page must not replace a backend's real browser page at grading time."""
    import aeval.worker as worker

    task = register_task(
        TaskSpec(
            task_id="worker_uses_verification_page",
            instruction="change the external page",
            category=Category.NAV,
            difficulty=Difficulty.EASY,
            start_url="https://runner.test/",
            verify=lambda page: page.url == "https://agent.test/done",
        )
    )
    runner_page = _Page("https://runner.test/")
    agent_page = _Page("https://agent.test/done")
    backend = _ExternalPageBackend(agent_page)
    fake_playwright = _Playwright(_Browser(_Context(runner_page)))

    monkeypatch.setattr(worker, "create_backend", lambda *_args, **_kwargs: backend)
    monkeypatch.setattr(worker, "sync_playwright", lambda: _PlaywrightContextManager(fake_playwright), raising=False)

    # ``execute`` imports this name lazily, so replace it at the package level too.
    import playwright.sync_api

    monkeypatch.setattr(playwright.sync_api, "sync_playwright", lambda: _PlaywrightContextManager(fake_playwright))
    try:
        result = execute(
            backend_name="external-test",
            task_id=task.task_id,
            run_index=0,
            out_path=str(tmp_path / "result.json"),
        )
    finally:
        schema._TASK_REGISTRY.pop(task.task_id, None)

    assert result.passed is True
    assert result.final_url == "https://agent.test/done"
    assert agent_page.timeouts[-1] == 4_000


def test_browser_use_selects_its_cdp_page() -> None:
    """A browser-use task is verified on its CDP page, not a supplied runner page."""
    wanted = _Page("https://agent.test/done")
    other = _Page("https://agent.test/other")
    fake_playwright = _Playwright(_Browser(_Context(other)))
    fake_playwright.chromium.browser.contexts.append(_Context(wanted))
    backend = BrowserUseBackend()
    backend._cdp_url = "http://127.0.0.1:9222"
    backend._agent_final_url = wanted.url

    assert backend.page_for_verification(None, fake_playwright) is wanted


def test_browser_use_refuses_to_grade_without_cdp() -> None:
    """Lack of a CDP endpoint is unavailable, never a silent blank-page grade."""
    with pytest.raises(BackendUnavailable):
        BrowserUseBackend().page_for_verification(None, object())
