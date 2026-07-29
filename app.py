import os
import threading

from flask import Flask, Response, send_file

import storage
from bot import run_polling

app = Flask(__name__)

_started = False
_lock = threading.Lock()


def _start_bot_once():
    global _started
    with _lock:
        if not _started:
            t = threading.Thread(target=run_polling, daemon=True)
            t.start()
            _started = True


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
