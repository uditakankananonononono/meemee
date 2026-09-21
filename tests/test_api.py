from fastapi.testclient import TestClient

from meemee.api import app


def test_health():
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_run_validation():
    response = TestClient(app).post("/v1/runs", json={"goal":"x"})
    assert response.status_code == 422
