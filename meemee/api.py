from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .auth import require_api_token
from .jobs import JobStore
from .runtime import build_agent

app = FastAPI(title="Meemee", version="0.1.0")
agent = build_agent()
jobs = JobStore(Path(agent.memory.connection.execute("PRAGMA database_list").fetchone()[2]).with_name("jobs.sqlite3"))


class RunRequest(BaseModel):
    goal: str = Field(min_length=2, max_length=20_000)
    approve_writes: bool = False


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": "0.1.0"}


@app.post("/v1/runs", dependencies=[Depends(require_api_token)])
async def create_run(request: RunRequest):
    def approval(name, arguments, risk):
        return request.approve_writes
    try:
        return await agent.run(request.goal, approve=approval)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"agent run failed: {exc}") from exc


class JobRequest(BaseModel):
    goal: str = Field(min_length=2, max_length=20_000)
    run_at: str | None = None


@app.post("/v1/jobs", dependencies=[Depends(require_api_token)])
def create_job(request: JobRequest):
    from datetime import datetime
    run_at = datetime.fromisoformat(request.run_at.replace("Z", "+00:00")) if request.run_at else None
    return {"id": jobs.enqueue(request.goal, run_at)}


@app.get("/v1/jobs/{job_id}", dependencies=[Depends(require_api_token)])
def get_job(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job
