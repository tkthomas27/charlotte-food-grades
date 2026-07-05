"""Client for the CDPEHS public inspection system (Mecklenburg = ESTTST_CTY=60).

Data paths (see docs/DATA-SOURCE.md):
- Bulk facilities+inspections: the grid's CSV export, chunked by date range
  (full-history exports 504 server-side).
- Violations: plain GET on ShowVIOLATIONTable.aspx once the internal
  ESTABLISHMENT/INSPECTION ids are known; ids come from the 302 Location of a
  grid-row ViolationDetails postback.
"""

import csv
import io
import re
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

BASE = "https://public.cdpehs.com"
COUNTY_PARAM = "ESTTST_CTY=60"  # Mecklenburg
TABLE_URL = f"{BASE}/NCENVPBL/ESTABLISHMENT/ShowESTABLISHMENTTablePage.aspx?{COUNTY_PARAM}"
VIOL_URL = f"{BASE}/NCENVPBL/INSPECTION_VIOLATION/ShowVIOLATIONTable.aspx"
USER_AGENT = (
    "Mozilla/5.0 (compatible; charlotte-food-grades/1.0; "
    "+https://github.com/tkthomas27/charlotte-food-grades)"
)
REQUEST_PAUSE_S = 0.4
MIN_CHUNK_DAYS = 7


@dataclass
class GridRow:
    repeater_index: str  # literal index string from the control id, e.g. "00"
    inspection_date: date
    name: str
    address: str
    city: str
    state: str
    zip: str
    state_id: str
    facility_type: str
    score: float | None
    grade: str | None


@dataclass
class Violation:
    item: str
    demerits: float | None
    description: str
    cdi: bool
    repeat: bool
    vr: bool
    comments: str


@dataclass
class ViolationPage:
    general_comments: str
    violations: list[Violation] = field(default_factory=list)


class CdpehsClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def _get(self, url, **kw):
        resp = self.session.get(url, timeout=90, **kw)
        resp.raise_for_status()
        time.sleep(REQUEST_PAUSE_S)
        return resp

    def _post(self, url, data, allow_redirects=True, timeout=180):
        resp = self.session.post(
            url, data=data, allow_redirects=allow_redirects,
            headers={"Referer": url}, timeout=timeout,
        )
        if resp.status_code not in (200, 302):
            resp.raise_for_status()
        time.sleep(REQUEST_PAUSE_S)
        return resp

    @staticmethod
    def _form_state(html: str) -> dict:
        """Harvest hidden/text inputs and selected options into a postback dict."""
        state = {}
        for m in re.finditer(r'<input[^>]*type="(?:hidden|text)"[^>]*>', html):
            tag = m.group(0)
            name = re.search(r'name="([^"]+)"', tag)
            val = re.search(r'value="([^"]*)"', tag)
            if name:
                state[name.group(1)] = val.group(1) if val else ""
        for m in re.finditer(r'<select[^>]*name="([^"]+)"(.*?)</select>', html, re.S):
            sel = re.search(r'<option[^>]*selected[^>]*value="([^"]*)"', m.group(2))
            state[m.group(1)] = sel.group(1) if sel else ""
        return state

    def _fresh_state(self) -> dict:
        return self._form_state(self._get(TABLE_URL).text)

    # ---------------- CSV export ----------------

    def export_csv(self, date_from: date, date_to: date) -> list[dict]:
        """Export inspections in [date_from, date_to] via the grid's CSV button.

        The server 504s on windows with too many rows; we split recursively.
        """
        try:
            return self._export_csv_once(date_from, date_to)
        except (requests.HTTPError, requests.Timeout):
            span = (date_to - date_from).days
            if span < MIN_CHUNK_DAYS:
                raise
            mid = date_from + timedelta(days=span // 2)
            return self.export_csv(date_from, mid) + self.export_csv(
                mid + timedelta(days=1), date_to
            )

    def _export_csv_once(self, date_from: date, date_to: date) -> list[dict]:
        data = self._fresh_state()
        data["ctl00$PageContent$INSPECTION_DATEFromFilter"] = date_from.strftime("%m/%d/%Y")
        data["ctl00$PageContent$INSPECTION_DATEToFilter"] = date_to.strftime("%m/%d/%Y")
        data["ctl00$PageContent$CSVButton1.x"] = "10"
        data["ctl00$PageContent$CSVButton1.y"] = "10"
        resp = self._post(TABLE_URL, data)
        disposition = resp.headers.get("Content-Disposition", "")
        if "csv" not in disposition.lower():
            raise requests.HTTPError(f"expected CSV attachment, got {disposition!r}")
        text = resp.content.decode("utf-8-sig", errors="replace")
        rows = []
        for raw in csv.DictReader(io.StringIO(text)):
            row = {k.strip(): (v or "").strip() for k, v in raw.items() if k}
            if row.get("Inspection Date"):
                rows.append(row)
        return rows

    # ---------------- grid walk (for internal ids) ----------------

    def refresh_grid(self, date_from: date, date_to: date) -> str:
        data = self._fresh_state()
        data["ctl00$PageContent$INSPECTION_DATEFromFilter"] = date_from.strftime("%m/%d/%Y")
        data["ctl00$PageContent$INSPECTION_DATEToFilter"] = date_to.strftime("%m/%d/%Y")
        data["ctl00$PageContent$RefreshButton1.x"] = "10"
        data["ctl00$PageContent$RefreshButton1.y"] = "10"
        return self._post(TABLE_URL, data).text

    def _paginate(self, grid_html: str, button: str, extra: dict | None = None) -> str:
        """Fire a pagination postback against the current grid state.

        _NextPage etc. are image buttons (name.x/name.y); _PageSizeButton is a
        LinkButton fired through __EVENTTARGET.
        """
        data = self._form_state(grid_html)
        data.update(extra or {})
        if button == "_PageSizeButton":
            data["__EVENTTARGET"] = f"ctl00$PageContent$Pagination${button}"
            data["__EVENTARGUMENT"] = ""
        else:
            data[f"ctl00$PageContent$Pagination${button}.x"] = "5"
            data[f"ctl00$PageContent$Pagination${button}.y"] = "5"
        return self._post(TABLE_URL, data).text

    @staticmethod
    def _page_info(html: str) -> tuple[int, int]:
        cur = re.search(
            r'name="ctl00\$PageContent\$Pagination\$_CurrentPage"[^>]*value="(\d+)"', html)
        total = re.search(
            r'id="ctl00_PageContent_Pagination__TotalPages">(\d+)<', html)
        return (int(cur.group(1)) if cur else 1,
                int(total.group(1)) if total else 1)

    def walk_grid(self, date_from: date, date_to: date, page_size: int = 100):
        """Yield (page_html, rows) for every page of the filtered grid.

        Page size and page changes are stateful WebForms postbacks — the plain
        Refresh postback ignores the _PageSize/_CurrentPage form values.
        """
        html = self.refresh_grid(date_from, date_to)
        html = self._paginate(
            html, "_PageSizeButton",
            {"ctl00$PageContent$Pagination$_PageSize": str(page_size)},
        )
        while True:
            rows = self.parse_grid(html)
            yield html, rows
            cur, total = self._page_info(html)
            if cur >= total or not rows:
                break
            html = self._paginate(html, "_NextPage")

    @staticmethod
    def parse_grid(html: str) -> list[GridRow]:
        rows = []
        soup = BeautifulSoup(html, "html.parser")
        for td in soup.find_all("td", id=re.compile(r"Repeater_ctl(\d+)_ViolDtlRow$")):
            idx = re.search(r"Repeater_ctl(\d+)_ViolDtlRow$", td["id"]).group(1)
            cells = td.find_parent("tr").find_all("td")
            if len(cells) < 9:
                continue
            texts = [c.get_text(" ", strip=True) for c in cells]
            # cells: [viol link, date, name, address, state id, type, score, grade, inspector, pdf]
            addr_parts = [p.strip() for p in re.split(r"<br\s*/?>", cells[3].decode_contents())]
            addr_parts = [BeautifulSoup(p, "html.parser").get_text(" ", strip=True)
                          for p in addr_parts if p.strip()]
            city = state = zip_ = ""
            street = ", ".join(addr_parts)
            if addr_parts:
                m = re.match(r"(.+?),\s*([A-Z]{2})\s+(\d{5})", addr_parts[-1])
                if m:
                    city, state, zip_ = m.group(1).strip(), m.group(2), m.group(3)
                    street = ", ".join(addr_parts[:-1])
            score_txt = texts[6].replace(",", "")
            grade = texts[7].strip() or None
            rows.append(GridRow(
                repeater_index=idx,
                inspection_date=_parse_date(texts[1]),
                name=texts[2],
                address=street,
                city=city, state=state, zip=zip_,
                state_id=texts[4],
                facility_type=texts[5],
                score=float(score_txt) if _is_num(score_txt) else None,
                grade=None if grade in ("", "N/A") else grade,
            ))
        return rows

    def resolve_ids(self, grid_html: str, repeater_index: str) -> tuple[str, str] | None:
        """Fire the row's ViolationDetails postback; the 302 Location carries
        the internal ESTABLISHMENT and INSPECTION ids."""
        data = self._form_state(grid_html)
        data["__EVENTTARGET"] = (
            f"ctl00$PageContent$VW_PUBLIC_ESTINSPTableControlRepeater"
            f"$ctl{repeater_index}$ViolationDetails"
        )
        data["__EVENTARGUMENT"] = ""
        resp = self._post(TABLE_URL, data, allow_redirects=False, timeout=90)
        loc = resp.headers.get("Location", "")
        est = re.search(r"ESTABLISHMENT=(\d+)", loc)
        insp = re.search(r"INSPECTION=(\d+)", loc)
        if est and insp:
            return est.group(1), insp.group(1)
        return None

    # ---------------- violations ----------------

    @staticmethod
    def violation_url(establishment_id: str, inspection_id: str) -> str:
        return (f"{VIOL_URL}?ESTABLISHMENT={establishment_id}"
                f"&INSPECTION={inspection_id}&{COUNTY_PARAM}")

    def fetch_violations(self, establishment_id: str, inspection_id: str) -> ViolationPage:
        html = self._get(self.violation_url(establishment_id, inspection_id)).text
        return self.parse_violations(html)

    @staticmethod
    def parse_violations(html: str) -> ViolationPage:
        soup = BeautifulSoup(html, "html.parser")
        general = ""
        label = soup.find(string=re.compile(r"General Comments"))
        if label:
            cell = label.find_parent("td")
            if cell:
                nxt = cell.find_next_sibling("td")
                if nxt:
                    general = nxt.get_text(" ", strip=True)
        page = ViolationPage(general_comments=general)
        header = soup.find(string=re.compile(r"Violation Item"))
        if not header:
            return page
        table = header.find_parent("table")
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 7:
                continue
            item = tds[0].get_text(" ", strip=True)
            if not re.fullmatch(r"\d+", item):
                continue
            dem = tds[1].get_text(" ", strip=True).replace(",", "")
            page.violations.append(Violation(
                item=item,
                demerits=float(dem) if _is_num(dem) else None,
                description=tds[2].get_text(" ", strip=True),
                cdi=tds[3].get_text(strip=True).lower() == "yes",
                repeat=tds[4].get_text(strip=True).lower() == "yes",
                vr=tds[5].get_text(strip=True).lower() == "yes",
                comments=tds[6].get_text(" ", strip=True),
            ))
        return page


def _parse_date(text: str) -> date:
    m, d, y = text.strip().split("/")
    return date(int(y), int(m), int(d))


def _is_num(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False
