# Connector fidelity repair, October 7

The independent audit's Protocol-only description of connectors was too narrow:
RSS, ICS and Gmail implementations already existed. This repair deepens actual
fetch/normalization behavior instead of treating a Protocol declaration as a product.
It does not claim unrestricted social media ingestion or automatic authenticated sync.

## Reproduced behavior

`tests/test_connector_fidelity.py` verifies:

- Atom fetched from an actual local HTTP server: href identifier, ISO timestamp and
  nested XHTML body survive ingestion into an owner-scoped ContextStore.
- XML external entities are rejected; oversized feeds raise instead of silently truncating.
- RFC5545 TZID conversion, folded lines, escaped descriptions, recurring-rule metadata,
  detached occurrence identity and all-day dates survive calendar normalization.
- Floating event times require an explicit timezone, never quietly become UTC.
- Gmail follows all pages, deduplicates message IDs, walks nested MIME parts, downloads
  attachment-backed plain-text bodies and decodes declared charset.
- Gmail overlaps the cursor second, sorts oldest-first before cursor ingestion and
  rejects repeated tokens or page-budget exhaustion without returning partial data.

Sources used for API/format behavior:
- https://developers.google.com/gmail/api/reference/rest/v1/users.messages/list
- https://developers.google.com/workspace/gmail/api/guides/filtering
- https://datatracker.ietf.org/doc/html/rfc5545

Gmail tests use recorded-format responses through a mocked HTTP transport, not a live
owner mailbox. The local feed test exercises real HTTP. No external messages were sent,
provider accounts added, secrets collected, or private data used for these tests.

## Limits

The ICS connector preserves RRULE but does not expand recurring instances. Gmail is
an inbox-message poller, not a full History API change feed: old messages newly moved
into the inbox or label/deletion changes can be missed by a timestamp cursor. HTML-only
mail retains a snippet with `body_complete=false`; HTML-to-text is not implemented.
Configured credentials and a caller driving fetch/ingest are still required. A successful
mock test is not live Gmail authorization, integration setup or a deployed background sync.
These limitations remain open rather than becoming a claim that all sources are connected.

Context version hashes on both SQLite and PostgreSQL now include event time, kind,
metadata, provenance and visibility as well as prose. An appointment moved without
changing its title/body is therefore not silently discarded. Existing older hashes
are preserved; the first re-ingestion under the new version identity can create one
additional version. No historical data is deleted or rewritten by this repair.
