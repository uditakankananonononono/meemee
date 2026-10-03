# ICS timezone conversion

IANA TZID times now convert to UTC, including DTSTART and DTEND. `TZID=Asia/Kolkata:20261001T150000` becomes `2026-10-01T09:30:00+00:00`. UTC/Z timestamps remain UTC. Folded content lines support spaces and tabs.

Safety changes: invalid timestamps, unknown timezone names, contradictory UTC+TZID and ambiguous/nonexistent DST local timestamps raise ValueError instead of silently inventing an instant. Floating timestamps need explicit `ICSConnector(..., default_timezone="Asia/Kolkata")` configuration. Existing feeds that omit timezone data may therefore now fail closed; configure their actual calendar timezone before ingesting. DATE/all-day values remain UTC-midnight date anchors with all_day/date_anchor_only metadata, not true inferred instants. Raw end values remain in metadata and end_utc adds the parsed UTC endpoint.

Parser behavior (follow-up fixes):

- Properties inside nested components (for example `VALARM`) are ignored; they no longer overwrite the event's SUMMARY, DESCRIPTION, DTSTART or DTEND. A nested component that is never closed, or an `END:` that does not match, raises ValueError.
- A `VEVENT` that is never closed now raises ValueError instead of being silently dropped.
- `DTEND` earlier than `DTSTART` (compared as UTC instants) raises ValueError. Equal values are accepted.
- `DTSTART` and `DTEND` must both be DATE or both be DATE-TIME; a mix raises ValueError.
- A present but empty `DTEND` raises ValueError. A missing `DTEND` is still allowed (`end_utc` is None).
- Failure policy is unchanged and deliberate: a missing, empty or malformed DTSTART, or any error above, aborts the whole feed with ValueError. Events are not skipped and nothing is guessed. `ICSConnector.fetch` has no caller in this repository today, so no code currently surfaces these errors to the owner; whoever wires it up must do that.

Packaging: `tzdata` is now a base dependency in `pyproject.toml` and `uv.lock` because `zoneinfo` needs an IANA database, which Windows and minimal container images do not ship. `meemee/companion/models.py` also uses `ZoneInfo`, so it benefits too. `pyproject.pg.toml` (the separate PostgreSQL package) does not use zoneinfo and is unchanged. This was only tested on Linux; no Windows run was done, and the owner's operating system is unconfirmed.

Limitations: this is not a complete RFC 5545 parser. Custom VTIMEZONE definitions, recurrence expansion and timezone aliases outside the installed IANA database are not implemented. Upstream ingestion should surface conversion errors to the owner rather than swallowing them.

Evidence: `tests/test_ics_timezone_regression.py` (78 parametrized cases from 23 test functions: 62 from the earlier timezone/grammar fix, 16 added for the parser fixes) plus `tests/test_connectors.py` (3 cases) passed on Linux, Python 3.10.12. The original fix commit was recorded as 12 tests; that counted test functions, not parametrized cases. Covered Kolkata start/end, unchanged UTC, all-day anchors, explicit floating defaults, bad/unknown zones, DST gaps/ambiguity and winter/summer offsets. Four pre-existing RSS Element truthiness warnings remain unrelated to this fix.
