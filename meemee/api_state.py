"""Stable references resolved only by awaited API startup."""
from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.security import HTTPAuthorizationCredentials

from .auth import bearer_dependency

_UNSET = object()


class APINotStartedError(RuntimeError):
    """An API resource was accessed outside a successfully started lifespan."""


class APIResource:
    def __init__(self, resolve: Callable[[], Any], name: str):
        object.__setattr__(self, '_resolve', resolve)
        object.__setattr__(self, '_name', name)

    def value(self):
        result = self._resolve()
        if result is _UNSET:
            raise APINotStartedError(f'API resource {self._name} requires a started lifespan')
        return result

    def child(self, name):
        return APIResource(lambda: getattr(self.value(), name), f'{self._name}.{name}')

    def __getattr__(self, name):
        return getattr(self.value(), name)

    def __setattr__(self, name, value):
        setattr(self.value(), name, value)

    def __getitem__(self, key):
        return self.value()[key]

    def __bool__(self):
        return bool(self.value())

    def __iter__(self):
        return iter(self.value())


class APIAuth(APIResource):
    def dependency(self, required):
        def check(request: Request, credentials: HTTPAuthorizationCredentials | None = bearer_dependency):
            return self.value().dependency(required)(request, credentials)
        return check
