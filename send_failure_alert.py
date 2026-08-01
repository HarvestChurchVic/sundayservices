#!/usr/bin/env python3
"""
Sends a failure alert email. Called by the GitHub Actions workflow whenever
any step fails, regardless of how far the pipeline got — even if it failed
before pipeline.py itself ever ran (e.g. a dependency install failure).

Reads context from environment variables set by the workflow, and includes
the tail of pipeline_debug.log if it exists, so whoever gets the email can
see roughly where things went wrong without needing to open GitHub.
"""
import json
import os
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path


def env(key, default=""):
    return os.environ.get(key, default)


def record_failure():
    history_path = Path("run_history.json")
    history = json.loads(history_path.read_text()) if history_path.exists() else []
    history.append({
        "status": "failure",
        "title": env("SERMON_TITLE", "unknown"),
        "sermon_date": env("SERMON_DATE", "unknown"),
        "speaker": env("SERMON_SPEAKER", "unknown"),
        "detail": None,
        "run_url": env("RUN_URL"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    history_path.write_text(json.dumps(history, indent=2))


def main():
    log_tail = ""
    log_path = Path("pipeline_debug.log")
    if log_path.exists():
        lines = log_path.read_text(errors="replace").splitlines()
        log_tail = "\n".join(lines[-25:])

    body = f"""A sermon processing run failed.

Title: {env('SERMON_TITLE', 'unknown')}
Speaker: {env('SERMON_SPEAKER', 'unknown')}
Sermon date: {env('SERMON_DATE', 'unknown')}

Full run log: {env('RUN_URL')}

--- Last lines of output before it failed ---
{log_tail if log_tail else '(no output captured — it likely failed before the pipeline itself started, e.g. during dependency installation)'}
"""

    msg = MIMEText(body)
    msg["Subject"] = f"FAILED: Sermon processing — {env('SERMON_TITLE', 'unknown')}"
    msg["From"] = env("EMAIL_FROM")
    msg["To"] = env("EMAIL_TO")

    with smtplib.SMTP(env("SMTP_HOST"), int(env("SMTP_PORT", "587"))) as server:
        server.starttls()
        server.login(env("SMTP_USERNAME"), env("SMTP_PASSWORD"))
        server.send_message(msg)

    print("Failure alert email sent.")
    record_failure()
    print("Failure recorded in run_history.json.")


if __name__ == "__main__":
    main()
