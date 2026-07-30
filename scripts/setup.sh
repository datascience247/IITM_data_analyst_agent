#!/usr/bin/env bash
# scripts/setup.sh — bootstrap script for a fresh clone.
#
# Usage:
#   ./scripts/setup.sh
#
# What it does:
#   1. Creates a Python venv and installs requirements
#   2. Prompts for the three required API keys (Telegram, Groq, Tavily)
#   3. Writes them to a fresh .env (NEVER committed — gitignored)
#   4. Verifies the keys work (Telegram getMe, Groq models.list)
#   5. Prints the resolved log_url so you know where the bot will point
#
# Re-run safely — it won't clobber an existing .env without asking.

set -euo pipefail

# Find repo root (parent of this script's directory)
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> Setting up data-analyst-bot in $REPO_ROOT"
echo

# --- 1. venv + deps ---------------------------------------------------------

if [[ ! -d .venv ]]; then
    echo "==> Creating Python venv..."
    if command -v uv >/dev/null 2>&1; then
        uv venv .venv
    else
        python3 -m venv .venv
    fi
else
    echo "==> .venv already exists, skipping"
fi

echo "==> Installing dependencies..."
if command -v uv >/dev/null 2>&1; then
    uv pip install -r requirements.txt --python .venv/bin/python
else
    .venv/bin/pip install -r requirements.txt
fi
echo

# --- 2. .env ----------------------------------------------------------------

if [[ -f .env ]]; then
    echo "==> .env already exists. Re-running will NOT overwrite your secrets."
    echo "    Delete .env first if you want a clean setup."
    echo
    SKIP_ENV_PROMPTS=1
else
    SKIP_ENV_PROMPTS=0
    echo "==> Creating .env from .env.example template..."
    cp .env.example .env
    chmod 600 .env
fi

prompt_for_key() {
    local var_name="$1"
    local prompt_text="$2"
    local help_text="$3"
    local current_value

    current_value="$(grep -E "^${var_name}=" .env | cut -d= -f2- || true)"

    # Skip if already set to a non-placeholder value
    if [[ -n "$current_value" && "$current_value" != *"your-key"* && "$current_value" != *"replace-me"* ]]; then
        echo "    $var_name already set, skipping"
        return
    fi

    echo "$help_text"
    local new_value
    read -r -p "$prompt_text: " new_value
    if [[ -z "$new_value" ]]; then
        echo "    (skipped — you'll need to set $var_name in .env before running)"
        return
    fi

    # Replace the value in .env
    if grep -q "^${var_name}=" .env; then
        sed -i.bak "s|^${var_name}=.*|${var_name}=${new_value}|" .env
        rm -f .env.bak
    else
        echo "${var_name}=${new_value}" >> .env
    fi
}

if [[ "$SKIP_ENV_PROMPTS" -eq 0 ]]; then
    echo "==> Now let's fill in the three required API keys."
    echo "    (Press Enter to skip — you can edit .env manually later.)"
    echo

    prompt_for_key "TELEGRAM_BOT_TOKEN" \
        "Telegram bot token (from @BotFather, /newbot)" \
        "  Get one: message @BotFather on Telegram, send /newbot, follow prompts."

    prompt_for_key "GROQ_API_KEY" \
        "Groq API key" \
        "  Get one: https://console.groq.com/keys (sign up, free tier available)."

    prompt_for_key "TAVILY_API_KEY" \
        "Tavily API key" \
        "  Get one: https://tavily.com (sign up, free tier = 1000 searches/month)."

    echo
    echo "==> .env created. Permissions set to 600 (owner read/write only)."
    echo
fi

# --- 3. Verify keys ---------------------------------------------------------

echo "==> Verifying keys (any failures are non-fatal — the bot will still run)..."
.venv/bin/python3 - <<'PYEOF'
import os, sys
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.getcwd(), ".env"))

def check(name, ok, detail=""):
    status = "OK" if ok else "FAIL"
    print(f"    [{status}] {name}{(': ' + detail) if detail else ''}")

# Telegram
token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if token and not token.endswith("your-botfather-token"):
    try:
        import requests
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        data = r.json()
        if data.get("ok"):
            check("Telegram", True, f"@{data['result'].get('username')}")
        else:
            check("Telegram", False, data.get("description", "unknown error"))
    except Exception as e:
        check("Telegram", False, type(e).__name__)
else:
    check("Telegram", False, "TELEGRAM_BOT_TOKEN not set")

# Groq
groq_key = os.environ.get("GROQ_API_KEY", "")
if groq_key and not groq_key.endswith("your-key"):
    try:
        from openai import OpenAI
        client = OpenAI(api_key=groq_key, base_url="https://api.groq.com/openai/v1")
        models = client.models.list()
        ids = [m.id for m in models.data]
        check("Groq", True, f"{len(ids)} models available")
        if "openai/gpt-oss-120b" in ids:
            print("           - openai/gpt-oss-120b: available")
        else:
            print("           - openai/gpt-oss-120b: NOT AVAILABLE (check your account)")
    except Exception as e:
        check("Groq", False, str(e)[:100])
else:
    check("Groq", False, "GROQ_API_KEY not set")

# Tavily
tavily_key = os.environ.get("TAVILY_API_KEY", "")
if tavily_key and not tavily_key.endswith("your-key"):
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=tavily_key)
        result = client.search("test", max_results=1)
        check("Tavily", True, "API responding")
    except Exception as e:
        check("Tavily", False, str(e)[:100])
else:
    check("Tavily", False, "TAVILY_API_KEY not set")
PYEOF
echo

# --- 4. Show resolved log URL ----------------------------------------------

echo "==> Resolving the public log URL..."
.venv/bin/python3 - <<'PYEOF'
import os
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.getcwd(), ".env"))
os.environ.setdefault("GROQ_API_KEY", "dummy")
os.environ.setdefault("TAVILY_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
import sys
sys.path.insert(0, ".")
from app import resolve_public_log_url
print(f"    {resolve_public_log_url()}")
PYEOF
echo

echo "==> Done. To run the bot:"
echo "    source .venv/bin/activate"
echo "    python3 app.py"
echo
echo "    Or directly:"
echo "    .venv/bin/python3 app.py"
echo
echo "==> To deploy to Render:"
echo "    1. Push this repo to your GitHub account (fork or clone-push)."
echo "    2. Sign up at render.com -> New -> Blueprint."
echo "    3. Connect the repo, set TELEGRAM_BOT_TOKEN, GROQ_API_KEY,"
echo "       TAVILY_API_KEY (and optionally GITHUB_TOKEN) in the dashboard."
echo "    4. Deploy. The render.yaml in this repo configures everything else."
echo
echo "==> See SETUP.md and README.md for more details."