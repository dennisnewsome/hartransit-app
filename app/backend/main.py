"""
main.py — HARTransit Azure App Service backend
================================================
Single FastAPI process that:
  • serves the single-page frontend at /
  • exposes schedule data from SQLite at /api/...
  • polls Passio GO in a background thread and exposes live data at /api/live
  • auto-imports the schedule zip on first boot if no data exists

Deploy to Azure App Service (Python 3.11+):
  startup command:  uvicorn app.backend.main:app --host 0.0.0.0 --port 8000
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import DEFAULT_SCHEDULE_ZIP, FRONTEND_DIR
from .db import get_connection, initialize_db
from .services.passio_service import get_live_snapshot, start_passio_poller
from .services.schedule_importer import import_schedule_archive

app = FastAPI(title="HARTransit")


# ── Startup ──────────────────────────────────────────────────────────────────

@app.on_event("startup")
def startup() -> None:
    initialize_db()
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM routes").fetchone()[0]
        if count == 0 and DEFAULT_SCHEDULE_ZIP.exists():
            import_schedule_archive(conn, DEFAULT_SCHEDULE_ZIP)
    start_passio_poller()


# ── Health ───────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    with get_connection() as conn:
        routes = conn.execute("SELECT COUNT(*) FROM routes").fetchone()[0]
        trips  = conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0]
    live = get_live_snapshot()
    return {
        "ok":           True,
        "routes":       routes,
        "trips":        trips,
        "live_ready":   live["ready"],
        "live_updated": live["updated_at"],
    }


# ── Schedule import ───────────────────────────────────────────────────────────

@app.get("/api/import/status")
def import_status():
    with get_connection() as conn:
        run = conn.execute(
            "SELECT source_path, imported_at, route_count, trip_count, stop_time_count "
            "FROM import_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not run:
        return {"imported": False}
    return {"imported": True, "source_path": run[0], "imported_at": run[1],
            "route_count": run[2], "trip_count": run[3], "stop_time_count": run[4]}


@app.post("/api/import/reload")
def reload_import():
    with get_connection() as conn:
        return import_schedule_archive(conn, DEFAULT_SCHEDULE_ZIP)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/api/routes")
def list_routes():
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT r.id, r.route_type, r.title, r.service_notes,
                   COUNT(DISTINCT t.id) AS trip_count
            FROM routes r
            LEFT JOIN trips t ON t.route_id = r.id
            GROUP BY r.id, r.route_type, r.title, r.service_notes
            ORDER BY CAST(r.id AS INTEGER)
            """
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/routes/{route_id}")
def route_detail(route_id: str):
    with get_connection() as conn:
        route = conn.execute(
            "SELECT id, route_type, title, pdf_name, service_notes FROM routes WHERE id = ?",
            (route_id,),
        ).fetchone()
        if not route:
            raise HTTPException(404, "Route not found")
        combos = conn.execute(
            """
            SELECT service_day, direction, COUNT(*) AS trip_count
            FROM trips WHERE route_id = ?
            GROUP BY service_day, direction ORDER BY service_day, direction
            """,
            (route_id,),
        ).fetchall()
    return {**dict(route), "service_combinations": [dict(c) for c in combos]}


@app.get("/api/routes/{route_id}/trips")
def route_trips(route_id: str, service_day: str, direction: str):
    with get_connection() as conn:
        trips = conn.execute(
            """
            SELECT id, route_id, service_day, direction, trip_index
            FROM trips WHERE route_id = ? AND service_day = ? AND direction = ?
            ORDER BY trip_index
            """,
            (route_id, service_day.lower(), direction.lower()),
        ).fetchall()
        response = []
        for trip in trips:
            stop_times = conn.execute(
                """
                SELECT stop_sequence, stop_name, departure_time, is_request_stop
                FROM stop_times WHERE trip_id = ? ORDER BY stop_sequence
                """,
                (trip["id"],),
            ).fetchall()
            response.append({
                **dict(trip),
                "stop_times": [
                    {**dict(st), "is_request_stop": bool(st["is_request_stop"])}
                    for st in stop_times
                ],
            })
    return response


# ── Live data (Passio GO) ─────────────────────────────────────────────────────

@app.get("/api/live")
def live_all():
    """All live data in one call — matches the old localhost:5000/api/all shape."""
    return get_live_snapshot()


@app.get("/api/live/vehicles")
def live_vehicles():
    return get_live_snapshot()["vehicles"]


@app.get("/api/live/alerts")
def live_alerts():
    return get_live_snapshot()["alerts"]


# ── Feedback (simple file store, good enough for prototype) ──────────────────

import json
from pathlib import Path
from datetime import datetime, timezone
from fastapi import Body

FEEDBACK_FILE = Path(__file__).resolve().parents[2] / "data" / "feedback.json"

@app.post("/api/feedback")
def submit_feedback(payload: dict = Body(...)):
    FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if FEEDBACK_FILE.exists():
        try:
            existing = json.loads(FEEDBACK_FILE.read_text())
        except Exception:
            pass
    existing.append({**payload, "submitted_at": datetime.now(timezone.utc).isoformat()})
    FEEDBACK_FILE.write_text(json.dumps(existing, indent=2))
    return {"ok": True}


@app.get("/api/feedback")
def get_feedback():
    if not FEEDBACK_FILE.exists():
        return []
    try:
        return json.loads(FEEDBACK_FILE.read_text())
    except Exception:
        return []


# ── Frontend (serve SPA) ──────────────────────────────────────────────────────

@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")

# Serve static assets (JS, CSS, any other files in frontend/)
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# ── Static schedule txt files (for frontend loadData()) ─────────────────────
from fastapi.responses import PlainTextResponse

_DATA_FILES_DIR = Path(__file__).resolve().parents[2] / "data" / "gtfs"

@app.get("/api/data/{filename}")
def schedule_file(filename: str):
    allowed = {"routes.txt","trips.txt","stops.txt","stop_times.txt",
                "route_metadata.txt","stop_metadata.txt","full_stop_inventory.txt",
                "calendar.txt","agency.txt"}
    if filename not in allowed:
        raise HTTPException(404, "Not found")
    path = _DATA_FILES_DIR / filename
    if not path.exists():
        raise HTTPException(404, f"{filename} not yet generated")
    return PlainTextResponse(path.read_text())
