# Data source decision (spec §2a / §10.1)

**Decision: CDPEHS (`public.cdpehs.com`) is the source of record for this project.**
Investigated 2026-07-05.

## Why not EHIDS

`ehids.eh.ncdhhs.gov/eh-lookup` is a newer statewide Angular SPA with a real JSON
backend (`/eh-web/lookup/getFacilityByFacilityNameTypeCounty/{name}/{type}/{county}/{excl}`),
but every API endpoint 302-redirects to NC's CAS login (`login.eh.ncdhhs.gov/cas`) for
anonymous callers, and the UI's own search button is disabled until login. It is not an
anonymously accessible public source. Its search also requires a ≥3-character name prefix,
so bulk enumeration would be awkward even with credentials.

## Why CDPEHS

- It is what Mecklenburg County's own Environmental Health site (`eh.mecknc.gov/food`)
  links to for inspection scores.
- Mecklenburg data is county-filtered with `ESTTST_CTY=60`.
- No HTML-grid scraping needed for the core dataset: the inspections grid at
  `NCENVPBL/ESTABLISHMENT/ShowESTABLISHMENTTablePage.aspx?ESTTST_CTY=60` has a **CSV export**
  (`ctl00$PageContent$CSVButton1` postback) that honors the date-range filter fields.
  - Full-history export times out (HTTP 504) server-side; **monthly/quarterly chunks work**
    (June 2026 ≈ 190 KB in ~10 s).
  - CSV columns: Inspection Date, Grade, Final Score, Inspector ID, Premises Name,
    Premise Address 1/2, City, State, ZIP, State ID#, Establishment Type.
- **Violations** live at
  `NCENVPBL/INSPECTION_VIOLATION/ShowVIOLATIONTable.aspx?ESTABLISHMENT={eid}&INSPECTION={iid}&ESTTST_CTY=60`
  — a plain GET once the internal ids are known. The ids are obtained by firing the grid
  row's `ViolationDetails` postback (`__EVENTTARGET =
  ctl00$PageContent$VW_PUBLIC_ESTINSPTableControlRepeater$ctl{NN}$ViolationDetails`) and
  reading the 302 `Location` header. The violations page contains: violation item number,
  demerits, description, CDI / Repeat / VR flags, inspector comments, plus premises info
  and general inspection comments.

## Field-level notes

- **Grade comes from the source** — no need to derive from score thresholds (spec §3's
  caution noted; we store both score and the county-reported grade). Non-graded programs
  (hospitals, institutions) show grade `N/A`.
- **Not available publicly:** inspection type (routine vs. reinspection), permit status.
  Both are nullable in our schema; the CDP public view doesn't expose them.
- **No lat/lon in the source** — geocode at ingestion (Census Bureau batch geocoder).
- Facility identity: **State ID#** (e.g. `2060014945`). CDP's internal `ESTABLISHMENT` /
  `INSPECTION` ids are stored when learned (needed for violations + source links).
