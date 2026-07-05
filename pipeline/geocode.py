"""Geocode facilities with the Census Bureau batch geocoder (free, no key).

https://geocoding.geo.census.gov/geocoder/locations/addressbatch
Input CSV: id,street,city,state,zip — up to 10k rows per request.
"""

import csv
import io

import requests

BATCH_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
BATCH_SIZE = 2500


def geocode_missing(con, log=print) -> int:
    rows = con.execute(
        """
        SELECT id, address, city, zip FROM facilities
        WHERE lat IS NULL AND address IS NOT NULL AND address <> ''
        """
    ).fetchall()
    if not rows:
        log("geocode: nothing to do")
        return 0
    matched = 0
    for start in range(0, len(rows), BATCH_SIZE):
        chunk = rows[start:start + BATCH_SIZE]
        buf = io.StringIO()
        writer = csv.writer(buf)
        for fid, address, city, zip_ in chunk:
            writer.writerow([fid, address, city or "", "NC", zip_ or ""])
        resp = requests.post(
            BATCH_URL,
            files={"addressFile": ("addresses.csv", buf.getvalue(), "text/csv")},
            data={"benchmark": "Public_AR_Current"},
            timeout=600,
        )
        resp.raise_for_status()
        for rec in csv.reader(io.StringIO(resp.text)):
            # id, input addr, match flag, exact/non-exact, matched addr, "lon,lat", line id, side
            if len(rec) >= 6 and rec[2].strip() == "Match" and rec[5]:
                lon, lat = rec[5].split(",")
                con.execute(
                    "UPDATE facilities SET lat = ?, lon = ? WHERE id = ?",
                    [float(lat), float(lon), rec[0]],
                )
                matched += 1
        log(f"geocode: {min(start + BATCH_SIZE, len(rows))}/{len(rows)} submitted, "
            f"{matched} matched so far")
    return matched
