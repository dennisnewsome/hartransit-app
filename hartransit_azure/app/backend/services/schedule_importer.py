from __future__ import annotations

import io
import re
import sqlite3
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET


TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
NS = {"table": TABLE_NS, "text": TEXT_NS}

ROUTE_FOLDER_RE = re.compile(r"(CityBus|LoopBus)_Route(?P<route_id>\d+)", re.IGNORECASE)
ODS_FILE_RE = re.compile(
    r"(?P<service_day>Weekday|Saturday|Sunday)_Route(?P<route_id>\d+)_(?P<direction>Inbound|Outbound)\.ods$",
    re.IGNORECASE,
)
TIME_TOKEN_RE = re.compile(
    r"^(?P<h>\d{1,2}):(?P<m>\d{2})(?::(?P<s>\d{2}))?\s*(?P<ampm>[AP]M)?$",
    re.IGNORECASE,
)


@dataclass
class RouteRecord:
    route_id: str
    route_type: str
    title: str
    folder_name: str
    pdf_name: str | None = None
    service_notes: str | None = None


def import_schedule_archive(conn: sqlite3.Connection, zip_path: Path) -> dict[str, int | str]:
    if not zip_path.exists():
        raise FileNotFoundError(f"Schedule zip not found: {zip_path}")

    routes: dict[str, RouteRecord] = {}
    trips_created = 0
    stop_times_created = 0

    with zipfile.ZipFile(zip_path) as archive:
        entries = archive.infolist()

        for info in entries:
            if info.is_dir():
                continue

            route = ensure_route_record(routes, info.filename)
            if route is None:
                continue

            if info.filename.lower().endswith(".pdf"):
                route.pdf_name = Path(info.filename).name
            elif info.filename.lower().endswith(".txt"):
                route.service_notes = read_archive_text(archive, info)

        clear_imported_data(conn)

        for route in routes.values():
            conn.execute(
                """
                INSERT INTO routes (id, route_type, title, folder_name, pdf_name, service_notes, source_zip_path)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    route.route_id,
                    route.route_type,
                    route.title,
                    route.folder_name,
                    route.pdf_name,
                    route.service_notes,
                    str(zip_path),
                ),
            )

        for info in entries:
            if info.is_dir() or not info.filename.lower().endswith(".ods"):
                continue

            match = ODS_FILE_RE.search(Path(info.filename).name)
            if not match:
                continue

            route_id = match.group("route_id")
            service_day = match.group("service_day").lower()
            direction = match.group("direction").lower()
            parsed = parse_ods_schedule(archive.read(info))

            for trip_index, stop_times in enumerate(parsed["trips"], start=1):
                cursor = conn.execute(
                    """
                    INSERT INTO trips (route_id, service_day, direction, trip_index, header_signature)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        route_id,
                        service_day,
                        direction,
                        trip_index,
                        " | ".join(parsed["stops"]),
                    ),
                )
                trip_id = cursor.lastrowid
                trips_created += 1

                for stop_sequence, item in enumerate(stop_times, start=1):
                    conn.execute(
                        """
                        INSERT INTO stop_times (
                            trip_id, stop_sequence, stop_name, departure_time, is_request_stop
                        )
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            trip_id,
                            stop_sequence,
                            item["stop_name"],
                            item["departure_time"],
                            1 if item["is_request_stop"] else 0,
                        ),
                    )
                    stop_times_created += 1

        imported_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO import_runs (source_path, imported_at, route_count, trip_count, stop_time_count, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(zip_path),
                imported_at,
                len(routes),
                trips_created,
                stop_times_created,
                "Initial schedule import from local zip archive",
            ),
        )
        conn.commit()

    return {
        "source_path": str(zip_path),
        "route_count": len(routes),
        "trip_count": trips_created,
        "stop_time_count": stop_times_created,
    }


def clear_imported_data(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM stop_times")
    conn.execute("DELETE FROM trips")
    conn.execute("DELETE FROM routes")


def ensure_route_record(routes: dict[str, RouteRecord], filename: str) -> RouteRecord | None:
    parts = Path(filename).parts
    folder_name = next((part for part in parts if "Route" in part), None)
    if not folder_name:
        return None

    match = ROUTE_FOLDER_RE.search(folder_name)
    if not match:
        return None

    route_id = match.group("route_id")
    route_type = "city" if match.group(1).lower() == "citybus" else "loop"

    if route_id not in routes:
        routes[route_id] = RouteRecord(
            route_id=route_id,
            route_type=route_type,
            title=f"Route {route_id}",
            folder_name=folder_name,
        )

    return routes[route_id]


def read_archive_text(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    raw = archive.read(info)
    try:
        return raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return raw.decode("latin-1").strip()


def parse_ods_schedule(ods_bytes: bytes) -> dict[str, list]:
    with zipfile.ZipFile(io.BytesIO(ods_bytes)) as ods:
        content_xml = ods.read("content.xml")

    root = ET.fromstring(content_xml)
    table = root.find(".//table:table", NS)
    if table is None:
        raise ValueError("ODS content missing table")

    rows = [trim_row(expand_row(row)) for row in table.findall("table:table-row", NS)]
    compact_rows = [row for row in rows if any(cell for cell in row)]
    if not compact_rows:
        return {"stops": [], "trips": []}

    header_index = find_header_row_index(compact_rows)
    stops = [normalize_stop_name(value) for value in compact_rows[header_index]]

    trips: list[list[dict[str, str | bool | None]]] = []
    for row in compact_rows[header_index + 1 :]:
        trip = normalize_trip_row(row, stops)
        if trip is None:
            if trips:
                break
            continue
        trips.append(trip)

    return {"stops": stops, "trips": trips}


def expand_row(row: ET.Element) -> list[str]:
    values: list[str] = []
    for cell in row.findall("table:table-cell", NS):
        repeat = int(cell.attrib.get(f"{{{TABLE_NS}}}number-columns-repeated", "1"))
        text = extract_cell_text(cell)
        values.extend([text] * repeat)
    return values


def trim_row(row: list[str]) -> list[str]:
    trimmed = list(row)
    while trimmed and not trimmed[-1]:
        trimmed.pop()
    return trimmed


def extract_cell_text(cell: ET.Element) -> str:
    values = []
    for paragraph in cell.findall(".//text:p", NS):
        text = "".join(paragraph.itertext()).strip()
        if text:
            values.append(text)
    return " ".join(values).strip()


def find_header_row_index(rows: list[list[str]]) -> int:
    best_index = 0
    best_score = -1

    for index, row in enumerate(rows):
        score = 0
        for value in row:
            lower = value.lower()
            if any(token in lower for token in ("depart", "arrive")):
                score += 3
            if not is_time_like(value) and value not in {"R", "--"}:
                score += 1
        if len(row) > 1 and score > best_score:
            best_score = score
            best_index = index

    return best_index


def normalize_trip_row(
    row: list[str], stops: list[str]
) -> list[dict[str, str | bool | None]] | None:
    values = row[: len(stops)]
    if len(values) < len(stops):
        values.extend([""] * (len(stops) - len(values)))

    parsed_count = sum(1 for value in values if is_time_like(value) or value in {"R", "--"})
    if parsed_count < max(2, len(stops) // 2):
        return None

    trip = []
    for stop_name, token in zip(stops, values):
        clean = token.strip()
        if clean == "R":
            trip.append(
                {
                    "stop_name": stop_name,
                    "departure_time": None,
                    "is_request_stop": True,
                }
            )
        elif clean == "--" or not clean:
            trip.append(
                {
                    "stop_name": stop_name,
                    "departure_time": None,
                    "is_request_stop": False,
                }
            )
        else:
            trip.append(
                {
                    "stop_name": stop_name,
                    "departure_time": normalize_time_token(clean),
                    "is_request_stop": False,
                }
            )

    return trip


def normalize_stop_name(value: str) -> str:
    return " ".join(value.replace("\n", " ").split())


def is_time_like(value: str) -> bool:
    return TIME_TOKEN_RE.match(value.strip()) is not None


def normalize_time_token(value: str) -> str | None:
    token = value.strip()
    match = TIME_TOKEN_RE.match(token)
    if not match:
        return None

    hour = int(match.group("h"))
    minute = int(match.group("m"))
    ampm = match.group("ampm")

    if ampm:
        ampm = ampm.upper()
        if ampm == "AM" and hour == 12:
            hour = 0
        elif ampm == "PM" and hour != 12:
            hour += 12

    return f"{hour:02d}:{minute:02d}"


def list_available_sources(zip_path: Path) -> dict[str, str]:
    return {
        "schedule_zip": str(zip_path),
        "hartransit_site": "https://www.hartransit.com",
        "passio_portal": "https://passiogo.com",
    }
