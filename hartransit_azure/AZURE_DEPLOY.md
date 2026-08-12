# HARTransit — Azure App Service Deployment Guide

## What you're deploying

A single Python (FastAPI) web app that:
- Serves the tabbed single-page frontend at `/`
- Serves schedule data at `/api/data/*.txt`
- Polls Passio GO every 15 seconds in a background thread
- Exposes live bus data at `/api/live`
- Accepts rider feedback at `/api/feedback`
- Stores everything in SQLite (no separate database needed)

**Estimated cost: $0/month** on Azure App Service Free tier (F1)

---

## Step 1 — Create the App Service in Azure Portal

1. Go to https://portal.azure.com
2. Click **Create a resource** → search **Web App**
3. Fill in:
   - **Subscription**: your active subscription
   - **Resource Group**: create new → `hartransit-rg`
   - **Name**: `hartransit-app` (must be globally unique — try `hartransit-danbury`)
   - **Publish**: Code
   - **Runtime stack**: Python 3.11
   - **Operating System**: Linux
   - **Region**: East US (or closest to you)
   - **Pricing plan**: Free F1 ← important for $0 cost
4. Click **Review + Create** → **Create**
5. Wait ~2 minutes for deployment

---

## Step 2 — Deploy the code

### Option A: GitHub Actions (recommended for future updates)

1. Push this folder to a GitHub repo
2. In Azure Portal → your App Service → **Deployment Center**
3. Source: GitHub → authorize → select your repo and branch
4. Azure auto-generates a workflow file and deploys on every push

### Option B: ZIP deploy (quickest for first deploy)

1. Zip the contents of this folder (not the folder itself — zip the files inside)
2. In Azure Portal → your App Service → **Advanced Tools** → Go → Kudu console
3. Or use Azure CLI:
   ```bash
   az webapp deploy --resource-group hartransit-rg \
     --name hartransit-danbury \
     --src-path hartransit_azure.zip \
     --type zip
   ```

---

## Step 3 — Set the startup command

In Azure Portal → your App Service → **Configuration** → **General settings**:

**Startup Command:**
```
uvicorn app.backend.main:app --host 0.0.0.0 --port 8000
```

Click **Save** → the app will restart.

---

## Step 4 — Verify it's running

1. Go to `https://hartransit-danbury.azurewebsites.net/api/health`
2. You should see JSON like:
   ```json
   {"ok": true, "routes": 11, "trips": 528, "live_ready": true}
   ```
3. Go to `https://hartransit-danbury.azurewebsites.net/`
4. The tabbed app should load with all 6 tabs

---

## Step 5 — Optional: Custom domain

In Azure Portal → your App Service → **Custom domains**:
- Add a domain you own (e.g. `transit.hartransit.com`)
- Azure provides a free TLS certificate automatically

---

## Environment variables (optional tuning)

Set in Azure Portal → **Configuration** → **Application settings**:

| Name | Default | Description |
|------|---------|-------------|
| `PASSIO_POLL_SECONDS` | `15` | How often to poll Passio GO (seconds) |
| `SCHEDULE_ZIP_PATH` | *(not set)* | Path to Bus_Schedules.zip for auto-import |

---

## File structure

```
hartransit_azure/
├── requirements.txt          ← Python dependencies
├── startup.sh                ← Azure startup script
├── AZURE_DEPLOY.md           ← This file
├── app/
│   ├── frontend/
│   │   └── index.html        ← Single-page tabbed app (all 6 tabs)
│   └── backend/
│       ├── main.py           ← FastAPI app
│       ├── config.py         ← Settings
│       ├── db.py             ← SQLite setup
│       └── services/
│           ├── passio_service.py    ← Background Passio poller
│           └── schedule_importer.py ← GTFS data importer
└── data/
    └── gtfs/                 ← Schedule txt files (routes, trips, stops, etc.)
        ├── routes.txt
        ├── trips.txt
        ├── stops.txt
        ├── stop_times.txt
        ├── calendar.txt
        ├── agency.txt
        ├── route_metadata.txt
        ├── stop_metadata.txt
        └── full_stop_inventory.txt
```

---

## Tabs in the app

| Tab | What it does |
|-----|-------------|
| 🧭 Ride Assist | Full Ride Assist with live tracking, alert logic, boarding prompts |
| 🗓 Schedule | Timetable for the selected route/direction |
| 📡 Live Buses | All active Passio GO vehicles with heading and route |
| 🗺 Planner | Origin→destination trip planner |
| 🖥 Monitor | Live system status — vehicle count, alerts, last update |
| 💬 Feedback | Rider feedback form (stored in `/data/feedback.json`) |

---

## Local testing before deploying

```bash
cd hartransit_azure
pip install -r requirements.txt
uvicorn app.backend.main:app --host 0.0.0.0 --port 8000 --reload
```

Then open http://localhost:8000

The app runs identically locally and on Azure — no environment changes needed.
