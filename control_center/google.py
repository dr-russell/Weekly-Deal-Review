import json
import copy
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials

from . import store
from .config import ARTIFACTS, GOOGLE_ACCOUNT, GOOGLE_TOKEN, HEADERS, SCOPES, TARGETS
from .logic import amount_usd_m, capital_usd_m, normalized_name, normalized_usd_amount, sheet_amount, announced_usd_amount, normalize_round


def credentials():
    if not GOOGLE_ACCOUNT:
        raise RuntimeError('Google settings missing; configure control_center/google.local.json first')
    if not GOOGLE_TOKEN.exists():
        raise RuntimeError("Google write authorization missing; run python -m control_center.auth")
    creds = Credentials.from_authorized_user_file(str(GOOGLE_TOKEN))
    if creds.account.casefold() != GOOGLE_ACCOUNT:
        raise RuntimeError(f"Google account is not verified as {GOOGLE_ACCOUNT}; run control_center/.venv/bin/python -m control_center.auth --reauthorize")
    if not creds.has_scopes(SCOPES):
        raise RuntimeError("Google authorization lacks Sheets/Docs write scopes")
    if creds.expired:
        creds.refresh(Request())
        fd = os.open(GOOGLE_TOKEN, os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(creds.to_json())
    return creds


def request(session, method, url, **kwargs):
    response = session.request(method, url, timeout=(10, 45), **kwargs)
    try:
        data = response.json()
    except ValueError:
        raise RuntimeError(f"Google HTTP {response.status_code}: non-JSON response")
    if not response.ok:
        raise RuntimeError(f"Google HTTP {response.status_code}: {data.get('error', {}).get('message', 'request failed')}")
    return data


def u16(text):
    return len(text.encode("utf-16-le")) // 2


def sheet_date(value):
    if isinstance(value, (int, float)):
        return (date(1899, 12, 30) + timedelta(days=int(value))).isoformat()
    if re.fullmatch(r"\d{4}-\d\d-\d\d", str(value)):
        return str(value)
    return ""


def padded(row):
    return (list(row) + [""] * 14)[:14]


def amount_key(company, day):
    return normalized_name(company) + '|' + day


def stats_for(package, historical):
    end = date.fromisoformat(package["end"])
    table = []
    excluded = []
    for back in range(3, -1, -1):
        b = end - timedelta(days=7 * back)
        a = b - timedelta(days=6)
        if back == 0:
            count, amount = package["stats"]["count"], package["stats"]["capital_usd_m"]
        else:
            rows = [padded(r) for r in historical if len(r) > 13 and r[13] == "Yes" and a.isoformat() <= sheet_date(r[2]) <= b.isoformat()]
            count, amount = len(rows), 0
            for row in rows:
                original = package.get('historical_amount_sources', {}).get(amount_key(row[3],sheet_date(row[2])), str(row[6]))
                _, numeric, audit = announced_usd_amount(original, sheet_date(row[2]), package.get("fx_rates"))
                reason = audit['exclusion_reason']
                if numeric is not None:
                    amount += numeric
                else:
                    excluded.append({"company": row[3], "week": str(a), "reason": reason})
        table.append([f"{a.month}/{a.day} - {b.month}/{b.day}", count, round(amount, 4)])
    return table, excluded


def build_sheet(package, snapshot):
    if any(row[13] != "Yes" for row in package["rows"]):
        raise ValueError("Only final Yes rows may be published")
    tabs = {s["properties"]["title"]: s["properties"] for s in snapshot["meta"]["sheets"]}
    list_id, stats_id = tabs["List"]["sheetId"], tabs["Stats"]["sheetId"]
    old = snapshot["values"].get("values", [])
    if not old or padded(old[0]) != HEADERS:
        raise ValueError("Google List column headers do not match the expected A:N schema")
    remove = [i for i, row in enumerate(old[1:], 1) if len(row) > 2 and package["start"] <= sheet_date(row[2]) <= package["end"]]
    if any(len(row) > 13 and row[13] == "Maybe" for i, row in enumerate(old[1:], 1) if i not in remove):
        raise ValueError("Historical Maybe rows remain outside this week; do not silently publish an unresolved review sheet")
    requests = [{"clearBasicFilter": {"sheetId": list_id}}]
    # Delete bottom-up, then insert the immutable final week in one atomic Sheet batch.
    for i in reversed(remove):
        requests.append({"deleteDimension": {"range": {"sheetId": list_id, "dimension": "ROWS", "startIndex": i, "endIndex": i + 1}}})
    requests.append({"repeatCell": {"range": {"sheetId": list_id, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 1}, "cell": {"userEnteredValue": {"boolValue": False}}, "fields": "userEnteredValue"}})
    rows, n = package["rows"], len(package["rows"])
    if n:
        requests.extend([
            {"insertDimension": {"range": {"sheetId": list_id, "dimension": "ROWS", "startIndex": 1, "endIndex": n + 1}, "inheritFromBefore": False}},
            {"copyPaste": {"source": {"sheetId": list_id, "startRowIndex": n + 1, "endRowIndex": n + 2, "startColumnIndex": 0, "endColumnIndex": 14}, "destination": {"sheetId": list_id, "startRowIndex": 1, "endRowIndex": n + 1, "startColumnIndex": 0, "endColumnIndex": 14}, "pasteType": "PASTE_FORMAT"}},
        ])
        cell_rows = []
        for row in rows:
            cells = []
            for col, value in enumerate(row):
                if col == 6:
                    value = sheet_amount(str(value))
                entered = {"boolValue": bool(value)} if col == 0 else {"numberValue": (date.fromisoformat(value) - date(1899, 12, 30)).days} if col == 2 else {"stringValue": str(value)}
                cell = {"userEnteredValue": entered}
                if col in {4, 12}:
                    cell["userEnteredFormat"] = {"textFormat": {"link": {"uri": value}}} if value else {"textFormat": {}}
                cells.append(cell)
            cell_rows.append({"values": cells})
        requests.append({"updateCells": {"start": {"sheetId": list_id, "rowIndex": 1, "columnIndex": 0}, "rows": cell_rows, "fields": "userEnteredValue,userEnteredFormat.textFormat.link"}})
        for col, options in [(1, ["Early", "Growth"]), (13, ["Yes", "No"])]:
            requests.append({"setDataValidation": {"range": {"sheetId": list_id, "startRowIndex": 1, "endRowIndex": n + 1, "startColumnIndex": col, "endColumnIndex": col + 1}, "rule": {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": v} for v in options]}, "strict": True, "showCustomUi": True}}})
    # Sheets orders text and numeric dates separately. Give retained history the
    # same native date type before sorting the combined List.
    retained = [row for i, row in enumerate(old[1:], 1) if i not in remove]
    historical_normalization = []
    historical_rounds = []
    for i, row in enumerate(retained, n + 1):
        if len(row) > 3 and row[3]:
            serial = (date.fromisoformat(sheet_date(row[2])) - date(1899, 12, 30)).days
            requests.append({"updateCells": {"start": {"sheetId": list_id, "rowIndex": i, "columnIndex": 2}, "rows": [{"values": [{"userEnteredValue": {"numberValue": serial}}]}], "fields": "userEnteredValue"}})
            row = padded(row)
            standard_round = normalize_round(str(row[5]), str(row[1]))
            if standard_round != row[5]:
                historical_rounds.append({'company':row[3], 'original_round':row[5], 'normalized_round':standard_round, 'deal_date':sheet_date(row[2])})
                requests.append({"updateCells":{"start":{"sheetId":list_id,"rowIndex":i,"columnIndex":5},"rows":[{"values":[{"userEnteredValue":{"stringValue":standard_round}}]}],"fields":"userEnteredValue"}})
                row[5] = standard_round
            original = package.get('historical_amount_sources', {}).get(amount_key(row[3],sheet_date(row[2])), str(row[6]))
            display, _, audit = normalized_usd_amount(original, sheet_date(row[2]), package.get("fx_rates"))
            display = sheet_amount(display)
            audit['display_amount'] = display
            if display != row[6]:
                historical_normalization.append({"company":row[3], **audit})
                requests.append({"updateCells":{"start":{"sheetId":list_id,"rowIndex":i,"columnIndex":6},"rows":[{"values":[{"userEnteredValue":{"stringValue":display}}]}],"fields":"userEnteredValue"}})
            row[6] = display
            retained[i - n - 1] = row
    table, excluded = stats_for(package, retained)
    requests.append({"updateCells":{"start":{"sheetId":stats_id,"rowIndex":0,"columnIndex":2},"rows":[{"values":[{"userEnteredValue":{"stringValue":"Capital Announced"}}]}],"fields":"userEnteredValue"}})
    requests.append({"updateCells":{"start":{"sheetId":stats_id,"rowIndex":2,"columnIndex":4},"rows":[{"values":[{"userEnteredValue":{"stringValue":"Capital announced this week"}}]}],"fields":"userEnteredValue"}})
    for col, width in [(1,130),(2,170),(4,230),(5,100)]:
        requests.append({'updateDimensionProperties':{'range':{'sheetId':stats_id,'dimension':'COLUMNS','startIndex':col,'endIndex':col+1},'properties':{'pixelSize':width},'fields':'pixelSize'}})
    for tab in snapshot['meta']['sheets']:
        for chart in tab.get('charts', []):
            if chart.get('spec', {}).get('title', '').startswith('Capital Deployed'):
                spec = copy.deepcopy(chart['spec'])
                spec['title'] = 'Capital Announced by IC Week'
                for axis in spec.get('basicChart', {}).get('axis', []):
                    if axis.get('position') == 'LEFT_AXIS':
                        axis['title'] = 'Capital announced'
                requests.append({'updateChartSpec':{'chartId':chart['chartId'], 'spec':spec}})
    for row, col, values in [(1, 0, table), (1, 5, [[n], [package["stats"]["capital_usd_m"]]])]:
        requests.append({"updateCells": {"start": {"sheetId": stats_id, "rowIndex": row, "columnIndex": col}, "rows": [{"values": [{"userEnteredValue": {"numberValue": value} if isinstance(value, (int, float)) else {"stringValue": value}} for value in line]} for line in values], "fields": "userEnteredValue"}})
    for a, b, col in [(1, 5, 2), (2, 3, 5)]:
        requests.append({"repeatCell": {"range": {"sheetId": stats_id, "startRowIndex": a, "endRowIndex": b, "startColumnIndex": col, "endColumnIndex": col + 1}, "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": '$0"M"'}}}, "fields": "userEnteredFormat.numberFormat"}})
    total = len(old) - len(remove) + n
    requests.extend([
        {"updateDimensionProperties":{"range":{"sheetId":list_id,"dimension":"COLUMNS","startIndex":6,"endIndex":7},"properties":{"pixelSize":230},"fields":"pixelSize"}},
        {"repeatCell":{"range":{"sheetId":list_id,"startRowIndex":1,"endRowIndex":max(total,2),"startColumnIndex":6,"endColumnIndex":7},"cell":{"userEnteredFormat":{"wrapStrategy":"WRAP"}},"fields":"userEnteredFormat.wrapStrategy"}},
        {"repeatCell": {"range": {"sheetId": list_id, "startRowIndex": 1, "endRowIndex": max(total, 2), "startColumnIndex": 2, "endColumnIndex": 3}, "cell": {"userEnteredFormat": {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}}}, "fields": "userEnteredFormat.numberFormat"}},
        {"autoResizeDimensions":{"dimensions":{"sheetId":list_id,"dimension":"ROWS","startIndex":1,"endIndex":max(total,2)}}},
        {"sortRange": {"range": {"sheetId": list_id, "startRowIndex": 1, "endRowIndex": max(total, 2), "startColumnIndex": 0, "endColumnIndex": 14}, "sortSpecs": [{"dimensionIndex": 2, "sortOrder": "DESCENDING"}]}},
        {"setBasicFilter": {"filter": {"range": {"sheetId": list_id, "startRowIndex": 0, "endRowIndex": max(total, 2), "startColumnIndex": 0, "endColumnIndex": 14}}}},
    ])
    return requests, {"removed_previous_week_rows": len(remove), "written_rows": n, "stats": table, "historical_excluded_amounts": excluded, "historical_amount_normalization": historical_normalization, "historical_round_normalization":historical_rounds}


def paragraph_text(element):
    return "".join(r.get("textRun", {}).get("content", "") for r in element.get("paragraph", {}).get("elements", []))


def document_tab(doc):
    tabs = doc.get("tabs", [])
    for tab in tabs:
        props = tab.get("tabProperties", {})
        if props.get("tabId", tab.get("tabId")) == "t.0":
            return tab.get("documentTab", tab)
    raise ValueError("Expected roundup tab t.0 not found")


def blurb(row):
    spans = []
    text = ""
    def add(label, bold=False, url=None):
        nonlocal text
        if bold or url:
            spans.append((u16(text), u16(text) + u16(label), bold, url))
        text += label
    add(row[3], True, row[4] or None)
    add(f" is based in {row[10]}. {row[9].rstrip('.')}.")
    if row[6] == "Undisclosed":
        add(" It announced an undisclosed funding round")
    elif amount_usd_m(row[6])[0] is None:
        add(" Financing: ")
        add(row[6], True)
        if row[5]:
            add(f" ({row[5]})")
    else:
        add(" It raised ")
        amount = row[6][0].lower() + row[6][1:] if row[6].startswith(("More than", "At least", "Over")) else row[6]
        add(amount, True)
        if row[5] in {"Early", "Growth"}:
            add(" in an early-stage funding round" if row[5] == "Early" else " in a growth-stage funding round")
        else:
            article = "an" if row[5].lower().startswith(("equity", "extension", "initial")) else "a"
            add(f" in {article} {row[5]} round" if row[5] else " in a funding round")
    if row[7] != "Undisclosed":
        add(" backed by ")
        investors = [v.strip() for v in re.split(r";|,", row[7]) if v.strip()]
        for i, investor in enumerate(investors[:8]):
            if i:
                add(", ")
            add(investor, True)
        if len(investors) > 8:
            add(", and others")
    add(".")
    if row[12]:
        add(" ")
        add("Announcement", url=row[12])
    add("\n")
    return text, spans


def validate_blurb_styles(element, row):
    text, spans = blurb(row)
    if paragraph_text(element) != text:
        raise ValueError(f"Roundup blurb differs for {row[3]}")
    runs = []
    cursor = 0
    for part in element["paragraph"]["elements"]:
        tr = part.get("textRun", {})
        length = u16(tr.get("content", ""))
        runs.append((cursor, cursor + length, tr.get("textStyle", {})))
        cursor += length
    for first, last, bold, url in spans:
        overlapping = [style for a, b, style in runs if a < last and b > first]
        if not overlapping or any(bool(style.get("bold", False)) != bold for style in overlapping):
            raise ValueError(f"Roundup emphasis differs for {row[3]}")
        if url and any(style.get("link", {}).get("url") != url for style in overlapping):
            raise ValueError(f"Roundup hyperlink differs for {row[3]}")


def linked_charts(doc):
    return {key: item.get("inlineObjectProperties", {}).get("embeddedObject", {}).get("linkedContentReference", {}).get("sheetsChartReference")
            for key, item in document_tab(doc).get("inlineObjects", {}).items()
            if item.get("inlineObjectProperties", {}).get("embeddedObject", {}).get("linkedContentReference", {}).get("sheetsChartReference")}


def build_doc(package, doc):
    tab = document_tab(doc)
    content = tab["body"]["content"]
    headings = {paragraph_text(e).strip(): e for e in content if "paragraph" in e}
    early, growth, market = [headings.get(label) for label in ["Early Stage Deals:", "Growth Stage Deals:", "Market Stats:"]]
    if not all([early, growth, market]) or not early["endIndex"] <= growth["startIndex"] < market["startIndex"]:
        raise ValueError("Roundup section anchors missing or out of order")
    tables = [e for e in content if "table" in e]
    if len(tables) != 1 or tables[0]["startIndex"] <= market["startIndex"]:
        raise ValueError("Expected one preserved Market Stats table")
    protected = ["richLink", "person", "dateElement", "dropdown", "inlineObjectElement"]
    for element in content:
        if early["endIndex"] <= element.get("startIndex", 0) < market["startIndex"]:
            for run in element.get("paragraph", {}).get("elements", []):
                if any(key in run for key in protected):
                    raise ValueError("Protected native element in deal replacement range; stop before deletion")
    def rng(a, b):
        return {"tabId": "t.0", "startIndex": a, "endIndex": b}
    edits, groups = [], []
    for stage, a, b in [("Early", early["endIndex"], growth["startIndex"]), ("Growth", growth["endIndex"], market["startIndex"])]:
        lines = [blurb(row) for row in package["rows"] if row[1] == stage]
        if not lines:
            lines = [("N/A - no relevant deals this week\n", [])]
        text = "".join(line for line, _ in lines)
        groups.append({"a": a, "b": b, "text": text, "lines": lines})
        edits.append((a, b, text))
    table = tables[0]["table"]
    label = table['tableRows'][2]['tableCells'][0]
    paragraphs = [e for e in label['content'] if 'paragraph' in e]
    if len(paragraphs) != 1:
        raise ValueError('Stats capital label cell shape changed')
    p = paragraphs[0]
    edits.append((p['startIndex'], p['endIndex'] - 1, 'Capital Announced'))
    for ri, value in [(1, str(package["stats"]["count"])), (2, f"${package['stats']['capital_usd_m']:.0f}M")]:
        cell = table["tableRows"][ri]["tableCells"][1]
        paragraphs = [e for e in cell["content"] if "paragraph" in e]
        if len(paragraphs) != 1:
            raise ValueError("Stats value cell shape changed")
        p = paragraphs[0]
        edits.append((p["startIndex"], p["endIndex"] - 1, value))
    requests = []
    for a, b, text in sorted(edits, key=lambda x: x[0], reverse=True):
        if b > a:
            requests.append({"deleteContentRange": {"range": rng(a, b)}})
        requests.append({"insertText": {"location": {"tabId": "t.0", "index": a}, "text": text}})
    delta = 0
    for group in groups:
        a = group["a"] + delta
        b = a + u16(group["text"])
        requests.extend([
            {"updateParagraphStyle": {"range": rng(a, b), "paragraphStyle": {"namedStyleType": "NORMAL_TEXT", "keepWithNext": False, "keepLinesTogether": False}, "fields": "namedStyleType,keepWithNext,keepLinesTogether"}},
            {"createParagraphBullets": {"range": rng(a, b), "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"}},
            {"updateTextStyle": {"range": rng(a, b), "textStyle": {"fontSize": {"magnitude": 10, "unit": "PT"}, "bold": False}, "fields": "fontSize,bold,link"}},
        ])
        cursor = a
        for line, spans in group["lines"]:
            for first, last, bold, url in spans:
                style = {"bold": bold}
                if url:
                    style["link"] = {"url": url}
                requests.append({"updateTextStyle": {"range": rng(cursor + first, cursor + last), "textStyle": style, "fields": "bold,link" if url else "bold"}})
            cursor += u16(line)
        delta += u16(group["text"]) - (group["b"] - group["a"])
    return {"requests": requests, "writeControl": {"requiredRevisionId": doc["revisionId"]}}


class Publisher:
    def __init__(self, target):
        if target == "production" and os.environ.get("DEAL_ALLOW_PRODUCTION") != "1":
            raise RuntimeError("Production writes are disabled; verify demo copies before explicitly enabling production")
        self.target = TARGETS[target]
        if not all(self.target.get(key) for key in ['sheet', 'doc']):
            raise RuntimeError('Google output targets missing; configure control_center/google.local.json first')
        self.creds = credentials()
        self.sheet_url = "https://sheets.googleapis.com/v4/spreadsheets/" + self.target["sheet"]
        self.doc_url = "https://docs.googleapis.com/v1/documents/" + self.target["doc"]

    def sheet_snapshot(self):
        with AuthorizedSession(self.creds) as session:
            meta = request(session, "GET", self.sheet_url, params={"fields": "spreadsheetId,properties(title),sheets(properties,charts(chartId,spec))"})
            props = next(s["properties"] for s in meta["sheets"] if s["properties"]["title"] == "List")
            limit = props["gridProperties"]["rowCount"]
            values = request(session, "GET", self.sheet_url + f"/values/List!A1:N{limit}", params={"valueRenderOption": "UNFORMATTED_VALUE", "dateTimeRenderOption": "SERIAL_NUMBER"})
            return {"meta": meta, "values": values}

    def doc_snapshot(self):
        with AuthorizedSession(self.creds) as session:
            return request(session, "GET", self.doc_url, params={"includeTabsContent": "true"})

    def snapshot(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            sheet = pool.submit(self.sheet_snapshot)
            doc = pool.submit(self.doc_snapshot)
            return {**sheet.result(), "doc": doc.result()}

    def publish(self, package, snapshot, run_id):
        folder = ARTIFACTS / run_id / "publication"
        folder.mkdir(parents=True, exist_ok=True)
        # Persist the fresh native state before any write; all non-target sections survive.
        (folder / "before.json").write_text(json.dumps(snapshot))
        sources = {}
        for previous in store.query("SELECT metrics FROM runs WHERE kind='usd_refresh' AND status='completed' ORDER BY started_at"):
            metrics = json.loads(previous['metrics'])
            for audit in metrics.get('publication',{}).get('historical_amount_normalization',[]):
                sources.setdefault(amount_key(audit['company'],audit['deal_date']),audit['original_amount'])
        for deal in store.query("SELECT company,fields FROM deals WHERE active=1"):
            fields = json.loads(deal['fields'])
            sources[amount_key(deal['company'],fields['deal_date'])] = fields['amount']
        package['historical_amount_sources'] = sources
        sheet_requests, summary = build_sheet(package, snapshot)
        doc_requests = build_doc(package, snapshot["doc"])
        (folder / "requests.json").write_text(json.dumps({"sheet": sheet_requests, "doc": doc_requests}))
        outcomes = {}
        def write(name, url, body):
            started = time.perf_counter()
            with AuthorizedSession(self.creds) as session:
                result = request(session, "POST", url + ":batchUpdate", json=body)
            return {"acknowledged": bool(result.get("spreadsheetId") or result.get("documentId")), "seconds": time.perf_counter() - started}
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {"sheet": pool.submit(write, "sheet", self.sheet_url, {"requests": sheet_requests}), "doc": pool.submit(write, "doc", self.doc_url, doc_requests)}
            for name, future in futures.items():
                try:
                    outcomes[name] = future.result()
                except Exception as error:
                    outcomes[name] = {"acknowledged": False, "error": str(error)}
                (folder / "acknowledgments.json").write_text(json.dumps(outcomes))
        if not all(v["acknowledged"] for v in outcomes.values()):
            raise RuntimeError("Partial Google publication: " + json.dumps(outcomes) + ". Rerun safely replaces this week; inspect the audit before retrying.")
        return {**summary, "writes": outcomes, "sheet_id": self.target["sheet"], "doc_id": self.target["doc"], "linked_chart_refresh": "manual"}

    def verify(self, package, before=None):
        with ThreadPoolExecutor(max_workers=2) as pool:
            sheet_future = pool.submit(self.sheet_snapshot)
            doc_future = pool.submit(self.doc_snapshot)
            snapshot, doc = sheet_future.result(), doc_future.result()
        rows = snapshot["values"].get("values", [])[1:]
        populated = [padded(row) for row in rows if len(row) > 3 and row[3]]
        dates = [sheet_date(row[2]) for row in populated]
        if dates != sorted(dates, reverse=True):
            raise ValueError("Completed Sheet is not sorted by Deal Date descending")
        if any(row[13] == "Maybe" for row in populated):
            raise ValueError("Completed Sheet still contains a Maybe")
        current = [padded(row) for row in rows if row and row[0] in (True, "TRUE")]
        for row in current:
            row[2] = sheet_date(row[2])
        def key(row):
            return normalized_name(row[3]), sheet_date(row[2]), str(row[5]), sheet_amount(str(row[6]))
        if {key(row) for row in current} != {key(row) for row in package["rows"]} or len(current) != len(package["rows"]):
            raise ValueError("Published current-week Sheet rows do not match the immutable package")
        for actual in current:
            wanted = next(row for row in package["rows"] if key(row) == key(actual))
            displayed = list(wanted)
            displayed[6] = sheet_amount(str(displayed[6]))
            if actual[1:] != displayed[1:]:
                raise ValueError(f"Published Sheet fields differ for {wanted[3]}")
        with AuthorizedSession(self.creds) as session:
            stats = request(session, "GET", self.sheet_url + "/values:batchGet", params={"ranges": ["Stats!A2:C5", "Stats!F2:F3"], "valueRenderOption": "UNFORMATTED_VALUE"})
        tables = stats.get("valueRanges", [])
        expected_table, _ = stats_for(package, snapshot["values"].get("values", [])[1:])
        if len(tables) != 2 or tables[0].get("values") != expected_table or tables[1].get("values") != [[package["stats"]["count"]], [package["stats"]["capital_usd_m"]]]:
            raise ValueError("Published Sheet Stats differ from package")
        content = document_tab(doc)["body"]["content"]
        heading = {paragraph_text(e).strip(): e for e in content if "paragraph" in e}
        a, b = heading["Early Stage Deals:"]["endIndex"], heading["Market Stats:"]["startIndex"]
        deal_paragraphs = [e for e in content if a <= e.get("startIndex", 0) < b and e.get("paragraph", {}).get("bullet") is not None]
        expected_lines = [blurb(row)[0] for stage in ["Early", "Growth"] for row in package["rows"] if row[1] == stage]
        expected_lines += ["N/A - no relevant deals this week\n" for stage in ["Early", "Growth"] if not any(row[1] == stage for row in package["rows"])]
        if sorted(paragraph_text(e) for e in deal_paragraphs) != sorted(expected_lines):
            raise ValueError("Published roundup deal text differs from package")
        for element in deal_paragraphs:
            p = element["paragraph"]
            if p.get("paragraphStyle", {}).get("namedStyleType") != "NORMAL_TEXT" or p.get("paragraphStyle", {}).get("keepWithNext", False) or p.get("paragraphStyle", {}).get("keepLinesTogether", False):
                raise ValueError("Roundup deal paragraph inherited heading/pagination styling")
            for run in p["elements"]:
                tr = run.get("textRun", {})
                if tr.get("content", "").strip() and tr.get("textStyle", {}).get("fontSize", {}).get("magnitude") != 10:
                    raise ValueError("Roundup blurb font is not 10pt")
        for row in package["rows"]:
            element = next(e for e in deal_paragraphs if paragraph_text(e) == blurb(row)[0])
            validate_blurb_styles(element, row)
        if before and linked_charts(doc) != linked_charts(before["doc"]):
            raise ValueError("Roundup linked chart references changed during publication")
        if any(chart["spreadsheetId"] != self.target["sheet"] for chart in linked_charts(doc).values()):
            raise ValueError("Roundup charts reference a different Sheet; relink the demo charts before calling publication complete")
        table = next(e["table"] for e in content if "table" in e)
        for row, wanted in [(1, str(package["stats"]["count"])), (2, f"${package['stats']['capital_usd_m']:.0f}M")]:
            value = "".join(paragraph_text(e) for e in table["tableRows"][row]["tableCells"][1]["content"]).strip()
            if value != wanted:
                raise ValueError("Roundup stats table differs from package")
        return True
