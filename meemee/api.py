from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .runtime import build_agent

app = FastAPI(title="Meemee", version="0.1.0")
agent = build_agent()


class RunRequest(BaseModel):
    goal: str = Field(min_length=2, max_length=20_000)
    approve_writes: bool = False


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": "0.1.0"}


@app.post("/v1/runs")
async def create_run(request: RunRequest):
    def approval(name, arguments, risk):
        return request.approve_writes
    try:
        return await agent.run(request.goal, approve=approval)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"agent run failed: {exc}") from exc
