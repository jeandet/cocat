"""Read the tokens that travel in URL fragments.

A token in a path or a query ends up in access logs and browser history, so the
links we mail put it after `#`, which never leaves the browser; the page asks
for it over the socket and keeps it in memory only.
"""

from nicegui import ui

FRAGMENT_SCRIPT = "window.location.hash.replace(/^#/, '')"

CLEAR_FRAGMENT_SCRIPT = (
    "history.replaceState(null, '', window.location.pathname + window.location.search)"
)


async def read_fragment_token() -> str:
    """Ask the browser for its fragment, or return an empty string if it cannot answer.

    `ui.run_javascript` raises `TimeoutError` when the browser does not reply
    (a blocked socket, disabled JavaScript): the page must still render and
    offer the paste box rather than stay blank.
    """
    try:
        value = await ui.run_javascript(FRAGMENT_SCRIPT)
    except TimeoutError:
        return ""
    return "" if value is None else str(value).strip()


def token_from_text(text: str) -> str:
    """Pull the token out of a pasted link, or take the paste as the token itself.

    Signing in leaves the page behind and a fragment does not survive that, so
    the user gets a box to paste the whole link into.
    """
    return text.partition("#")[2].strip() or text.strip()
