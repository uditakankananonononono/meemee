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
    A close error never replaces the constructor's original exception.
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
                    except Exception:  # noqa: BLE001,S110 - keep the original failure
                        pass
            raise

    return wrapper  # type: ignore[return-value]
