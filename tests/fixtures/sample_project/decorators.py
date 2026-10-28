"""Sample decorator patterns."""

from __future__ import annotations

import functools

app = None  # stand-in so the module parses without a web framework


def login_required(func: object) -> object:
    """Guard decorator."""
    return func


@app.route("/")
def index() -> str:
    """Index page."""
    return "index"


@app.route("/about", methods=["GET"])
def about() -> str:
    """About page."""
    return "about"


@functools.cache
def cached() -> int:
    """Cached computation."""
    return 1


@login_required
@app.route("/me")
def me() -> str:
    """Profile page."""
    return "me"
