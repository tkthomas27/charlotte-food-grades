"""Remote MCP server over the Charlotte food-grades DuckDB dataset.

Read-only, no auth (public government records), IP rate-limited.
Transport: streamable HTTP at /mcp — reachable by any remote MCP client.

Env:
  PORT            listen port (default 8080)
  DB_PATH         local DuckDB file (default ./data/db.duckdb)
  DB_URL          optional URL to download the DuckDB file from at boot and
                  every REFRESH_HOURS (the GitHub release asset the pipeline
                  publishes)
  REFRESH_HOURS   how often to re-download DB_URL (default 24)
  RATE_LIMIT      requests per minute per client IP (default 60)
"""

import os
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

import duckdb
import requests as http_requests
from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

DB_PATH = Path(os.environ.get("DB_PATH", "data/db.duckdb"))
DB_URL = os.environ.get("DB_URL", "")
REFRESH_HOURS = float(os.environ.get("REFRESH_HOURS", "24"))
RATE_LIMIT = int(os.environ.get("RATE_LIMIT", "60"))
PORT = int(os.environ.get("PORT", "8080"))

mcp = FastMCP(
    "charlotte-food-grades",
    instructions=(
        "Food-service inspection grades for Charlotte / Mecklenburg County, NC. "
        "Unofficial weekly snapshot of public county records; the county lookup "
        "(public.cdpehs.com) is the authoritative source."
    ),
    host="0.0.0.0",
    port=PORT,
)


# ---------------- data access ----------------

def _download_db():
    tmp = DB_PATH.with_suffix(".tmp")
    resp = http_requests.get(DB_URL, timeout=300)
    resp.raise_for_status()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(resp.content)
    tmp.replace(DB_PATH)
    print(f"downloaded db: {DB_PATH.stat().st_size / 1e6:.1f} MB", flush=True)


def ensure_db():
    if DB_URL and (not DB_PATH.exists()
                   or time.time() - DB_PATH.stat().st_mtime > REFRESH_HOURS * 3600):
        _download_db()
    if not DB_PATH.exists():
        raise SystemExit(f"no database at {DB_PATH} and DB_URL not set")


def _refresher():
    while True:
        time.sleep(3600)
        try:
            ensure_db()
        except Exception as e:
            print("db refresh failed:", e, flush=True)


def q(sql: str, params: list) -> list[dict]:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        cur = con.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        con.close()


def _iso(rows: list[dict]) -> list[dict]:
    for r in rows:
        for k, v in r.items():
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
    return rows


# ---------------- tools ----------------

@mcp.tool()
def search_establishments(query: str = "", facility_type: str = "",
                          min_score: float | None = None,
                          max_score: float | None = None,
                          zip: str = "", limit: int = 25) -> list[dict]:
    """Search food-service facilities by name/address substring, facility type
    (e.g. 'Restaurant'), latest-inspection score range, and/or ZIP code.
    Returns facility info with the most recent inspection grade and score."""
    limit = max(1, min(limit, 100))
    sql = """
      WITH latest AS (
        SELECT facility_id, max(inspection_date) AS last_date,
               max_by(score, inspection_date) AS score,
               max_by(grade, inspection_date) AS grade
        FROM inspections GROUP BY facility_id
      )
      SELECT f.id, f.name, f.facility_type, f.address, f.city, f.zip,
             f.lat, f.lon, l.grade, l.score, l.last_date
      FROM facilities f JOIN latest l ON l.facility_id = f.id
      WHERE 1=1
    """
    params: list = []
    if query:
        sql += " AND (f.name ILIKE ? OR f.address ILIKE ?)"
        params += [f"%{query}%", f"%{query}%"]
    if facility_type:
        sql += " AND f.facility_type ILIKE ?"
        params.append(f"%{facility_type}%")
    if min_score is not None:
        sql += " AND l.score >= ?"
        params.append(min_score)
    if max_score is not None:
        sql += " AND l.score <= ?"
        params.append(max_score)
    if zip:
        sql += " AND f.zip = ?"
        params.append(zip)
    sql += " ORDER BY l.last_date DESC LIMIT ?"
    params.append(limit)
    return _iso(q(sql, params))


@mcp.tool()
def get_establishment(id: str) -> dict:
    """Full record for one facility by its id (State ID#, e.g. '2060014945'):
    facility info, complete inspection history, and violations for each
    inspection where details were captured."""
    fac = q("SELECT * FROM facilities WHERE id = ?", [id])
    if not fac:
        return {"error": f"no facility with id {id}"}
    inspections = _iso(q(
        """SELECT id, inspection_date, score, grade, source_url, general_comments
           FROM inspections WHERE facility_id = ? ORDER BY inspection_date DESC""",
        [id]))
    for insp in inspections:
        insp["violations"] = q(
            """SELECT code, description, category, points_deducted, cdi, repeat, vr, comments
               FROM violations WHERE inspection_id = ?
               ORDER BY points_deducted DESC NULLS LAST""",
            [insp["id"]])
    out = _iso(fac)[0]
    out["inspections"] = inspections
    return out


@mcp.tool()
def nearby_establishments(lat: float, lon: float, radius_miles: float = 1.0,
                          min_score: float | None = None, limit: int = 25) -> list[dict]:
    """Facilities within radius_miles of a point, nearest first, with their
    latest grade/score. Optionally require a minimum latest score."""
    limit = max(1, min(limit, 100))
    radius_miles = min(radius_miles, 50.0)
    sql = """
      WITH latest AS (
        SELECT facility_id, max(inspection_date) AS last_date,
               max_by(score, inspection_date) AS score,
               max_by(grade, inspection_date) AS grade
        FROM inspections GROUP BY facility_id
      ),
      candidates AS (
        SELECT f.id, f.name, f.facility_type, f.address, f.city,
               l.grade, l.score, l.last_date,
               3958.8 * 2 * asin(sqrt(
                 sin(radians((f.lat - ?) / 2)) ** 2 +
                 cos(radians(?)) * cos(radians(f.lat)) *
                 sin(radians((f.lon - ?) / 2)) ** 2)) AS miles
        FROM facilities f JOIN latest l ON l.facility_id = f.id
        WHERE f.lat IS NOT NULL {score_filter}
      )
      SELECT * FROM candidates WHERE miles <= ? ORDER BY miles LIMIT ?
    """
    params: list = [lat, lat, lon]
    score_filter = ""
    if min_score is not None:
        score_filter = "AND l.score >= ?"
        params.append(min_score)
    sql = sql.format(score_filter=score_filter)
    params += [radius_miles, limit]
    rows = q(sql, params)
    for r in rows:
        r["miles"] = round(r["miles"], 2)
    return _iso(rows)


@mcp.tool()
def score_distribution(facility_type: str = "") -> dict:
    """Aggregate stats over facilities' latest inspections: count, mean/median
    score, quartiles, and grade counts. Optionally filtered by facility type —
    useful for 'how does this score compare?'."""
    sql = """
      WITH latest AS (
        SELECT i.facility_id, max_by(i.score, i.inspection_date) AS score,
               max_by(i.grade, i.inspection_date) AS grade
        FROM inspections i JOIN facilities f ON f.id = i.facility_id
        WHERE 1=1 {type_filter}
        GROUP BY i.facility_id
      )
      SELECT count(*) AS facilities,
             round(avg(score), 2) AS mean_score,
             round(median(score), 2) AS median_score,
             round(quantile_cont(score, 0.25), 2) AS p25,
             round(quantile_cont(score, 0.75), 2) AS p75,
             round(min(score), 2) AS min_score,
             round(max(score), 2) AS max_score
      FROM latest WHERE score IS NOT NULL
    """
    type_filter = ""
    params: list = []
    if facility_type:
        type_filter = "AND f.facility_type ILIKE ?"
        params.append(f"%{facility_type}%")
    stats = q(sql.format(type_filter=type_filter), params)[0]
    grade_sql = """
      WITH latest AS (
        SELECT i.facility_id, max_by(i.grade, i.inspection_date) AS grade
        FROM inspections i JOIN facilities f ON f.id = i.facility_id
        WHERE 1=1 {type_filter}
        GROUP BY i.facility_id
      )
      SELECT coalesce(grade, 'ungraded') AS grade, count(*) AS n
      FROM latest GROUP BY 1 ORDER BY 1
    """
    stats["grades"] = {r["grade"]: r["n"]
                       for r in q(grade_sql.format(type_filter=type_filter), params)}
    if facility_type:
        stats["facility_type_filter"] = facility_type
    return stats


# ---------------- rate limiting ----------------

class RateLimiter(BaseHTTPMiddleware):
    def __init__(self, app, per_minute: int):
        super().__init__(app)
        self.per_minute = per_minute
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    async def dispatch(self, request, call_next):
        ip = (request.headers.get("fly-client-ip")
              or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
              or (request.client.host if request.client else "unknown"))
        now = time.time()
        with self.lock:
            dq = self.hits[ip]
            while dq and dq[0] < now - 60:
                dq.popleft()
            if len(dq) >= self.per_minute:
                return JSONResponse({"error": "rate limit exceeded"}, status_code=429)
            dq.append(now)
        return await call_next(request)


def main():
    ensure_db()
    if DB_URL:
        threading.Thread(target=_refresher, daemon=True).start()
    app = mcp.streamable_http_app()
    app.add_middleware(RateLimiter, per_minute=RATE_LIMIT)

    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
