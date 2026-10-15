import os
from pathlib import Path


APP_UID = 10001
APP_GID = 10001
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))

DATA_DIR.mkdir(parents=True, exist_ok=True)

if os.geteuid() == 0:
    os.chown(DATA_DIR, APP_UID, APP_GID)
    os.setgroups([])
    os.setgid(APP_GID)
    os.setuid(APP_UID)

port = os.environ.get("PORT", "8020")
command = [
    "uvicorn",
    "app.main:app",
    "--host",
    "0.0.0.0",
    "--port",
    port,
    "--workers",
    "1",
]
os.execvpe(command[0], command, os.environ)
