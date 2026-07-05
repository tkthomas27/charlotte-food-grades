# Charlotte Metro Food Grades

Interactive map of food-service inspection grades across Charlotte / Mecklenburg County, built on scraped public inspection data, plus a live MCP server exposing the same dataset.

**Status:** pre-build — spec finalized, implementation not started.

## Deliverables

1. **Map site** — static, MapLibre GL JS, full-bleed map of inspection grades. Rebuilt weekly by a scheduled GitHub Action and served from GitHub Pages.
2. **MCP server** — remote (HTTP/SSE) read-only server over the same DuckDB dataset, hosted on Fly.io/Railway.

Full requirements: [docs/SPEC.md](docs/SPEC.md)

## Resolved decisions (2026-07-05)

The open decisions from spec §10 were settled before build:

1. **CDPEHS vs. EHIDS** — to be verified empirically at build start (inspect both systems, pick the current source of record, document why).
2. **Facility types in v1** — full Food & Facilities Sanitation scope (restaurants, hotels, mobile food, child care, schools), with the facility-type filter doing the work.
3. **Phase 2 civic layers** (Charlotte Code Enforcement, Quality of Life Explorer) — deferred; not in v1.
4. **MCP hosting** — Fly.io or Railway (not Posit Connect).
5. **MCP access** — open read-only, no auth, with rate limiting.

## Data disclaimer

This is an unofficial visualization of public Mecklenburg County inspection records, refreshed weekly. Grades can change between refreshes — the official county lookup is the authoritative record.
