"""Close a constructor-opened SQLite handle when the constructor fails."""
from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., None])
_HANDLE_NAMES = ("db", "connection")


def close_db_on_init_failure(init: F) -> F:
    """Wrap ``__init__`` so a failure after ``sqlite3.connect`` closes the handle.

    Only for classes whose ``__init__`` itself opens ``self.db``/``self.connection``.
    Any close error, including cancellation or interrupts raised by close, is
    contained and never replaces the constructor's original exception.
    """

    @functools.wraps(init)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> None:
        try:
            init(self, *args, **kwargs)
        except BaseException:
            for name in _HANDLE_NAMES:
                handle = self.__dict__.get(name)
                if handle is not None and hasattr(handle, "close"):
                    try:
                        handle.close()
                    except BaseException:  # noqa: BLE001,S110 - cleanup must not replace the original
                        pass
            raise

    return wrapper  # type: ignore[return-value]


def rollback_quietly(connection: Any) -> None:
    """Roll back, containing any error so it cannot replace the failure being handled."""
    try:
        connection.rollback()
    except BaseException:  # noqa: BLE001,S110 - cleanup must not replace the original
        pass


def close_quietly(connection: Any) -> None:
    """Close, containing any error so it cannot replace the failure being handled."""
    try:
        connection.close()
    except BaseException:  # noqa: BLE001,S110 - cleanup must not replace the original
        pass
