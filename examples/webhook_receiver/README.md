# Verified webhook receiver

Set `MEEMEE_RECEIVER_SECRET` to the one-time secret returned when the subscription is created. Run `uvicorn examples.webhook_receiver.app:app`. The fixture reads raw bytes, verifies timestamped HMAC before parsing JSON, and returns the event ID. A production receiver must persist event-ID deduplication before effects and return 2xx only after durable acceptance.
