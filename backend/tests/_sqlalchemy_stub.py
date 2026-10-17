"""Scoped `sqlalchemy` stub for unit tests that import a service without a database.

Some older unit tests stubbed `sqlalchemy` with `sys.modules.setdefault`, which leaked a MagicMock into every later test module in
the same pytest process. `sqlalchemy_stubbed()` installs the stub only while the service under test is imported, then restores
whatever was there before, so the real package stays available to every other test."""
from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import MagicMock

_NAMES = ("sqlalchemy", "sqlalchemy.ext.asyncio", "sqlalchemy.pool")


@contextmanager
def sqlalchemy_stubbed() -> Iterator[None]:
    saved = {name: sys.modules.pop(name) for name in _NAMES if name in sys.modules}
    stub = MagicMock()
    stub.text = lambda query: query            # text() only returns the string in these tests
    sys.modules["sqlalchemy"] = stub
    sys.modules["sqlalchemy.ext.asyncio"] = MagicMock()
    sys.modules["sqlalchemy.pool"] = MagicMock()
    try:
        yield
    finally:
        for name in _NAMES:
            sys.modules.pop(name, None)
        sys.modules.update(saved)
