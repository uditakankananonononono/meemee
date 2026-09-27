# Monitor deadline correctness

Monitor creation now rejects a zero/negative or oversized fire budget and requires deadlines to be ISO-8601 instants with an explicit timezone. It normalizes them to UTC before storing. Both SQLite and PostgreSQL evaluate at a normalized UTC instant, avoiding wrong expiry decisions when input timestamps have different offsets. A missing deadline remains allowed.

This is a correctness fix, not a new source connector or continuous scan. Monitors only evaluate events delivered by existing connected sources; they do not authorize an external response. Local SQLite tests cover validation and offset-equivalent instants. PostgreSQL monitor behavior shares the normalization path but was not live-tested in this change, and no whole-suite result is claimed.
