"""Export the DuckDB dataset to the static GeoJSON + metadata the map site reads."""

import json
from datetime import datetime, timezone
from pathlib import Path

SITE_DATA = Path(__file__).resolve().parent.parent / "site" / "data"


def export_site_data(con, out_dir: Path = SITE_DATA, log=print):
    out_dir.mkdir(parents=True, exist_ok=True)

    facilities = con.execute(
        """
        WITH latest AS (
          SELECT facility_id,
                 max_by(id, inspection_date) AS insp_id,
                 max_by(score, inspection_date) AS score,
                 max_by(grade, inspection_date) AS grade,
                 max_by(source_url, inspection_date) AS source_url,
                 max(inspection_date) AS last_date
          FROM inspections
          GROUP BY facility_id
        ),
        history AS (
          SELECT facility_id,
                 list({'d': inspection_date, 's': score} ORDER BY inspection_date DESC)[:8]
                   AS spark
          FROM inspections
          GROUP BY facility_id
        )
        SELECT f.id, f.name, f.facility_type, f.address, f.city, f.zip,
               f.lat, f.lon, f.fetched_at,
               l.insp_id, l.score, l.grade, l.last_date, l.source_url,
               h.spark
        FROM facilities f
        JOIN latest l ON l.facility_id = f.id
        LEFT JOIN history h ON h.facility_id = f.id
        WHERE f.lat IS NOT NULL
        """
    ).fetchall()

    viols = {}
    for insp_id, code, desc, dem in con.execute(
        """
        SELECT inspection_id, code, description, points_deducted
        FROM violations
        ORDER BY inspection_id, points_deducted DESC NULLS LAST
        """
    ).fetchall():
        viols.setdefault(insp_id, [])
        if len(viols[insp_id]) < 5:
            viols[insp_id].append({"code": code, "desc": desc, "pts": dem})

    features = []
    for (fid, name, ftype, address, city, zip_, lat, lon, fetched_at,
         insp_id, score, grade, last_date, source_url, spark) in facilities:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [round(lon, 6), round(lat, 6)]},
            "properties": {
                "id": fid,
                "name": name,
                "type": ftype,
                "address": address,
                "city": city,
                "zip": zip_,
                "grade": grade,
                "score": score,
                "last_date": last_date.isoformat(),
                "spark": [{"d": e["d"].isoformat(), "s": e["s"]} for e in (spark or [])],
                "violations": viols.get(insp_id, []),
                "source_url": source_url,
                "fetched_at": fetched_at.strftime("%Y-%m-%d"),
            },
        })

    geojson = {"type": "FeatureCollection", "features": features}
    (out_dir / "facilities.geojson").write_text(json.dumps(geojson, separators=(",", ":")))

    counts = dict(con.execute(
        """
        SELECT coalesce(grade, 'ungraded'), count(*)
        FROM (SELECT facility_id, max_by(grade, inspection_date) AS grade
              FROM inspections GROUP BY facility_id) GROUP BY 1
        """
    ).fetchall())
    meta = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "facilities": len(features),
        "inspections": con.execute("SELECT count(*) FROM inspections").fetchone()[0],
        "violations": con.execute("SELECT count(*) FROM violations").fetchone()[0],
        "grades": counts,
        "types": dict(con.execute(
            "SELECT facility_type, count(*) FROM facilities GROUP BY 1 ORDER BY 2 DESC"
        ).fetchall()),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    log(f"export: {len(features)} facilities -> {out_dir/'facilities.geojson'}")
