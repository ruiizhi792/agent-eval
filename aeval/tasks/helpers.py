"""Small, dependency-free helpers for writing robust ``verify(page)`` functions.

Every helper swallows Playwright exceptions and returns a sentinel instead. That is
intentional: a grader must always produce a verdict, and "the selector was not there"
*is* the verdict (a False / empty / -1 result), not a crash.

None of these import Playwright — they are duck-typed on ``page`` — so this module
stays importable in a bare interpreter, which keeps the offline test path fast.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, TypeVar

T = TypeVar("T")

__all__ = [
    "safe",
    "ntext",
    "count_of",
    "text_of",
    "texts_of",
    "value_of",
    "visible",
    "checked",
    "frame_texts",
    "frame_text_of",
    "money",
]


def safe(fn: Callable[[], T], default: T) -> T:
    """Call ``fn`` and fall back to ``default`` if it raises.

    Args:
        fn: Zero-argument callable to evaluate.
        default: Value returned when ``fn`` raises.

    Returns:
        ``fn()`` on success, ``default`` otherwise.
    """
    try:
        return fn()
    except Exception:  # noqa: BLE001 - a grader must never propagate
        return default


def ntext(value: Any) -> str:
    """Collapse whitespace and strip, tolerating ``None``.

    Args:
        value: Raw text, possibly ``None``.

    Returns:
        A single-space-separated stripped string.
    """
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def count_of(page: Any, selector: str) -> int:
    """Count elements matching ``selector``.

    Args:
        page: Playwright page or frame.
        selector: CSS (or Playwright) selector.

    Returns:
        The match count, or ``-1`` if the query failed.
    """
    return safe(lambda: int(page.locator(selector).count()), -1)


def text_of(page: Any, selector: str, timeout: int = 4000, default: str = "") -> str:
    """Read the (normalised) text of the first match of ``selector``.

    Args:
        page: Playwright page or frame.
        selector: CSS selector.
        timeout: Milliseconds to wait for the element.
        default: Value returned when the element is missing.

    Returns:
        Normalised text, or ``default``.
    """
    return safe(lambda: ntext(page.locator(selector).first.inner_text(timeout=timeout)), default)


def texts_of(page: Any, selector: str) -> List[str]:
    """Read the normalised text of every match of ``selector``.

    Note: Playwright's ``all_inner_texts()`` takes no timeout argument, so this
    helper cannot wait — call :func:`visible` first if the list may still be
    rendering (the dynamic-loading tasks do exactly that).

    Args:
        page: Playwright page or frame.
        selector: CSS selector.

    Returns:
        A list of normalised strings (possibly empty).
    """
    return safe(lambda: [ntext(t) for t in page.locator(selector).all_inner_texts()], [])


def value_of(page: Any, selector: str, timeout: int = 4000, default: str = "") -> str:
    """Read the ``value`` property of the first matching form control.

    Args:
        page: Playwright page or frame.
        selector: CSS selector.
        timeout: Milliseconds to wait for the element.
        default: Value returned when the element is missing.

    Returns:
        The input value, or ``default``.
    """
    return safe(lambda: ntext(page.locator(selector).first.input_value(timeout=timeout)), default)


def visible(page: Any, selector: str, timeout: int = 4000) -> bool:
    """Return whether the first match of ``selector`` is visible.

    Args:
        page: Playwright page or frame.
        selector: CSS selector.
        timeout: Milliseconds to wait before giving up.

    Returns:
        True when visible, False when hidden, missing, or on error.
    """
    return safe(lambda: bool(page.locator(selector).first.is_visible(timeout=timeout)), False)


def checked(page: Any, selector: str, timeout: int = 4000) -> bool:
    """Return whether the first matching checkbox is checked.

    Args:
        page: Playwright page or frame.
        selector: CSS selector.
        timeout: Milliseconds to wait before giving up.

    Returns:
        True when checked, False otherwise or on error.
    """
    return safe(lambda: bool(page.locator(selector).first.is_checked(timeout=timeout)), False)


def frame_texts(page: Any) -> Dict[str, str]:
    """Map every named frame in the page tree to its normalised body text.

    ``page.frames`` includes nested frames, which makes this the simplest way to
    assert on a frameset such as the-internet's ``/nested_frames``.

    Args:
        page: Playwright page.

    Returns:
        ``{frame.name: normalised body text}`` for all frames that have a name.
    """
    out: Dict[str, str] = {}

    def _collect() -> Dict[str, str]:
        # Per-frame try/except is essential: a <frameset> host document has no <body>,
        # and one unreadable frame must not empty the whole map (that bug made the
        # nested-frames verify return {} and cost 60s of timeouts before it was fixed).
        for frame in page.frames:
            if not frame.name:
                continue
            try:
                out[frame.name] = ntext(frame.locator("body").inner_text(timeout=4000))
            except Exception:  # noqa: BLE001 - an unreadable frame reads as ""
                out[frame.name] = ""
        return out

    return safe(_collect, {})


def frame_text_of(page: Any, frame_selector: str, selector: str, timeout: int = 4000) -> str:
    """Read text from inside an iframe without permanently switching context.

    Args:
        page: Playwright page.
        frame_selector: Selector of the iframe element on the host page.
        selector: Selector inside the iframe.
        timeout: Milliseconds to wait.

    Returns:
        Normalised inner text, or ``""`` when unavailable.
    """
    return safe(
        lambda: ntext(
            page.frame_locator(frame_selector).locator(selector).first.inner_text(timeout=timeout)
        ),
        "",
    )


def money(text: str) -> float:
    """Parse a price such as ``"$7.99"`` into ``7.99``.

    Args:
        text: Price label.

    Returns:
        The parsed amount, or ``float("nan")`` when unparseable.
    """
    cleaned = ntext(text).replace("$", "").replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return float("nan")
