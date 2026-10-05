"""
Connect to the REAL Firebase Realtime Database (not the emulator) with a service-account key.

    pip install google-auth requests

The key file comes from Firebase console → Project settings → Service accounts → Generate new private key.
Keep it OUTSIDE the repo (e.g. C:\\dev\\secrets\\ai-home-service-account.json). It gives full admin access.
The Raspberry Pi uses exactly the same code.
"""
import json
import time
from pathlib import Path

from rest_db import RestDB

SCOPES = [
    "https://www.googleapis.com/auth/firebase.database",
    "https://www.googleapis.com/auth/userinfo.email",
]


def cloud_db(key_file, database_url=None):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError:
        raise SystemExit("Missing packages for the real database. Run:  pip install google-auth requests")

    key_path = Path(key_file).expanduser()
    if not key_path.exists():
        raise SystemExit(f"Service-account key not found: {key_path}")
    project_id = json.loads(key_path.read_text(encoding="utf-8"))["project_id"]
    creds = service_account.Credentials.from_service_account_file(str(key_path), scopes=SCOPES)
    state = {"exp": 0}

    def token():
        if time.time() > state["exp"]:          # refresh a few minutes before the 1 h expiry
            creds.refresh(Request())
            state["exp"] = time.time() + 50 * 60
        return creds.token

    url = database_url or f"https://{project_id}-default-rtdb.europe-west1.firebasedatabase.app"
    db = RestDB(base_url=url, namespace="", token=token)
    db.project_id = project_id
    return db
