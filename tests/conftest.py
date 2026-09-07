"""Make the repository importable from ``tests/`` regardless of how pytest is invoked.

Two jobs:

1. Put the project root on ``sys.path`` so ``import aeval`` works even when pytest is
   invoked as ``pytest tests/`` (pytest would otherwise only add ``tests/``).
2. Import :mod:`aeval.tasks`, which is what *populates* the task registry. Without it
   the registry is empty and every starter-set test would see zero tasks — a bug that
   was caught here and is now guarded by these tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import aeval.tasks  # noqa: E402,F401  - importing registers the STARTER_SET
