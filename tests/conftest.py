import os
import tempfile

os.environ.setdefault("MEEMEE_DATA_DIR", tempfile.mkdtemp(prefix="meemee-tests-"))
os.environ.setdefault("MEEMEE_RATE_LIMIT_REQUESTS", "100000")

# Runtime intentionally fails closed without an encryption key. Tests use a
# deterministic non-production key supplied before application modules import.
os.environ.setdefault("MEEMEE_VAULT_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
os.environ.setdefault("MEEMEE_API_TOKEN", "test-bootstrap-token")
