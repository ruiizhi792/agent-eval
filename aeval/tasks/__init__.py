"""STARTER_SET — 这是起步集，用户应当审阅并替换/扩充。任务集与 verify 函数是这个项目的知识产权核心。

Starter task set for AgentEval.

**This is a STARTER_SET.** It was written to exercise the harness end-to-end, not to
be the definitive benchmark. Before you quote any number produced with it, read every
task, replace the ones that do not match the behaviour you care about, and add tasks
until the set covers your own failure taxonomy. The *task list plus its ``verify``
functions* is the intellectual property of this project — the harness around it is
commodity plumbing.

Coverage of the starter set (20 tasks):

==========  =========  =========================================================
category    n tasks    what it probes
==========  =========  =========================================================
``auth``    5          login success, wrong password, locked account (x2 sites)
``nav``     4          add to cart, multi-item cart, remove item, logout
``form``    3          full checkout, validation error, <select>
``table``   3          row count, cell lookup, client-side sort
``dynamic`` 3          client-side re-sort, deferred content, native JS dialog
``frame``   2          typing inside an iframe, reading a nested frameset
==========  =========  =========================================================

Sites: 10 tasks on ``saucedemo.com``, 10 on ``the-internet.herokuapp.com`` — both are
publicly documented automation sandboxes. No task targets a site whose terms of
service forbid automation.
"""

from __future__ import annotations

from typing import List

from aeval.schema import Category, TaskSpec, all_tasks, register_task, tasks_by_category

# Importing the two modules is what populates the registry (they call register_task).
from aeval.tasks import saucedemo, theinternet  # noqa: F401

__all__ = [
    "STARTER_SET",
    "STARTER_SET_SIZE",
    "all_tasks",
    "tasks_by_category",
    "register_task",
    "count_by_category",
    "Category",
    "TaskSpec",
]


def _build_starter_set() -> List[TaskSpec]:
    """Collect every registered task into a stable, documented list.

    Returns:
        All tasks, ordered by category then id.
    """
    return all_tasks()


#: The tasks shipped with this repository. See the module docstring — review it.
STARTER_SET: List[TaskSpec] = _build_starter_set()
STARTER_SET_SIZE = len(STARTER_SET)


def count_by_category() -> dict:
    """Count starter tasks per category.

    Returns:
        ``{category_value: n_tasks}`` in canonical category order.
    """
    return {str(cat): len(tasks_by_category(cat)) for cat in Category}
