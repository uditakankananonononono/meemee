from pathlib import Path

import httpx
import pytest

from meemee.config import Settings
from meemee.preflight import run_preflight
from meemee.vault import SecretVault


def settings(tmp_path: Path, **changes) -> Settings:
    values = {
        "data_dir": tmp_path,
        "api_token": "a" * 32,
        "vault_key": SecretVault.generate_key(),
        "model_base_url": "https://model.example/v1",
        "readiness_min_free_bytes": 1,
    }
    values.update(changes)
    return Settings(**values)


@pytest.mark.asyncio
async def test_preflight_passes_and_checks_existing_database(tmp_path: Path):
    import sqlite3

    sqlite3.connect(tmp_path / "jobs.sqlite3").execute("CREATE TABLE jobs(id TEXT)").connection.close()
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request))
    )
    report = await run_preflight(settings(tmp_path), client, require_model=True)
    await client.aclose()
    assert report["status"] == "pass"
    assert report["summary"]["failed"] == 0
    assert any(check["name"] == "database_integrity:jobs.sqlite3" for check in report["checks"])


@pytest.mark.asyncio
async def test_preflight_fails_without_required_secrets_and_redacts_values(tmp_path: Path):
    token = "short-secret"
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(503, request=request))
    )
    report = await run_preflight(
        settings(tmp_path, api_token=token, vault_key=None), client, require_model=True
    )
    await client.aclose()
    assert report["status"] == "fail" and report["summary"]["failed"] == 3
    assert token not in str(report)


@pytest.mark.asyncio
async def test_optional_offline_model_is_warning_only(tmp_path: Path):
    def offline(request):
        raise httpx.ConnectError("offline", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(offline))
    report = await run_preflight(settings(tmp_path), client, require_model=False)
    await client.aclose()
    assert report["status"] == "pass"
    assert report["summary"]["warnings"] == 1

@pytest.mark.asyncio
async def test_postgres_preflight_fails_without_dsn(tmp_path):
    settings=Settings(data_dir=tmp_path,persistence_backend="postgresql",postgres_dsn=None,api_token="x"*32,vault_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    report=await run_preflight(settings,client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200))))
    check=next(item for item in report["checks"] if item["name"]=="postgresql_connection_and_schema")
    assert report["status"]=="fail" and not check["ok"] and "POSTGRES_DSN" in check["error"]

@pytest.mark.asyncio
async def test_postgres_preflight_checks_live_schema(monkeypatch,tmp_path):
    import meemee_persist_pg
    class DB:
        def __init__(self,*a,**k): pass
        def close(self): pass
    class Migrations:
        def __init__(self,db): pass
        def pending(self): return []
    monkeypatch.setattr(meemee_persist_pg,"Database",DB); monkeypatch.setattr(meemee_persist_pg,"MigrationStore",Migrations)
    settings=Settings(data_dir=tmp_path,persistence_backend="postgresql",postgres_dsn="postgresql://test",api_token="x"*32,vault_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    report=await run_preflight(settings,client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200))))
    assert next(item for item in report["checks"] if item["name"]=="postgresql_connection_and_schema")["ok"]
