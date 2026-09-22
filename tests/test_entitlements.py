from pathlib import Path

import pytest

from meemee.entitlements import EntitlementStore, public_catalog


def test_assignment_and_limits_are_persistent(tmp_path: Path):
    path=tmp_path/"e.db"; store=EntitlementStore(path)
    assert store.get("u")["plan"]=="starter"
    assert store.allows("u","webhooks",2) and not store.allows("u","webhooks",3)
    store.assign("u","team","2026-09-22T00:00:00Z")
    assert EntitlementStore(path).get("u")["limits"]["daily_jobs"]==1000


def test_unknown_plan_rejected_and_catalog_disclaims_billing(tmp_path: Path):
    store=EntitlementStore(tmp_path/"e.db")
    with pytest.raises(ValueError,match="unknown plan"): store.assign("u","enterprise","now")
    catalog=public_catalog()
    assert catalog["billing"]["status"]=="external"
    assert all("price" not in plan for plan in catalog["plans"])


def test_product_catalog_and_entitlement_api(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from meemee import api
    from meemee.entitlements import EntitlementStore
    from meemee.quotas import QuotaStore

    monkeypatch.setattr(api,"entitlements",EntitlementStore(tmp_path/"entitlements.db"))
    monkeypatch.setattr(api,"quotas",QuotaStore(tmp_path/"quotas.db"))
    client=TestClient(api.app); auth={"Authorization":"Bearer test-bootstrap-token"}
    catalog=client.get("/v1/product/plans",headers=auth)
    assert catalog.status_code==200 and catalog.json()["billing"]["status"]=="external"
    assigned=client.put("/v1/entitlements/customer-1",headers=auth,json={"plan":"team"})
    assert assigned.status_code==200 and assigned.json()["limits"]["daily_jobs"]==1000
    assert api.quotas.limit("customer-1")==1000
    invalid=client.put("/v1/entitlements/customer-1",headers=auth,json={"plan":"enterprise"})
    assert invalid.status_code==422
