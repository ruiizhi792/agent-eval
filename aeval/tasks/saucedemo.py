"""Starter tasks on https://www.saucedemo.com.

saucedemo is a purpose-built automation sandbox: a React storefront with a login
gate, an inventory grid, a cart and a two-step checkout. Every selector below was
written against its public markup (``data-test`` attributes for cart controls, plain
ids for the checkout form).

**These selectors are the single most fragile thing in the repository.** They are
assertions about a third-party website that can be redesigned without notice. If a
calibration run (``--backend oracle``) suddenly shows failures on saucedemo tasks
that used to pass, suspect the site before you suspect the harness.
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

SITE = "saucedemo.com"
BASE_URL = "https://www.saucedemo.com"

INVENTORY_URL = f"{BASE_URL}/inventory.html"
CART_URL = f"{BASE_URL}/cart.html"
CHECKOUT_ONE_URL = f"{BASE_URL}/checkout-step-one.html"
CHECKOUT_TWO_URL = f"{BASE_URL}/checkout-step-two.html"
CHECKOUT_DONE_URL = f"{BASE_URL}/checkout-complete.html"

#: The six products on the default inventory page (used to assert item counts).
N_PRODUCTS = 6

#: Cheapest item in the default catalogue, used by the sort task.
CHEAPEST_PRICE = 7.99


def _login(username: str = "standard_user", password: str = "secret_sauce") -> Tuple[Step, ...]:
    """Build the shared "log in" prefix of most saucedemo plans.

    Args:
        username: Account to use.
        password: Password to use.

    Returns:
        The plan steps that log in.
    """
    return (
        Step("goto", BASE_URL),
        Step("fill", "#user-name", username),
        Step("fill", "#password", password),
        Step("click", "#login-button"),
    )


def _add_to_cart(data_test_ids: Tuple[str, ...]) -> Tuple[Step, ...]:
    """Build "add these items to the cart" steps.

    Args:
        data_test_ids: ``data-test`` attribute suffixes, e.g. ``add-to-cart-...``.

    Returns:
        One click step per item.
    """
    return tuple(Step("click", f'[data-test="{i}"]') for i in data_test_ids)


BACKPACK = "add-to-cart-sauce-labs-backpack"
BIKE_LIGHT = "add-to-cart-sauce-labs-bike-light"
BOLT_TSHIRT = "add-to-cart-sauce-labs-bolt-t-shirt"
REMOVE_BACKPACK = "remove-sauce-labs-backpack"


# --------------------------------------------------------------------------- #
# verify functions
# --------------------------------------------------------------------------- #
def _v_login_success(page: Any) -> VerifyOutcome:
    """Assert we are on a fully rendered inventory page."""
    url = h.ntext(page.url)
    n_items = h.count_of(page, ".inventory_item")
    return VerifyOutcome(
        [
            VerifyCheck.of("url_is_inventory", normalize_url(url) == INVENTORY_URL, f"url={url}"),
            VerifyCheck.of("inventory_list_visible", h.visible(page, ".inventory_list"), "selector=.inventory_list"),
            VerifyCheck.of(
                "six_products_listed",
                n_items == N_PRODUCTS,
                f"count={n_items} expected={N_PRODUCTS}",
            ),
        ]
    )


def _v_still_on_login_with_error(page: Any, expected_fragment: str) -> VerifyOutcome:
    """Assert the login was rejected and the reason is on screen."""
    url = h.ntext(page.url)
    error = h.text_of(page, '[data-test="error"]')
    return VerifyOutcome(
        [
            VerifyCheck.of(
                "did_not_reach_inventory",
                "/inventory" not in url,
                f"url={url}",
            ),
            VerifyCheck.of("login_button_still_visible", h.visible(page, "#login-button"), "selector=#login-button"),
            VerifyCheck.of(
                "error_message_shown",
                expected_fragment.lower() in error.lower(),
                f"error={error!r} expected_fragment={expected_fragment!r}",
            ),
        ]
    )


def _v_wrong_password(page: Any) -> VerifyOutcome:
    """Assert the classic 'do not match' rejection."""
    return _v_still_on_login_with_error(page, "do not match")


def _v_locked_out(page: Any) -> VerifyOutcome:
    """Assert the 'locked out' rejection."""
    return _v_still_on_login_with_error(page, "locked out")


def _v_cart_badge(page: Any, expected: int) -> VerifyOutcome:
    """Assert the cart badge shows exactly ``expected`` items."""
    badge = h.text_of(page, ".shopping_cart_badge", default="")
    url = h.ntext(page.url)
    try:
        actual = int(badge)
    except ValueError:
        actual = -1
    return VerifyOutcome(
        [
            VerifyCheck.of("still_on_inventory", "/inventory" in url, f"url={url}"),
            VerifyCheck.of("badge_count", actual == expected, f"badge={badge!r} expected={expected}"),
        ]
    )


def _v_cart_badge_1(page: Any) -> VerifyOutcome:
    """Assert badge == 1."""
    return _v_cart_badge(page, 1)


def _v_cart_badge_3(page: Any) -> VerifyOutcome:
    """Assert badge == 3."""
    return _v_cart_badge(page, 3)


def _v_cart_after_remove(page: Any) -> VerifyOutcome:
    """Assert exactly one item (Bike Light) remains on the cart page."""
    url = h.ntext(page.url)
    n_items = h.count_of(page, ".cart_item")
    names = h.texts_of(page, ".cart_item .inventory_item_name")
    return VerifyOutcome(
        [
            VerifyCheck.of("on_cart_page", normalize_url(url) == CART_URL, f"url={url}"),
            VerifyCheck.of("one_cart_item", n_items == 1, f"count={n_items} expected=1"),
            VerifyCheck.of(
                "remaining_item_is_bike_light",
                bool(names) and names[0] == "Sauce Labs Bike Light",
                f"names={names}",
            ),
        ]
    )


def _v_checkout_complete(page: Any) -> VerifyOutcome:
    """Assert we reached the order-confirmation screen."""
    url = h.ntext(page.url)
    header = h.text_of(page, ".complete-header")
    body = h.text_of(page, ".complete-text")
    return VerifyOutcome(
        [
            VerifyCheck.of("on_complete_page", normalize_url(url) == CHECKOUT_DONE_URL, f"url={url}"),
            VerifyCheck.of(
                "thank_you_header",
                header == "Thank you for your order!",
                f"header={header!r}",
            ),
            VerifyCheck.of("confirmation_body_present", "dispatched" in body.lower(), f"body={body!r}"),
        ]
    )


def _v_checkout_postal_error(page: Any) -> VerifyOutcome:
    """Assert checkout step one refused to advance without a postal code."""
    url = h.ntext(page.url)
    error = h.text_of(page, '[data-test="error"]')
    first_name = h.value_of(page, "#first-name")
    return VerifyOutcome(
        [
            VerifyCheck.of("still_on_step_one", normalize_url(url) == CHECKOUT_ONE_URL, f"url={url}"),
            VerifyCheck.of(
                "postal_code_error",
                "postal code is required" in error.lower(),
                f"error={error!r}",
            ),
            VerifyCheck.of("first_name_retained", first_name == "Ada", f"first_name={first_name!r}"),
        ]
    )


def _v_sorted_low_to_high(page: Any) -> VerifyOutcome:
    """Assert the grid is re-rendered in ascending price order."""
    selected = h.value_of(page, '[data-test="product-sort-container"]')
    labels = h.texts_of(page, ".inventory_item_price")
    prices = [h.money(label) for label in labels]
    ascending = len(prices) > 1 and all(a <= b for a, b in zip(prices, prices[1:]))
    first = prices[0] if prices else float("nan")
    return VerifyOutcome(
        [
            VerifyCheck.of("sort_control_set_to_lohi", selected == "lohi", f"value={selected!r}"),
            VerifyCheck.of(
                "prices_ascending",
                ascending,
                f"prices={prices}",
            ),
            VerifyCheck.of(
                "cheapest_first",
                first == CHEAPEST_PRICE,
                f"first_price={first} expected={CHEAPEST_PRICE}",
            ),
        ]
    )


def _v_logged_out(page: Any) -> VerifyOutcome:
    """Assert the session was destroyed and we are back at the gate."""
    url = h.ntext(page.url)
    return VerifyOutcome(
        [
            VerifyCheck.of("login_button_visible", h.visible(page, "#login-button"), "selector=#login-button"),
            VerifyCheck.of("left_inventory", "/inventory" not in url, f"url={url}"),
            VerifyCheck.of("no_products_rendered", h.count_of(page, ".inventory_item") == 0, f"count={h.count_of(page, '.inventory_item')}"),
        ]
    )


# --------------------------------------------------------------------------- #
# task registration
# --------------------------------------------------------------------------- #
TASKS: List[TaskSpec] = [
    register_task(
        TaskSpec(
            task_id="sauce_login_success",
            instruction=(
                'Go to https://www.saucedemo.com, log in with the username "standard_user" '
                'and the password "secret_sauce", and wait until the product inventory is shown.'
            ),
            category=Category.AUTH,
            difficulty=Difficulty.EASY,
            start_url=BASE_URL,
            site=SITE,
            plan=_login() + (Step("wait_for", ".inventory_list"),),
            verify=_v_login_success,
            notes="Happy-path auth. Oracle must be ~100% here or the harness is broken.",
        )
    ),
    register_task(
        TaskSpec(
            task_id="sauce_login_wrong_password",
            instruction=(
                'Go to https://www.saucedemo.com, try to log in with the username "standard_user" '
                'and the password "not_the_password", and leave the resulting error message on screen.'
            ),
            category=Category.AUTH,
            difficulty=Difficulty.EASY,
            start_url=BASE_URL,
            site=SITE,
            plan=(
                Step("goto", BASE_URL),
                Step("fill", "#user-name", "standard_user"),
                Step("fill", "#password", "not_the_password"),
                Step("click", "#login-button"),
            ),
            verify=_v_wrong_password,
            notes="Negative case: the task passes only if the agent *does not* proceed.",
        )
    ),
    register_task(
        TaskSpec(
            task_id="sauce_login_locked_user",
            instruction=(
                'Go to https://www.saucedemo.com, try to log in as "locked_out_user" with the '
                'password "secret_sauce", and leave the resulting error message on screen.'
            ),
            category=Category.AUTH,
            difficulty=Difficulty.EASY,
            start_url=BASE_URL,
            site=SITE,
            plan=(
                Step("goto", BASE_URL),
                Step("fill", "#user-name", "locked_out_user"),
                Step("fill", "#password", "secret_sauce"),
                Step("click", "#login-button"),
            ),
            verify=_v_locked_out,
            notes="Distinct error surface from the wrong-password case.",
        )
    ),
    register_task(
        TaskSpec(
            task_id="sauce_add_to_cart_single",
            instruction=(
                "Log in to https://www.saucedemo.com as standard_user / secret_sauce, then add "
                '"Sauce Labs Backpack" to the cart and stop. The cart badge must read 1.'
            ),
            category=Category.NAV,
            difficulty=Difficulty.EASY,
            start_url=BASE_URL,
            site=SITE,
            plan=_login() + _add_to_cart((BACKPACK,)),
            verify=_v_cart_badge_1,
            notes="First task that requires a post-navigation interaction — where fragile"
            " executors typically start dropping steps.",
        )
    ),
    register_task(
        TaskSpec(
            task_id="sauce_add_to_cart_three",
            instruction=(
                "Log in to https://www.saucedemo.com as standard_user / secret_sauce, then add "
                '"Sauce Labs Backpack", "Sauce Labs Bike Light" and "Sauce Labs Bolt T-Shirt" '
                "to the cart. The cart badge must read 3."
            ),
            category=Category.NAV,
            difficulty=Difficulty.MEDIUM,
            start_url=BASE_URL,
            site=SITE,
            plan=_login() + _add_to_cart((BACKPACK, BIKE_LIGHT, BOLT_TSHIRT)),
            verify=_v_cart_badge_3,
            notes="Longer action chain: each extra click is another chance to desync from the DOM.",
        )
    ),
    register_task(
        TaskSpec(
            task_id="sauce_cart_remove_item",
            instruction=(
                "Log in to https://www.saucedemo.com as standard_user / secret_sauce, add "
                '"Sauce Labs Backpack" and "Sauce Labs Bike Light" to the cart, open the cart, '
                'and remove "Sauce Labs Backpack". Exactly one item must remain.'
            ),
            category=Category.NAV,
            difficulty=Difficulty.MEDIUM,
            start_url=BASE_URL,
            site=SITE,
            plan=_login()
            + _add_to_cart((BACKPACK, BIKE_LIGHT))
            + (Step("click", ".shopping_cart_link"), Step("click", f'[data-test="{REMOVE_BACKPACK}"]')),
            verify=_v_cart_after_remove,
            notes="Requires a page transition plus a removal that re-renders the list.",
        )
    ),
    register_task(
        TaskSpec(
            task_id="sauce_checkout_happy_path",
            instruction=(
                "Log in to https://www.saucedemo.com as standard_user / secret_sauce, add "
                '"Sauce Labs Backpack" to the cart, and complete the checkout filling the form '
                'with first name "Ada", last name "Lovelace", postal code "N2L 3G1". '
                "Finish the order."
            ),
            category=Category.FORM,
            difficulty=Difficulty.HARD,
            start_url=BASE_URL,
            site=SITE,
            plan=_login()
            + _add_to_cart((BACKPACK,))
            + (
                Step("click", ".shopping_cart_link"),
                Step("click", '[data-test="checkout"]'),
                Step("fill", "#first-name", "Ada"),
                Step("fill", "#last-name", "Lovelace"),
                Step("fill", "#postal-code", "N2L 3G1"),
                Step("click", "#continue"),
                Step("wait_for", ".summary_info"),
                Step("click", "#finish"),
            ),
            verify=_v_checkout_complete,
            notes="Longest chain in the starter set (10 steps). Any single desync kills it.",
        )
    ),
    register_task(
        TaskSpec(
            task_id="sauce_checkout_missing_postal",
            instruction=(
                "Log in to https://www.saucedemo.com as standard_user / secret_sauce, add "
                '"Sauce Labs Backpack" to the cart, start checkout, fill first name "Ada" and '
                'last name "Lovelace" but leave the postal code empty, then submit. '
                "Leave the validation error on screen."
            ),
            category=Category.FORM,
            difficulty=Difficulty.MEDIUM,
            start_url=BASE_URL,
            site=SITE,
            plan=_login()
            + _add_to_cart((BACKPACK,))
            + (
                Step("click", ".shopping_cart_link"),
                Step("click", '[data-test="checkout"]'),
                Step("fill", "#first-name", "Ada"),
                Step("fill", "#last-name", "Lovelace"),
                Step("click", "#continue"),
            ),
            verify=_v_checkout_postal_error,
            notes="Negative form case — passing requires *stopping* at the validation error.",
        )
    ),
    register_task(
        TaskSpec(
            task_id="sauce_sort_price_low_high",
            instruction=(
                "Log in to https://www.saucedemo.com as standard_user / secret_sauce and sort "
                "the product list by price from low to high."
            ),
            category=Category.DYNAMIC,
            difficulty=Difficulty.MEDIUM,
            start_url=BASE_URL,
            site=SITE,
            plan=_login() + (Step("select", '[data-test="product-sort-container"]', "lohi"),),
            verify=_v_sorted_low_to_high,
            notes="Client-side re-render: a non-waiting executor can read the pre-sort DOM.",
        )
    ),
    register_task(
        TaskSpec(
            task_id="sauce_logout",
            instruction=(
                "Log in to https://www.saucedemo.com as standard_user / secret_sauce, open the "
                "side menu and log out."
            ),
            category=Category.NAV,
            difficulty=Difficulty.EASY,
            start_url=BASE_URL,
            site=SITE,
            plan=_login()
            + (
                Step("click", "#react-burger-menu-btn"),
                Step("wait_for", "#logout_sidebar_link"),
                Step("click", "#logout_sidebar_link"),
            ),
            verify=_v_logged_out,
            notes="Menu is animated: clicking through the animation is a classic race.",
        )
    ),
]
