from __future__ import annotations

import concurrent.futures
import tempfile
import uuid
from pathlib import Path

from .audit import AuditLog
from .auth import TokenStore
from .entitlements import EntitlementStore
from .jobs import JobStore


def run_loadcheck(
    operations: int = 1_000,
    workers: int = 16,
    data_dir: Path | None = None,
) -> dict:
    """Exercise shared-store contention; any exception or count mismatch fails the gate."""
    if operations < 1 or workers < 1:
        raise ValueError("operations and workers must be positive")
    temporary = tempfile.TemporaryDirectory(prefix="meemee-loadcheck-") if data_dir is None else None
    root = Path(temporary.name) if temporary else data_dir
    assert root is not None
    root.mkdir(parents=True, exist_ok=True)
    tokens = TokenStore(root / "auth.sqlite3")
    audit = AuditLog(root / "audit.sqlite3")
    jobs = JobStore(root / "jobs.sqlite3")
    entitlements = EntitlementStore(root / "entitlements.sqlite3")
    principal_id = "loadcheck_" + uuid.uuid4().hex
    _, raw = tokens.create(principal_id, {"jobs:read", "jobs:write"})

    def operation(index: int) -> str:
        lane = index % 4
        if lane == 0:
            principal = tokens.authenticate(raw)
            if principal is None: raise RuntimeError("token authentication failed")
            return "authenticate"
        if lane == 1:
            audit.append("loadcheck", "operation", str(index), "success")
            return "audit"
        if lane == 2:
            jobs.enqueue(f"load operation {index}", principal=principal_id)
            return "job"
        entitlements.get(principal_id)
        return "entitlement"

    errors: list[str] = []
    counts = {name: 0 for name in ("authenticate", "audit", "job", "entitlement")}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(operation, index) for index in range(operations)]
        for future in futures:
            try: counts[future.result()] += 1
            except Exception as exc:  # noqa: BLE001 - native driver failures are failed operations
                errors.append(type(exc).__name__)
    audit_valid, broken_at = audit.verify()
    stored_jobs, cursor = 0, None
    while True:
        page, cursor = jobs.list_for_principal(principal_id, limit=500, cursor=cursor)
        stored_jobs += len(page)
        if cursor is None:
            break
    expected_jobs = counts["job"]
    status = "pass" if not errors and audit_valid and stored_jobs == expected_jobs else "fail"
    result = {
        "status": status, "operations": operations, "workers": workers,
        "principal": principal_id,
        "counts": counts, "errors": errors, "audit_valid": audit_valid,
        "audit_broken_at": broken_at, "stored_jobs": stored_jobs,
    }
    if temporary: temporary.cleanup()
    return result
