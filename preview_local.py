"""
Launches a safe local preview of Weber's Tank Testing at http://127.0.0.1:8000
using a separate sandboxed database (_local_preview.db) so testing locally NEVER
touches live shop records or triggers live Google Sheets webhooks!
Usage: python preview_local.py
"""

import os
import shutil
import sqlite3
import subprocess
import sys
import webbrowser
from pathlib import Path
from threading import Timer

ROOT = Path(__file__).parent
PREVIEW_DB = ROOT / "_local_preview.db"
LIVE_LOCAL_DB = ROOT / "webers_hydro.db"

if not PREVIEW_DB.exists() and LIVE_LOCAL_DB.exists():
    shutil.copy2(LIVE_LOCAL_DB, PREVIEW_DB)

os.environ["WEBER_DB_PATH"] = str(PREVIEW_DB)
import database
database.DB_PATH = PREVIEW_DB
database.init_db()

# Disconnect live Sheets webhook inside the local sandbox DB so practice clicks stay 100% local
conn = sqlite3.connect(PREVIEW_DB)
conn.execute("UPDATE settings SET value='' WHERE key IN ('sheets_webhook_url', 'feedback_webhook_url', 'resend_api_key', 'smtp_app_password');")
pins = conn.execute("SELECT name, initials, role, pin FROM crew_users WHERE active=1 ORDER BY id;").fetchall()
conn.commit()
conn.close()

print("\n======================================================================")
print("  WEBER'S TANK TESTING — LOCAL SANDBOX PREVIEW (AUTO-RELOAD ON SAVE)")
print("======================================================================")
print(f"  Sandbox DB : {PREVIEW_DB.name} (100% isolated from live shop & Sheets)")
print("  Shop Ops   : http://127.0.0.1:8000/ops")
print("  Schedule   : http://127.0.0.1:8000/schedule")
print("  Office     : http://127.0.0.1:8000/office")
print("  Live TV    : http://127.0.0.1:8000/tv")
print("  Public Site: http://127.0.0.1:8000/?view=public")
print("----------------------------------------------------------------------")
print("  Local Login PINs:")
for name, init, role, pin in pins:
    print(f"    - {name:<22} ({init} • {role:<12}) -> PIN: {pin}")
print("  Press Ctrl + C in this window anytime to stop the local server.")
print("======================================================================\n")

Timer(1.4, lambda: webbrowser.open("http://127.0.0.1:8000/ops")).start()
subprocess.run([sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000", "--reload"], cwd=str(ROOT))
