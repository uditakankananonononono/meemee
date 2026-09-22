from __future__ import annotations

import asyncio

import httpx


async def run_api_loadcheck(app, token: str, requests: int = 200, concurrency: int = 20) -> dict:
    """Run parallel in-process HTTP requests through the complete ASGI middleware stack."""
    if requests < 1 or concurrency < 1:
        raise ValueError("requests and concurrency must be positive")
    semaphore = asyncio.Semaphore(concurrency)
    routes = ("/health", "/v1/whoami", "/v1/jobs?limit=10")
    statuses: dict[str, int] = {}
    missing_request_ids = 0
    errors: list[str] = []
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        async def one(index: int) -> None:
            nonlocal missing_request_ids
            route = routes[index % len(routes)]
            headers = {} if route == "/health" else {"Authorization": f"Bearer {token}"}
            try:
                async with semaphore:
                    response = await client.get(route, headers=headers)
                key = str(response.status_code); statuses[key] = statuses.get(key, 0) + 1
                if "x-request-id" not in response.headers: missing_request_ids += 1
            except httpx.HTTPError as exc:
                errors.append(type(exc).__name__)
        await asyncio.gather(*(one(index) for index in range(requests)))
    unexpected_5xx = sum(count for status, count in statuses.items() if int(status) >= 500)
    completed = sum(statuses.values())
    status = "pass" if completed == requests and not errors and not unexpected_5xx and not missing_request_ids else "fail"
    return {
        "status": status, "requests": requests, "concurrency": concurrency,
        "completed": completed, "statuses": statuses, "errors": errors,
        "unexpected_5xx": unexpected_5xx, "missing_request_ids": missing_request_ids,
    }
