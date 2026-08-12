#!/bin/bash
# Azure App Service startup script
# Set this as your Startup Command in Azure Portal:
#   bash /home/site/wwwroot/startup.sh

cd /home/site/wwwroot
pip install -r requirements.txt --quiet
uvicorn app.backend.main:app --host 0.0.0.0 --port 8000
