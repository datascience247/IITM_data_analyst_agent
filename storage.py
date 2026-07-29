"""
storage.py — append-only JSONL run log + tiny in-memory per-chat history.

The log file is served directly by app.py at GET /run.jsonl, so it stays a
plain flat file on disk (works fine on a single always-on instance, which is
what this bot needs — see README for persistent-disk notes on your host).
"""

import json
import os
import threading
import time
from collections import defaultdict, deque

LOG_PATH = os.environ.get("LOG_PATH", "logs/run.jsonl")
_lock = threading.Lock()

# chat_id -> deque of {"role": ..., "content": ...}
_HISTORY: dict[int, deque] = defaultdict(lambda: deque(maxlen=20))


def append_log(entry: dict) -> None:
    entry = {"ts": time.time(), **entry}
    os.makedirs(os.path.dirname(LOG_PATH) or ".", exist_ok=True)
    with _lock:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def get_history(chat_id: int) -> list[dict]:
    return list(_HISTORY[chat_id])


def add_message(chat_id: int, role: str, content: str) -> None:
    _HISTORY[chat_id].append({"role": role, "content": content})


def reset_history(chat_id: int) -> None:
    _HISTORY[chat_id].clear()
