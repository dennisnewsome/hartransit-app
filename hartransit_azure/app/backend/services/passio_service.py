"""
passio_service.py
-----------------
Background thread that polls Passio GO every PASSIO_POLL_SECONDS seconds
and caches the result in memory.  The FastAPI app reads from this cache;
no second process needed.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..config import PASSIO_POLL_SECONDS, PASSIO_SYSTEM_ID, ROUTE_COLORS

log = logging.getLogger(__name__)

# ── In-memory cache ──────────────────────────────────────────────────────────

@dataclass
class LiveCache:
    vehicles:   list[dict] = field(default_factory=list)
    routes:     list[dict] = field(default_factory=list)
    stops:      list[dict] = field(default_factory=list)
    alerts:     list[dict] = field(default_factory=list)
    updated_at: str | None = None
    error:      str | None = None
    ready:      bool       = False

_cache = LiveCache()
_lock  = threading.Lock()


def get_live_snapshot() -> dict[str, Any]:
    with _lock:
        return {
            "vehicles":   list(_cache.vehicles),
            "routes":     list(_cache.routes),
            "stops":      list(_cache.stops),
            "alerts":     list(_cache.alerts),
            "updated_at": _cache.updated_at,
            "error":      _cache.error,
            "ready":      _cache.ready,
        }


# ── Coordinate sanity check (filter GPS glitches) ───────────────────────────

_REGION = {"lat_min": 41.0, "lat_max": 42.2, "lon_min": -73.8, "lon_max": -73.0}

def _valid_coord(lat: float | None, lon: float | None) -> bool:
    if lat is None or lon is None:
        return False
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    return (
        _REGION["lat_min"] <= la <= _REGION["lat_max"]
        and _REGION["lon_min"] <= lo <= _REGION["lon_max"]
    )


# ── Passio fetch ─────────────────────────────────────────────────────────────

def _fetch_and_update() -> None:
    try:
        import passiogo  # type: ignore

        system   = passiogo.getSystemFromID(PASSIO_SYSTEM_ID)
        raw_v    = system.getVehicles()  or []
        raw_r    = system.getRoutes()    or []
        raw_s    = system.getStops()     or []
        raw_a    = getattr(system, "getAlerts", lambda: [])() or []

        vehicles = []
        for v in raw_v:
            try:
                lat = getattr(v, "latitude",  None)
                lon = getattr(v, "longitude", None)
                if not _valid_coord(lat, lon):
                    continue
                rid = str(getattr(v, "routeId", "") or "")
                vehicles.append({
                    "id":        str(getattr(v, "id", "")),
                    "route_id":  rid,
                    "lat":       float(lat),
                    "lon":       float(lon),
                    "heading":   getattr(v, "heading",   None),
                    "color":     ROUTE_COLORS.get(rid, "#607D8B"),
                    "name":      getattr(v, "name",      None),
                })
            except Exception:
                pass

        routes = []
        for r in raw_r:
            try:
                rid = str(getattr(r, "id", "") or "")
                routes.append({
                    "id":    rid,
                    "name":  getattr(r, "name", rid),
                    "color": ROUTE_COLORS.get(rid, "#607D8B"),
                })
            except Exception:
                pass

        stops = []
        for s in raw_s:
            try:
                lat = getattr(s, "latitude",  None)
                lon = getattr(s, "longitude", None)
                stops.append({
                    "id":   str(getattr(s, "id",   "")),
                    "name": getattr(s, "name", ""),
                    "lat":  float(lat) if lat is not None else None,
                    "lon":  float(lon) if lon is not None else None,
                })
            except Exception:
                pass

        alerts = []
        for a in raw_a:
            try:
                alerts.append({
                    "id":       str(getattr(a, "id",      "")),
                    "route_id": str(getattr(a, "routeId", "") or ""),
                    "title":    getattr(a, "title",   ""),
                    "body":     getattr(a, "message", ""),
                })
            except Exception:
                pass

        now = datetime.now(timezone.utc).isoformat()
        with _lock:
            _cache.vehicles   = vehicles
            _cache.routes     = routes
            _cache.stops      = stops
            _cache.alerts     = alerts
            _cache.updated_at = now
            _cache.error      = None
            _cache.ready      = True

        log.info(
            "Passio poll OK — %d vehicles, %d routes, %d stops, %d alerts",
            len(vehicles), len(routes), len(stops), len(alerts),
        )

    except ImportError:
        with _lock:
            _cache.error = "passiogo library not installed"
            _cache.ready = False
        log.warning("passiogo not installed — live data unavailable")

    except Exception as exc:
        with _lock:
            _cache.error = str(exc)
        log.warning("Passio poll error: %s", exc)


# ── Background thread ─────────────────────────────────────────────────────────

_thread: threading.Thread | None = None


def start_passio_poller() -> None:
    """Call once at app startup."""
    global _thread

    def _loop():
        while True:
            _fetch_and_update()
            time.sleep(PASSIO_POLL_SECONDS)

    _thread = threading.Thread(target=_loop, daemon=True, name="passio-poller")
    _thread.start()
    log.info("Passio poller started (interval=%ds, system=%d)", PASSIO_POLL_SECONDS, PASSIO_SYSTEM_ID)
