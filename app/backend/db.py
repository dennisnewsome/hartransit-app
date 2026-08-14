import sqlite3
from contextlib import contextmanager

from .config import DATA_DIR, DB_PATH


SCHEMA = """
CREATE TABLE IF NOT EXISTS import_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    route_count INTEGER NOT NULL DEFAULT 0,
    trip_count INTEGER NOT NULL DEFAULT 0,
    stop_time_count INTEGER NOT NULL DEFAULT 0,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS routes (
    id TEXT PRIMARY KEY,
    route_type TEXT NOT NULL,
    title TEXT NOT NULL,
    folder_name TEXT,
    pdf_name TEXT,
    service_notes TEXT,
    source_zip_path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id TEXT NOT NULL,
    service_day TEXT NOT NULL,
    direction TEXT NOT NULL,
    trip_index INTEGER NOT NULL,
    header_signature TEXT,
    FOREIGN KEY(route_id) REFERENCES routes(id)
);

CREATE TABLE IF NOT EXISTS stop_times (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id INTEGER NOT NULL,
    stop_sequence INTEGER NOT NULL,
    stop_name TEXT NOT NULL,
    departure_time TEXT,
    is_request_stop INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(trip_id) REFERENCES trips(id)
);

CREATE INDEX IF NOT EXISTS idx_trips_route_service_direction
ON trips(route_id, service_day, direction, trip_index);

CREATE INDEX IF NOT EXISTS idx_stop_times_trip_sequence
ON stop_times(trip_id, stop_sequence);
"""


def initialize_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


@contextmanager
def get_connection():
    initialize_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
