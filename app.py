import logging
import os
import subprocess
import threading
import time

from flask import Flask, Response, send_file

import storage
from bot import run_polling

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("app")

app = Flask(__name__)

_started = False
_lock = threading.Lock()


# --- git auto-push for run.jsonl -------------------------------------------
#
# Pushes the run log to the configured remote on a timer so the raw
# GitHub URL stays current for the grader's wget. Only active when
# GIT_AUTO_PUSH=true. Uses GITHUB_TOKEN if set (deploy host with no SSH key);
# otherwise relies on whatever git auth the host already has.
#
# Behavior:
#   - Pushes every GIT_PUSH_INTERVAL_SECONDS (default 60).
#   - Skips the push if run.jsonl hasn't changed since last successful push
#     (tracked via a sidecar .last_push_size file).
#   - Logs errors but never crashes the bot on push failure.
#
# This is the Option A pattern from the project brief:
#   git add run.jsonl && git commit -m log && git push


_GIT_AUTO_PUSH = os.environ.get("GIT_AUTO_PUSH", "false").lower() in ("1", "true", "yes")
_PUSH_INTERVAL = int(os.environ.get("GIT_PUSH_INTERVAL_SECONDS", "60"))
_LAST_PUSH_SIZE_FILE = os.environ.get("LOG_PATH", "run.jsonl") + ".last_push_size"


def _push_log_once():
    """One auto-push attempt. Returns True on success, False otherwise."""
    log_path = storage.LOG_PATH
    if not os.path.exists(log_path):
        return False

    # Skip if file hasn't grown since last successful push
    current_size = os.path.getsize(log_path)
    last_pushed = 0
    if os.path.exists(_LAST_PUSH_SIZE_FILE):
        try:
            last_pushed = int(open(_LAST_PUSH_SIZE_FILE).read().strip() or "0")
        except (ValueError, OSError):
            last_pushed = 0
    if current_size <= last_pushed:
        return False

    # Configure git user if not already set (deploy hosts often lack this)
    subprocess.run(["git", "config", "user.email", "bot@data-analyst.local"],
                   check=False, capture_output=True)
    subprocess.run(["git", "config", "user.name", "data-analyst-bot"],
                   check=False, capture_output=True)

    # Stage and commit
    add = subprocess.run(["git", "add", log_path], capture_output=True, text=True)
    if add.returncode != 0:
        log.warning("git add failed: %s", add.stderr.strip())
        return False

    # If nothing to commit (file unchanged at git level), skip
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
    if diff.returncode == 0:
        return False

    commit = subprocess.run(
        ["git", "commit", "-m", "log: append run.jsonl"],
        capture_output=True, text=True,
    )
    if commit.returncode != 0:
        log.warning("git commit failed: %s", commit.stderr.strip())
        return False

    # Push; use GITHUB_TOKEN if available
    push_cmd = ["git", "push"]
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        # Rewrite remote URL to embed token (HTTPS-only; SSH deploys skip this)
        remote_url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, check=False,
        ).stdout.strip()
        if remote_url.startswith("https://"):
            authed = remote_url.replace(
                "https://", f"https://x-access-token:{token}@", 1
            )
            # set-url only for this push via GIT_DIR env would be complex; just set it
            subprocess.run(
                ["git", "remote", "set-url", "origin", authed],
                check=False, capture_output=True,
            )

    push = subprocess.run(push_cmd, capture_output=True, text=True, timeout=30)
    if push.returncode != 0:
        log.warning("git push failed: %s", push.stderr.strip())
        return False

    # Record success
    try:
        open(_LAST_PUSH_SIZE_FILE, "w").write(str(current_size))
    except OSError:
        pass
    log.info("auto-pushed run.jsonl (%d bytes)", current_size)
    return True


def _auto_push_loop():
    """Background thread that calls _push_log_once every interval."""
    log.info("git auto-push enabled (every %ds)", _PUSH_INTERVAL)
    while True:
        try:
            _push_log_once()
        except Exception as e:
            log.warning("auto-push loop error: %s", e)
        time.sleep(_PUSH_INTERVAL)


# --- bot + flask startup ----------------------------------------------------


def _start_bot_once():
    global _started
    with _lock:
        if not _started:
            t = threading.Thread(target=run_polling, daemon=True)
            t.start()
            _started = True
        if _GIT_AUTO_PUSH:
            # Start a second daemon thread for the push loop
            p = threading.Thread(target=_auto_push_loop, daemon=True)
            p.start()


@app.route("/")
def health():
    return {"status": "ok"}


@app.route("/run.jsonl")
def run_log():
    path = storage.LOG_PATH
    if not os.path.exists(path):
        return Response("", mimetype="text/plain")
    return send_file(path, mimetype="application/x-ndjson")


_start_bot_once()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)