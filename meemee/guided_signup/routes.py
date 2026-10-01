"""Explicit host opt-in router. All browser operations run on one dedicated actor.

No default engine or site registration: host must supply reviewed local profiles,
credential vault, scoped inbox and authenticated owner dependency.
"""
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field


class Start(BaseModel):
    model_config = ConfigDict(extra='forbid')
    site: str
    email: str
    name: str
    credential_ref: str


class Approval(BaseModel):
    model_config = ConfigDict(extra='forbid')
    inspection_digest: str = Field(min_length=64, max_length=64)


def build_router(engine_factory, owner_dependency):
    actor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='guided-signup')
    engine = actor.submit(engine_factory).result()
    router = APIRouter(prefix='/guided-signup', tags=['guided-signup-local-only'])

    def call(method, *args):
        try:
            return actor.submit(getattr(engine, method), *args).result()
        except KeyError:
            raise HTTPException(404, 'signup request or site unavailable') from None
        except ValueError:
            raise HTTPException(409, 'signup request requires current review') from None
        except Exception:
            raise HTTPException(503, 'signup actor unavailable') from None

    @router.post('/requests')
    def start(body: Start, owner=Depends(owner_dependency)):
        return call('start', str(owner), body.site, body.email, body.name, body.credential_ref)

    @router.get('/requests/{rid}')
    def get(rid: str, owner=Depends(owner_dependency)):
        return call('get', str(owner), rid)

    @router.post('/requests/{rid}/approve')
    def approve(rid: str, body: Approval, owner=Depends(owner_dependency)):
        return call('approve', str(owner), rid, body.inspection_digest)

    def add_action(action):
        def perform(rid: str, owner=Depends(owner_dependency)):
            return call(action, str(owner), rid)
        router.add_api_route('/requests/{rid}/'+action, perform, methods=['POST'], name='signup_'+action)

    for action in ('submit', 'verify', 'resend', 'cancel'):
        add_action(action)

    def close():
        actor.submit(engine.close).result()
        actor.shutdown(wait=True)
    # Host owns shutdown as well as authentication and rate limiting.
    return router, close
