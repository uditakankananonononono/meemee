import os

# Runtime intentionally fails closed without an encryption key. Tests use a
# deterministic non-production key supplied before application modules import.
os.environ.setdefault("MEEMEE_VAULT_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
os.environ.setdefault("MEEMEE_API_TOKEN", "test-bootstrap-token")
