"""Minimal receiver fixture: preserve raw bytes, verify first, parse JSON second."""

import os

from fastapi import FastAPI, Header, HTTPException, Request

from meemee.webhook_verify import verify_signature

app = FastAPI()


@app.post("/webhooks/meemee")
async def receive(
    request: Request,
    x_meemee_timestamp: str = Header(),
    x_meemee_signature_256: str = Header(),
):
    body = await request.body()
    secret = os.environ["MEEMEE_RECEIVER_SECRET"]
    if not verify_signature(secret, x_meemee_timestamp, body, x_meemee_signature_256):
        raise HTTPException(401, "invalid or stale signature")
    event = await request.json()
    # Deduplicate durably on event["event_id"] before effects in real receivers.
    return {"accepted": True, "event_id": event["event_id"]}
