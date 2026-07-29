"""
bot.py — minimal Telegram long-polling loop (no webhook needed).

Long polling means: no public HTTPS callback URL required for Telegram
itself; the process just needs to stay running. We still run a tiny Flask
server (see app.py) alongside it so the host's health checks pass and so we
can serve /run.jsonl publicly.
"""

import logging
import os
import time
import traceback

import requests

import storage
from agent import answer_question

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot")

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API_BASE = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
LOG_URL = os.environ["PUBLIC_LOG_URL"]  # e.g. https://your-host.onrender.com/run.jsonl

POLL_TIMEOUT = 25


def send_message(chat_id: int, text: str) -> None:
    try:
        r = requests.post(
            f"{API_BASE}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=15,
        )
        r.raise_for_status()
    except Exception:
        log.error("sendMessage failed:\n%s", traceback.format_exc())


def handle_message(chat_id: int, text: str) -> None:
    storage.add_message(chat_id, "user", text)
    history = storage.get_history(chat_id)

    try:
        result = answer_question(history, LOG_URL)
        final_json = result["final_json"]
        import json as _json

        reply_text = _json.dumps(final_json, ensure_ascii=False)
    except Exception as e:
        log.error("agent failed:\n%s", traceback.format_exc())
        reply_text = _json_error(e)
        result = {"final_json": None, "raw_text": str(e), "transcript": []}

    storage.add_message(chat_id, "assistant", reply_text)
    storage.append_log(
        {
            "chat_id": chat_id,
            "incoming_message": text,
            "reply": reply_text,
            "raw_model_text": result.get("raw_text"),
            "transcript": result.get("transcript"),
        }
    )
    send_message(chat_id, reply_text)


def _json_error(e: Exception) -> str:
    import json as _json

    return _json.dumps({"answer": None, "error": str(e), "log_url": LOG_URL})


def run_polling() -> None:
    log.info("Starting long-polling loop...")
    offset = None
    while True:
        try:
            r = requests.get(
                f"{API_BASE}/getUpdates",
                params={"timeout": POLL_TIMEOUT, "offset": offset},
                timeout=POLL_TIMEOUT + 10,
            )
            r.raise_for_status()
            data = r.json()
        except Exception:
            log.error("getUpdates failed:\n%s", traceback.format_exc())
            time.sleep(3)
            continue

        for update in data.get("result", []):
            offset = update["update_id"] + 1
            msg = update.get("message") or update.get("edited_message")
            if not msg or "text" not in msg:
                continue
            chat_id = msg["chat"]["id"]
            text = msg["text"]
            log.info("chat %s -> %r", chat_id, text)
            try:
                handle_message(chat_id, text)
            except Exception:
                log.error("handle_message crashed:\n%s", traceback.format_exc())
