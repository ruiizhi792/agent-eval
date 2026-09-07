"""Starter tasks on https://the-internet.herokuapp.com.

the-internet is a long-standing Selenium/Playwright teaching site. It is used here
because it exposes, in one place, the four failure modes that break real browser
agents and that saucedemo cannot provide: **tables**, **iframes**, **nested
frames**, **deferred content** and **native JS dialogs**.

Selectors were written from the site's public markup. As with saucedemo, treat them
as assertions about a third-party page: run ``--backend oracle`` before trusting any
number produced by these tasks.
"""

from __future__ import annotations

from typing import Any, List, Tuple

from aeval.schema import (
    Category,
    Difficulty,
    Step,
    TaskSpec,
    VerifyCheck,
    VerifyOutcome,
    normalize_url,
    register_task,
)
from aeval.tasks import helpers as h

__all__ = ["BASE_URL", "SITE", "TASKS"]

SITE = "the-internet.herokuapp.com"
BASE_URL = "https://the-internet.herokuapp.com"

LOGIN_URL = f"{BASE_URL}/login"
SECURE_URL = f"{BASE_URL}/secure"
TABLES_URL = f"{BASE_URL}/tables"
DROPDOWN_URL = f"{BASE_URL}/dropdown"
IFRAME_URL = f"{BASE_URL}/iframe"
NESTED_FRAMES_URL = f"{BASE_URL}/nested_frames"
DYNAMIC_1_URL = f"{BASE_URL}/dynamic_loading/1"
ALERTS_URL = f"{BASE_URL}/javascript_alerts"

#: Both sample tables on /tables have exactly four data rows.
N_TABLE_ROWS = 4

#: Row order of #table1 after sorting ascending by "Last Name".
#: NOTE: the source row is "Bach" (Frank Bach), NOT "Bachman" — this constant was wrong
#: on the first pass and the oracle calibration caught it. Always re-derive expected
#: values from the live page; never write them from memory of the page.
EXPECTED_LAST_NAME_ORDER: Tuple[str, ...] = ("Bach", "Conway", "Doe", "Smith")

#: Text the TinyMCE editor in /iframe must end up containing.
IFRAME_TARGET_TEXT = "AgentEval was here"


def _goto(path_or_url: str) -> Step:
    """Build a ``goto`` step for an absolute URL or a site-relative path.

    Args:
        path_or_url: ``"/login"`` or a full URL.

    Returns:
        The corresponding :class:`Step`.
    """
    target = path_or_url if path_or_url.startswith("http") else f"{BASE_URL}{path_or_url}"
    return Step("goto", target)


# --------------------------------------------------------------------------- #
# verify functions
# --------------------------------------------------------------------------- #
def _v_login_success(page: Any) -> VerifyOutcome:
    """Assert we reached the secure area with the success flash."""
    url = h.ntext(page.url)
    flash = h.text_of(page, "#flash")
    return VerifyOutcome(
        [
            VerifyCheck.of("url_is_secure", normalize_url(url) == SECURE_URL, f"url={url}"),
            VerifyCheck.of(
                "flash_confirms_login",
                "you logged into a secure area" in flash.lower(),
                f"flash={flash!r}",
            ),
            VerifyCheck.of("logout_button_visible", h.visible(page, "a.button"), "selector=a.button"),
        ]
    )


def _v_login_invalid(page: Any) -> VerifyOutcome:
    """Assert the login was rejected with the 'password is invalid' flash."""
    url = h.ntext(page.url)
    flash = h.text_of(page, "#flash")
    return VerifyOutcome(
        [
            VerifyCheck.of("still_on_login", "/login" in url, f"url={url}"),
            VerifyCheck.of(
                "flash_reports_invalid_password",
                "your password is invalid" in flash.lower(),
                f"flash={flash!r}",
            ),
        ]
    )


def _v_dropdown(page: Any) -> VerifyOutcome:
    """Assert Option 1 is the selected element of the dropdown."""
    value = h.value_of(page, "#dropdown")
    selected_text = h.text_of(page, "#dropdown option:checked", default="")
    return VerifyOutcome(
        [
            VerifyCheck.of("selected_value_is_1", value == "1", f"value={value!r}"),
            VerifyCheck.of(
                "selected_label_is_option_1",
                selected_text == "Option 1",
                f"selected_text={selected_text!r}",
            ),
        ]
    )


def _v_table_row_count(page: Any) -> VerifyOutcome:
    """Assert both sample tables have four data rows."""
    n1 = h.count_of(page, "#table1 tbody tr")
    n2 = h.count_of(page, "#table2 tbody tr")
    return VerifyOutcome(
        [
            VerifyCheck.of("table1_has_four_rows", n1 == N_TABLE_ROWS, f"count={n1}"),
            VerifyCheck.of("table2_has_four_rows", n2 == N_TABLE_ROWS, f"count={n2}"),
        ]
    )


def _row_cells(page: Any, table: str, row_index: int) -> List[str]:
    """Read one data row of a table as a list of normalised cell texts.

    Args:
        page: Playwright page.
        table: Table selector, e.g. ``"#table1"``.
        row_index: Zero-based row index within ``tbody``.

    Returns:
        The row's cell texts, or an empty list if the row is missing.
    """
    row = page.locator(f"{table} tbody tr").nth(row_index)
    return h.safe(
        lambda: [h.ntext(t) for t in row.locator("td").all_inner_texts()],
        [],
    )


def _v_table_read_cell(page: Any) -> VerifyOutcome:
    """Assert the row for last name 'Doe' carries the expected email."""
    n_rows = h.count_of(page, "#table1 tbody tr")
    email = ""
    first_name = ""
    for i in range(max(n_rows, 0)):
        cells = _row_cells(page, "#table1", i)
        if len(cells) > 2 and cells[0] == "Doe":
            first_name = cells[1]
            email = cells[2]
            break
    return VerifyOutcome(
        [
            VerifyCheck.of("table_has_rows", n_rows == N_TABLE_ROWS, f"count={n_rows}"),
            VerifyCheck.of(
                "doe_row_found",
                bool(email),
                f"found_first_name={first_name!r} found_email={email!r}",
            ),
            VerifyCheck.of(
                "doe_email_matches",
                email == "jdoe@hotmail.com",
                f"email={email!r} expected='jdoe@hotmail.com'",
            ),
        ]
    )


def _v_table_sorted(page: Any) -> VerifyOutcome:
    """Assert #table1 was re-sorted ascending by last name."""
    n_rows = h.count_of(page, "#table1 tbody tr")
    observed: List[str] = []
    for i in range(max(n_rows, 0)):
        cells = _row_cells(page, "#table1", i)
        if cells:
            observed.append(cells[0])
    return VerifyOutcome(
        [
            VerifyCheck.of("table_has_rows", n_rows == N_TABLE_ROWS, f"count={n_rows}"),
            VerifyCheck.of(
                "last_names_ascending",
                tuple(observed) == EXPECTED_LAST_NAME_ORDER,
                f"observed={observed} expected={list(EXPECTED_LAST_NAME_ORDER)}",
            ),
        ]
    )


def _v_iframe_text(page: Any) -> VerifyOutcome:
    """Assert the TinyMCE editor inside the iframe holds the typed text."""
    body = h.frame_text_of(page, "#mce_0_ifr", "#tinymce")
    return VerifyOutcome(
        [
            VerifyCheck.of(
                "iframe_text_matches",
                body == IFRAME_TARGET_TEXT,
                f"iframe_text={body!r} expected={IFRAME_TARGET_TEXT!r}",
            ),
            VerifyCheck.of("iframe_text_non_empty", bool(body), f"iframe_text={body!r}"),
        ]
    )


def _v_nested_frames(page: Any) -> VerifyOutcome:
    """Assert all four nested frames could be entered and read."""
    texts = h.frame_texts(page)
    expected = {
        "frame-left": "LEFT",
        "frame-middle": "MIDDLE",
        "frame-right": "RIGHT",
        "frame-bottom": "BOTTOM",
    }
    checks = [
        VerifyCheck.of(
            f"frame_{name}_content",
            texts.get(name, "") == want,
            f"observed={texts.get(name, '')!r} expected={want!r}",
        )
        for name, want in expected.items()
    ]
    return VerifyOutcome(checks)


def _v_dynamic_loading(page: Any) -> VerifyOutcome:
    """Assert the deferred element finally rendered its message.

    Note: /dynamic_loading/1 waits ~5 s before revealing ``#finish``. The grader
    gives it a bounded window, so a backend that never waits can still pass when the
    loader happens to finish inside that window. That race is *intentional* — it is
    one of the real sources of flakiness this harness is built to surface — but it
    also means this task's pass rate is partly a property of the grader's timeout.
    """
    visible = h.visible(page, "#finish", timeout=4000)
    text = h.text_of(page, "#finish", timeout=4000, default="")
    return VerifyOutcome(
        [
            VerifyCheck.of("finish_element_visible", visible, "selector=#finish"),
            VerifyCheck.of(
                "finish_text_is_hello_world",
                text == "Hello World!",
                f"text={text!r} expected='Hello World!'",
            ),
        ]
    )


def _v_js_confirm(page: Any) -> VerifyOutcome:
    """Assert the JS confirm dialog was accepted, not dismissed."""
    result = h.text_of(page, "#result")
    return VerifyOutcome(
        [
            VerifyCheck.of(
                "result_is_ok",
                result == "You clicked: Ok",
                f"result={result!r} expected='You clicked: Ok'",
            ),
            VerifyCheck.of(
                "result_is_not_cancel",
                "cancel" not in result.lower(),
                f"result={result!r}",
            ),
        ]
    )


# --------------------------------------------------------------------------- #
# task registration
# --------------------------------------------------------------------------- #
TASKS: List[TaskSpec] = [
    register_task(
        TaskSpec(
            task_id="inet_login_success",
            instruction=(
                "Open https://the-internet.herokuapp.com/login, log in with the username "
                '"tomsmith" and the password "SuperSecretPassword!", and stay on the secure page.'
            ),
            category=Category.AUTH,
            difficulty=Difficulty.EASY,
            start_url=LOGIN_URL,
            site=SITE,
            plan=(
                _goto("/login"),
                Step("fill", "#username", "tomsmith"),
                Step("fill", "#password", "SuperSecretPassword!"),
                Step("click", 'button[type="submit"]'),
                Step("wait_for", "#flash"),
            ),
            verify=_v_login_success,
            notes="Server-rendered form POST; the flash message is the success signal.",
        )
    ),
    register_task(
        TaskSpec(
            task_id="inet_login_invalid_password",
            instruction=(
                "Open https://the-internet.herokuapp.com/login, try to log in as "
                '"tomsmith" with the wrong password "hunter2", and leave the error message '
                "on screen."
            ),
            category=Category.AUTH,
            difficulty=Difficulty.EASY,
            start_url=LOGIN_URL,
            site=SITE,
            plan=(
                _goto("/login"),
                Step("fill", "#username", "tomsmith"),
                Step("fill", "#password", "hunter2"),
                Step("click", 'button[type="submit"]'),
            ),
            verify=_v_login_invalid,
            notes="Negative case; a backend that ignores the failed login scores 0.",
        )
    ),
    register_task(
        TaskSpec(
            task_id="inet_dropdown_select",
            instruction=(
                "Open https://the-internet.herokuapp.com/dropdown and select the option "
                'labelled "Option 1" from the dropdown.'
            ),
            category=Category.FORM,
            difficulty=Difficulty.EASY,
            start_url=DROPDOWN_URL,
            site=SITE,
            plan=(_goto("/dropdown"), Step("select", "#dropdown", "1")),
            verify=_v_dropdown,
            notes="Simplest possible <select> interaction.",
        )
    ),
    register_task(
        TaskSpec(
            task_id="inet_table_row_count",
            instruction=(
                "Open https://the-internet.herokuapp.com/tables and leave the two example "
                "tables rendered. Both tables must show all four data rows."
            ),
            category=Category.TABLE,
            difficulty=Difficulty.EASY,
            start_url=TABLES_URL,
            site=SITE,
            plan=(_goto("/tables"),),
            verify=_v_table_row_count,
            notes="Read-only table task: establishes that /tables renders at all.",
        )
    ),
    register_task(
        TaskSpec(
            task_id="inet_table_read_cell",
            instruction=(
                "Open https://the-internet.herokuapp.com/tables and find the e-mail address "
                'of the person whose last name is "Doe" in the first table.'
            ),
            category=Category.TABLE,
            difficulty=Difficulty.MEDIUM,
            start_url=TABLES_URL,
            site=SITE,
            plan=(_goto("/tables"), Step("wait_for", "#table1 tbody tr")),
            verify=_v_table_read_cell,
            notes="Requires locating a row by one column and reporting another.",
        )
    ),
    register_task(
        TaskSpec(
            task_id="inet_table_sort_last_name",
            instruction=(
                "Open https://the-internet.herokuapp.com/tables and sort the first table by "
                '"Last Name" so the names run in ascending alphabetical order.'
            ),
            category=Category.TABLE,
            difficulty=Difficulty.HARD,
            start_url=TABLES_URL,
            site=SITE,
            plan=(
                _goto("/tables"),
                Step("wait_for", "#table1 tbody tr"),
                Step("click", '#table1 thead th:has-text("Last Name")'),
            ),
            verify=_v_table_sorted,
            notes="Header click triggers a client-side sort; the grader checks full row order.",
        )
    ),
    register_task(
        TaskSpec(
            task_id="inet_iframe_type_text",
            instruction=(
                "Open https://the-internet.herokuapp.com/iframe and type the exact text "
                f'"{IFRAME_TARGET_TEXT}" into the rich-text editor inside the frame.'
            ),
            category=Category.FRAME,
            difficulty=Difficulty.MEDIUM,
            start_url=IFRAME_URL,
            site=SITE,
            plan=(
                _goto("/iframe"),
                Step("frame_fill", "#tinymce", IFRAME_TARGET_TEXT, frame="#mce_0_ifr"),
            ),
            verify=_v_iframe_text,
            notes=(
                "Requires entering an iframe. Backends that never switch frames score 0. "
                "SITE OUTAGE (2026-08): the-internet's TinyMCE editor is served with "
                "contenteditable=false ('no more editor loads available this month'), so "
                "NO agent can type into it right now and the oracle legitimately fails this "
                "task. Re-run the oracle next month before trusting it."
            ),
        )
    ),
    register_task(
        TaskSpec(
            task_id="inet_nested_frames_read",
            instruction=(
                "Open https://the-internet.herokuapp.com/nested_frames and visit each of the "
                "frames so that the text of the left, middle, right and bottom frames is loaded."
            ),
            category=Category.FRAME,
            difficulty=Difficulty.HARD,
            start_url=NESTED_FRAMES_URL,
            site=SITE,
            plan=(
                _goto("/nested_frames"),
                Step("frame_read", "body", frame='frame[name="frame-left"]'),
                Step("frame_read", "body", frame='frame[name="frame-middle"]'),
                Step("frame_read", "body", frame='frame[name="frame-right"]'),
                Step("frame_read", "body", frame='frame[name="frame-bottom"]'),
            ),
            verify=_v_nested_frames,
            notes=(
                "Nested frameset (top contains left/middle/right). WEAK TASK: the browser "
                "attaches these frames automatically, so a do-nothing agent also passes. "
                "Kept for category coverage; replace with an *interactive* frame task as "
                "soon as the-internet's TinyMCE editor is writable again."
            ),
        )
    ),
    register_task(
        TaskSpec(
            task_id="inet_dynamic_loading_1",
            instruction=(
                "Open https://the-internet.herokuapp.com/dynamic_loading/1, click the start "
                'button, and wait until the "Hello World!" message is visible.'
            ),
            category=Category.DYNAMIC,
            difficulty=Difficulty.EASY,
            start_url=DYNAMIC_1_URL,
            site=SITE,
            plan=(
                _goto("/dynamic_loading/1"),
                Step("click", "#start button"),
                Step("wait_for", "#finish"),
            ),
            verify=_v_dynamic_loading,
            notes="Content appears after a multi-second delay; not waiting is the failure mode.",
        )
    ),
    register_task(
        TaskSpec(
            task_id="inet_js_confirm_accept",
            instruction=(
                "Open https://the-internet.herokuapp.com/javascript_alerts, click the button "
                "that opens a JavaScript confirm dialog, and ACCEPT the dialog so the page "
                'reports "You clicked: Ok".'
            ),
            category=Category.DYNAMIC,
            difficulty=Difficulty.MEDIUM,
            start_url=ALERTS_URL,
            site=SITE,
            plan=(
                _goto("/javascript_alerts"),
                Step("dialog_accept"),
                Step("click", 'button[onclick="jsConfirm()"]'),
                Step("wait_for", "#result"),
            ),
            verify=_v_js_confirm,
            notes="Native dialog handling. Playwright auto-dismisses, so doing nothing scores 0.",
        )
    ),
]
