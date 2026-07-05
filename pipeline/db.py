"""DuckDB schema and upsert helpers (spec §3, deviations noted in docs/DATA-SOURCE.md)."""

import hashlib
from pathlib import Path

import duckdb

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "db.duckdb"

SCHEMA = """
CREATE TABLE IF NOT EXISTS facilities (
  id                   TEXT PRIMARY KEY,   -- State ID# from the source
  name                 TEXT,
  facility_type        TEXT,
  address              TEXT,
  city                 TEXT,
  zip                  TEXT,
  lat                  DOUBLE,
  lon                  DOUBLE,
  permit_status        TEXT,               -- not exposed by CDP public view; stays NULL
  cdp_establishment_id TEXT,               -- internal CDP id, learned via grid walk
  fetched_at           TIMESTAMP
);
CREATE TABLE IF NOT EXISTS inspections (
  id                  TEXT PRIMARY KEY,    -- sha1(facility|date|score), stable across runs
  facility_id         TEXT,
  inspection_date     DATE,
  score               DOUBLE,
  grade               TEXT,                -- as reported by the source (NULL for ungraded programs)
  inspection_type     TEXT,                -- not exposed by CDP public view; stays NULL
  cdp_inspection_id   TEXT,
  source_url          TEXT,
  violations_fetched  BOOLEAN DEFAULT FALSE,
  general_comments    TEXT,
  fetched_at          TIMESTAMP
);
CREATE TABLE IF NOT EXISTS violations (
  id              TEXT PRIMARY KEY,        -- inspection_id:item:seq
  inspection_id   TEXT,
  code            TEXT,                    -- NC inspection form item number
  description     TEXT,
  category        TEXT,                    -- risk_factor (items 1-29) / good_retail_practice
  points_deducted DOUBLE,
  cdi             BOOLEAN,
  repeat          BOOLEAN,
  vr              BOOLEAN,
  comments        TEXT
);
"""


def connect(path: Path | str = DB_PATH) -> duckdb.DuckDBPyConnection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(SCHEMA)
    return con


def inspection_key(facility_id: str, inspection_date, score) -> str:
    raw = f"{facility_id}|{inspection_date.isoformat()}|{score}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def upsert_facility(con, *, id, name, facility_type, address, city, zip,
                    fetched_at, cdp_establishment_id=None):
    con.execute(
        """
        INSERT INTO facilities (id, name, facility_type, address, city, zip,
                                cdp_establishment_id, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
          name = excluded.name,
          facility_type = excluded.facility_type,
          address = excluded.address,
          city = excluded.city,
          zip = excluded.zip,
          cdp_establishment_id = coalesce(excluded.cdp_establishment_id,
                                          facilities.cdp_establishment_id),
          fetched_at = excluded.fetched_at
        """,
        [id, name, facility_type, address, city, zip, cdp_establishment_id, fetched_at],
    )


def upsert_inspection(con, *, facility_id, inspection_date, score, grade,
                      fetched_at, cdp_inspection_id=None, source_url=None):
    iid = inspection_key(facility_id, inspection_date, score)
    con.execute(
        """
        INSERT INTO inspections (id, facility_id, inspection_date, score, grade,
                                 cdp_inspection_id, source_url, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
          grade = excluded.grade,
          cdp_inspection_id = coalesce(excluded.cdp_inspection_id,
                                       inspections.cdp_inspection_id),
          source_url = coalesce(excluded.source_url, inspections.source_url),
          fetched_at = excluded.fetched_at
        """,
        [iid, facility_id, inspection_date, score, grade,
         cdp_inspection_id, source_url, fetched_at],
    )
    return iid


def replace_violations(con, inspection_id: str, violations, general_comments: str):
    con.execute("DELETE FROM violations WHERE inspection_id = ?", [inspection_id])
    for n, v in enumerate(violations):
        category = "risk_factor" if int(v.item) <= 29 else "good_retail_practice"
        con.execute(
            """
            INSERT INTO violations (id, inspection_id, code, description, category,
                                    points_deducted, cdi, repeat, vr, comments)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [f"{inspection_id}:{v.item}:{n}", inspection_id, v.item, v.description,
             category, v.demerits, v.cdi, v.repeat, v.vr, v.comments],
        )
    con.execute(
        "UPDATE inspections SET violations_fetched = TRUE, general_comments = ? WHERE id = ?",
        [general_comments, inspection_id],
    )
