"""Backend registry.

Adding a backend is a two-line change: subclass :class:`AgentBackend`, then add it to
:data:`BACKEND_CLASSES` below. Nothing else in the harness needs to know.
"""

from __future__ import annotations

from typing import Dict, List, Tuple, Type

from aeval.backends.base import AgentBackend, BackendUnavailable
from aeval.backends.browser_use import BrowserUseBackend
from aeval.backends.naive import NaiveBackend
from aeval.backends.oracle import OracleBackend

__all__ = [
    "AgentBackend",
    "BackendUnavailable",
    "BACKEND_CLASSES",
    "backend_names",
    "create_backend",
    "describe_backends",
    "UnknownBackend",
]


class UnknownBackend(ValueError):
    """Raised when the CLI is asked for a backend name that is not registered."""


#: Canonical name -> class. Order here is the order shown by ``list-backends``.
BACKEND_CLASSES: Dict[str, Type[AgentBackend]] = {
    OracleBackend.name: OracleBackend,
    NaiveBackend.name: NaiveBackend,
    BrowserUseBackend.name: BrowserUseBackend,
}


def backend_names() -> List[str]:
    """Return the registered backend names.

    Returns:
        Names in registry order.
    """
    return list(BACKEND_CLASSES)


def create_backend(name: str, **kwargs: object) -> AgentBackend:
    """Instantiate a backend by name.

    Args:
        name: Registry key, e.g. ``"oracle"``.
        **kwargs: Forwarded to the backend constructor (``headless``, ``seed``, ...).

    Returns:
        The backend instance.

    Raises:
        UnknownBackend: If ``name`` is not registered.
    """
    cls = BACKEND_CLASSES.get(name)
    if cls is None:
        raise UnknownBackend(
            f"unknown backend {name!r}; available: {', '.join(backend_names())}"
        )
    return cls(**kwargs)  # type: ignore[arg-type]


def describe_backends() -> List[Tuple[str, str, bool]]:
    """Summarise every backend for ``run_eval.py list-backends``.

    Returns:
        ``(name, description, is_available)`` triples. Availability probes are
        wrapped so that one broken backend cannot break the listing.
    """
    rows: List[Tuple[str, str, bool]] = []
    for name, cls in BACKEND_CLASSES.items():
        try:
            available = cls().is_available()
        except Exception:  # noqa: BLE001 - listing must never fail
            available = False
        rows.append((name, cls.description, available))
    return rows
