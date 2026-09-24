# Builders

Each parallel builder claims one component here before building, so work does not collide. One line per claim: builder, branch, component, files touched.

- pb7 | branch `pb7` | Forced interruption of blocking tools (process-isolated tool execution, kill on cancel/timeout) | `meemee/isolation.py`, `meemee/tools/base.py` (isolation hook only), `tests/test_isolation.py`, docs
