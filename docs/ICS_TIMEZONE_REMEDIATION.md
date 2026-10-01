# ICS timezone conversion

IANA TZID times now convert to UTC, including DTSTART and DTEND. `TZID=Asia/Kolkata:20261001T150000` becomes `2026-10-01T09:30:00+00:00`. UTC/Z timestamps remain UTC. Folded content lines support spaces and tabs.

Safety changes: invalid timestamps, unknown timezone names, contradictory UTC+TZID and ambiguous/nonexistent DST local timestamps raise ValueError instead of silently inventing an instant. Floating timestamps need explicit `ICSConnector(..., default_timezone="Asia/Kolkata")` configuration. Existing feeds that omit timezone data may therefore now fail closed; configure their actual calendar timezone before ingesting. DATE/all-day values remain UTC-midnight date anchors with all_day/date_anchor_only metadata, not true inferred instants. Raw end values remain in metadata and end_utc adds the parsed UTC endpoint.

Limitations: this is not a complete RFC 5545 parser. Custom VTIMEZONE definitions, recurrence expansion and timezone aliases outside the installed IANA database are not implemented. Upstream ingestion should surface conversion errors to the owner rather than swallowing them.

Evidence: 12 connector/regression tests passed on Python 3.12.14. Covered Kolkata start/end, unchanged UTC, all-day anchors, explicit floating defaults, bad/unknown zones, DST gaps/ambiguity and winter/summer offsets. Four pre-existing RSS Element truthiness warnings remain unrelated to this fix.
