"""DocuMask-Local — single-process launcher.

Starts API server, background worker, and web UI in one process.
Just run: python app.py
Then open: http://localhost:8501

No Docker, no separate terminals, no install.ps1 needed.
Requirements must be installed (pip install -r requirements.txt).
"""
from __future__ import annotations

import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from documask.config import settings
from documask import jobs, license as lic

HOST = "127.0.0.1"
API_PORT = 8000
UI_PORT = 8501


def _run_api() -> None:
    """Start FastAPI in a daemon thread."""
    from documask.api import app
    uvicorn.run(app, host=HOST, port=API_PORT, log_level="warning")


def _run_worker() -> None:
    """Poll job queue in a daemon thread."""
    time.sleep(2)
    conn = jobs.connect()
    jobs.init_db(conn)
    jobs.requeue_stuck(conn)

    if not lic.check_license():
        info = lic.license_info()
        print(f"[WARN] No valid subscription. Reason: {info.get('reason')}")
        print(f"       HWID: {info['hwid']}")
        print(f"       Activate via UI or place license.key in app directory.")
    else:
        info = lic.license_info()
        print(f"[OK] Subscription active. Expires {info['expiry']} "
              f"({info['days_left']} days)")

    print("[worker] Ready.")
    POLL = 1.0
    while True:
        job = jobs.claim_next(conn)
        if job is None:
            time.sleep(POLL)
            continue
        print(f"[worker] Processing {job['id'][:8]}...")
        try:
            from documask.worker import process_one
            process_one(conn, job)
        except Exception as e:
            print(f"[worker] Error: {e}")
        print(f"[worker] Done {job['id'][:8]}")


def main() -> None:
    settings.ensure_dirs()
    print("=" * 50)
    print("  DocuMask-Local v0.1.0")
    print("=" * 50)
    print(f"  Work dir: {settings.work_dir}")
    print()

    key_path = Path("license.key")
    if key_path.exists():
        key_str = key_path.read_text().strip()
        lic.activate_key(key_str, save=False)
        info = lic.license_info()
        if info["valid"]:
            print(f"[OK] Subscription loaded from license.key")
            print(f"     Expires: {info['expiry']} ({info['days_left']} days)")
        else:
            print(f"[WARN] license.key found but invalid: {info.get('reason')}")
    else:
        print("[INFO] No license.key found. Activate via UI.")

    print()
    print(f"  Starting API on http://{HOST}:{API_PORT}")
    print(f"  Starting worker...")
    print(f"  Starting UI on http://{HOST}:{UI_PORT}")
    print()

    api_thread = threading.Thread(target=_run_api, daemon=True)
    worker_thread = threading.Thread(target=_run_worker, daemon=True)

    api_thread.start()
    worker_thread.start()

    time.sleep(3)

    print("-" * 50)
    print(f"  OPEN: http://{HOST}:{UI_PORT}")
    print(f"  API:  http://{HOST}:{API_PORT}/docs")
    print("-" * 50)

    try:
        import streamlit.web.cli as st_cli
        sys.argv = ["streamlit", "run", "documask/ui.py",
                    "--server.port", str(UI_PORT),
                    "--server.address", HOST,
                    "--server.headless", "true",
                    "--browser.serverAddress", HOST]
        st_cli.main()
    except ImportError:
        print("[WARN] Streamlit not installed. Opening API docs instead.")
        webbrowser.open(f"http://{HOST}:{API_PORT}/docs")
        while True:
            time.sleep(1)


if __name__ == "__main__":
    main()