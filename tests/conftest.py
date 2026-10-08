import os
import tempfile

os.environ.setdefault("MEEMEE_DATA_DIR", tempfile.mkdtemp(prefix="meemee-tests-"))
os.environ.setdefault("MEEMEE_RATE_LIMIT_REQUESTS", "100000")

# Runtime intentionally fails closed without an encryption key. Tests use a
# deterministic non-production key supplied before application modules import.
os.environ.setdefault("MEEMEE_VAULT_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
os.environ.setdefault("MEEMEE_API_TOKEN", "test-bootstrap-token")

# API resource callers must enter the supported startup/shutdown boundary.

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def started_api_for_legacy_callers(request):
    if request.module.__name__ in {'test_api_bootstrap_lifecycle', 'test_api_owned_http_cleanup', 'test_shutdown'}:
        yield
        return
    source = request.path.read_text()
    if 'meemee.api' not in source and 'from meemee import api' not in source:
        yield
        return
    from meemee import api
    with TestClient(api.app):
        yield
