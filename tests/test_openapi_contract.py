from meemee.api import app


def test_openapi_has_current_version_and_bearer_security():
    schema = app.openapi()
    assert schema["info"]["version"] == "0.43.0"
    schemes = schema["components"]["securitySchemes"]
    assert schemes["HTTPBearer"] == {"type":"http","scheme":"bearer"}
    for path, method in (("/v1/jobs","post"),("/v1/tokens","post"),("/v1/webhooks","post")):
        assert {"HTTPBearer": []} in schema["paths"][path][method]["security"]


def test_openapi_exposes_commercial_endpoints():
    paths = app.openapi()["paths"]
    expected = {"/v1/quota", "/v1/approvals/{principal_id}", "/v1/webhooks", "/v1/webhook-deliveries"}
    assert expected <= paths.keys()
