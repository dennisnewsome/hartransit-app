from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parents[2]
APP_DIR  = BASE_DIR / "app"
FRONTEND_DIR = APP_DIR / "frontend"

# Azure App Service stores persistent data under /home by default
# Locally falls back to a data/ folder next to the project root
_azure_home = Path(os.environ.get("HOME", "")) / "data"
DATA_DIR = _azure_home if os.environ.get("WEBSITE_SITE_NAME") else BASE_DIR / "data"
DB_PATH  = DATA_DIR / "hartransit.db"

# Schedule zip: set SCHEDULE_ZIP_PATH env var in Azure App Service config
# Locally point at your Bus Schedules.zip
DEFAULT_SCHEDULE_ZIP = Path(
    os.environ.get(
        "SCHEDULE_ZIP_PATH",
        str(BASE_DIR / "Bus_Schedules.zip"),
    )
)

HARTRANSIT_BASE_URL = "https://www.hartransit.com"
PASSIO_SYSTEM_ID    = 2250
PASSIO_POLL_SECONDS = int(os.environ.get("PASSIO_POLL_SECONDS", "15"))

ROUTE_COLORS = {
    "1": "#E63946", "2": "#2196F3", "3":  "#009688",
    "4": "#FF9800", "5": "#9C27B0", "6":  "#4CAF50",
    "7": "#00BCD4", "8": "#795548", "9":  "#E91E63",
    "10": "#FF5722","17": "#3F51B5",
}
