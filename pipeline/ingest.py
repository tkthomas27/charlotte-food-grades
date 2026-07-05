"""Ingestion CLI.

  python -m pipeline.ingest backfill --start 2019-01-01 [--end 2026-06-30]
      CSV-export chunks (quarterly, auto-split on 504) -> facilities + inspections.

  python -m pipeline.ingest update [--days 35] [--max-violations 400]
      Grid walk of the recent window: upserts rows, learns internal CDP ids,
      fetches violations for inspections that don't have them yet.

  python -m pipeline.ingest geocode
      Census batch geocode for facilities missing lat/lon.

  python -m pipeline.ingest export
      Write site/data/facilities.geojson + meta.json.

  python -m pipeline.ingest weekly
      update -> geocode -> export (what the scheduled Action runs).
"""

import argparse
import sys
import traceback
from datetime import date, datetime, timedelta, timezone

from . import db as dbm
from .cdpehs import CdpehsClient
from .export import export_site_data
from .geocode import geocode_missing

GRID_PAGE_SIZE = 100


def log(*args):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}]", *args, flush=True)


def _quarters(start: date, end: date):
    cur = start
    while cur <= end:
        q_end = min(cur + timedelta(days=91), end)
        yield cur, q_end
        cur = q_end + timedelta(days=1)


def _upsert_csv_rows(con, rows, fetched_at):
    n = 0
    for r in rows:
        state_id = r.get("State ID#", "")
        d = r.get("Inspection Date", "")
        if not state_id or not d:
            continue
        m, day, y = d.split("/")
        insp_date = date(int(y), int(m), int(day))
        score_txt = (r.get("Final Score") or "").replace(",", "")
        try:
            score = float(score_txt)
        except ValueError:
            score = None
        grade = r.get("Grade") or None
        if grade in ("N/A", ""):
            grade = None
        address = r.get("Premise Address 1", "")
        if r.get("Premise Address 2"):
            address += ", " + r["Premise Address 2"]
        dbm.upsert_facility(
            con, id=state_id, name=r.get("Premises Name", ""),
            facility_type=r.get("Establishment Type", ""),
            address=address, city=r.get("Premise City", ""),
            zip=r.get("Premise ZIP", ""), fetched_at=fetched_at,
        )
        dbm.upsert_inspection(
            con, facility_id=state_id, inspection_date=insp_date,
            score=score, grade=grade, fetched_at=fetched_at,
        )
        n += 1
    return n


def cmd_backfill(args):
    con = dbm.connect(args.db)
    client = CdpehsClient()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else date.today()
    fetched_at = datetime.now(timezone.utc)
    total = 0
    failures = []
    for q_start, q_end in _quarters(start, end):
        try:
            rows = client.export_csv(q_start, q_end)
            n = _upsert_csv_rows(con, rows, fetched_at)
            total += n
            log(f"backfill {q_start}..{q_end}: {n} inspections")
        except Exception as e:  # keep going; one bad window shouldn't kill the run
            failures.append((q_start, q_end, str(e)))
            log(f"backfill {q_start}..{q_end} FAILED: {e}")
    con.close()
    log(f"backfill done: {total} inspections, {len(failures)} failed windows")
    if failures:
        for f in failures:
            log("  failed:", *f)
        sys.exit(1 if total == 0 else 0)


def cmd_update(args):
    con = dbm.connect(args.db)
    client = CdpehsClient()
    date_to = date.today()
    date_from = date_to - timedelta(days=args.days)
    fetched_at = datetime.now(timezone.utc)
    budget = args.max_violations
    seen = 0
    for page_num, (html, rows) in enumerate(
            client.walk_grid(date_from, date_to, page_size=GRID_PAGE_SIZE), start=1):
        if not rows:
            break
        log(f"update: page {page_num}, {len(rows)} rows")
        for row in rows:
            seen += 1
            dbm.upsert_facility(
                con, id=row.state_id, name=row.name, facility_type=row.facility_type,
                address=row.address, city=row.city, zip=row.zip, fetched_at=fetched_at,
            )
            iid = dbm.upsert_inspection(
                con, facility_id=row.state_id, inspection_date=row.inspection_date,
                score=row.score, grade=row.grade, fetched_at=fetched_at,
            )
            done = con.execute(
                "SELECT violations_fetched FROM inspections WHERE id = ?", [iid]
            ).fetchone()[0]
            if done or budget <= 0:
                continue
            try:
                ids = client.resolve_ids(html, row.repeater_index)
                if not ids:
                    continue
                est_id, insp_id = ids
                vp = client.fetch_violations(est_id, insp_id)
                con.execute(
                    """UPDATE inspections
                       SET cdp_inspection_id = ?, source_url = ?
                       WHERE id = ?""",
                    [insp_id, client.violation_url(est_id, insp_id), iid],
                )
                con.execute(
                    "UPDATE facilities SET cdp_establishment_id = ? WHERE id = ?",
                    [est_id, row.state_id],
                )
                dbm.replace_violations(con, iid, vp.violations, vp.general_comments)
                budget -= 1
            except Exception:
                log(f"update: violations failed for {row.name} {row.inspection_date}")
                traceback.print_exc()
    con.close()
    log(f"update done: {seen} grid rows, violation budget left {budget}")


def cmd_geocode(args):
    con = dbm.connect(args.db)
    geocode_missing(con, log=log)
    con.close()


def cmd_export(args):
    con = dbm.connect(args.db)
    export_site_data(con, log=log)
    con.close()


def cmd_weekly(args):
    cmd_update(args)
    cmd_geocode(args)
    cmd_export(args)


def main():
    p = argparse.ArgumentParser(prog="pipeline.ingest")
    p.add_argument("--db", default=str(dbm.DB_PATH))
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backfill")
    b.add_argument("--start", required=True)
    b.add_argument("--end")
    b.set_defaults(fn=cmd_backfill)

    u = sub.add_parser("update")
    u.add_argument("--days", type=int, default=35)
    u.add_argument("--max-violations", type=int, default=400)
    u.set_defaults(fn=cmd_update)

    g = sub.add_parser("geocode")
    g.set_defaults(fn=cmd_geocode)

    e = sub.add_parser("export")
    e.set_defaults(fn=cmd_export)

    w = sub.add_parser("weekly")
    w.add_argument("--days", type=int, default=35)
    w.add_argument("--max-violations", type=int, default=400)
    w.set_defaults(fn=cmd_weekly)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
