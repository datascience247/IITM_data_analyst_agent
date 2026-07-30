# Setup

Quick checklist for getting the bot running locally. There are two paths:
**fast** (use the bootstrap script) or **manual** (run each step yourself).

## Fast path: bootstrap script

If you've just cloned the repo:

```bash
./scripts/setup.sh
```

The script will:

1. Create a Python venv (uses `uv` if available, falls back to `python3 -m venv`)
2. Install dependencies from `requirements.txt`
3. Prompt you for the three required API keys (Telegram, Groq, Tavily)
4. Write them to a fresh `.env` (gitignored, mode 600)
5. Verify each key actually works (calls Telegram `getMe`, Groq `models.list`, Tavily `search`)
6. Print the resolved log URL so you know where the bot's replies will point

Re-running the script is safe — it won't overwrite an existing `.env`.

## Manual path

### 1. Clone and create a virtualenv

```bash
git clone https://github.com/<your-username>/data-analyst-bot.git
cd data-analyst-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Get the three required API keys

You need **your own** keys (don't share them with anyone, and don't commit
them to git):

1. **Telegram bot token** — talk to [@BotFather](https://t.me/BotFather) on
   Telegram, send `/newbot`, follow the prompts. Pick a username ending in
   `bot`. BotFather gives you a token like `123456:ABC...`.

2. **Groq API key** — sign up at [console.groq.com](https://console.groq.com/keys)
   (free tier is fine) and create a key.

3. **Tavily API key** — sign up at [tavily.com](https://tavily.com) (free
   tier is fine, gives you 1000 searches/month) and create a key.

### 3. Create your `.env`

```bash
cp .env.example .env
chmod 600 .env
```

Open `.env` in your editor and fill in:

- `TELEGRAM_BOT_TOKEN` — the BotFather token from step 2.1.
- `GROQ_API_KEY` — the Groq key from step 2.2.
- `TAVILY_API_KEY` — the Tavily key from step 2.3.

**You do NOT need to set `LOG_PUBLIC_URL` or `PUBLIC_LOG_URL`** — the bot
auto-derives the public log URL from `git remote get-url origin`. It will
work for any GitHub fork/clone without manual URL configuration.

Set `LOG_PUBLIC_URL` only if you want to override (e.g. you deployed to
Render and want the bot's replies to point at the Render URL instead of
GitHub's raw URL).

**Do not commit `.env`.** It's already in `.gitignore`, but double-check
before every `git add` / `git commit`.

### 4. Run the bot

```bash
python3 app.py
```

You should see:

```
 * Running on http://0.0.0.0:8000
Starting long-polling loop...
```

Open Telegram, send your bot any message, and watch the logs. Each incoming
message + reply is appended as one JSON line to `run.jsonl` (at the repo
root, NOT in a `logs/` subdirectory — that's intentional, so the file can
be committed and pushed to GitHub).

### 5. Verify it works end-to-end

Send this to your bot (the worked example from the original project prompt):

> Which state has the highest maternal mortality rate based on MOSPI data?
> Reply with ONLY this JSON object and nothing else:
> `{"answer": {"state": "<state name>"}, "log_url": "<public wget-able URL to your agent's JSONL log>"}`

You should get back exactly one JSON object with the answer. Check
`run.jsonl` — there should be a new line with the full transcript (model
text, tool calls, tool results).

## Deploying

For a hosted, always-on deployment, see [README.md](README.md) or just
use Render's Blueprint feature:

1. Fork or push this repo to your GitHub account.
2. Sign up at [render.com](https://render.com).
3. New → Blueprint → connect your repo → Render reads `render.yaml`.
4. In the Render dashboard, set the secret env vars:
   `TELEGRAM_BOT_TOKEN`, `GROQ_API_KEY`, `TAVILY_API_KEY`,
   optionally `GITHUB_TOKEN` for auto-push.
5. Confirm the `logs` disk is mounted at `/app/logs` (the `render.yaml`
   configures this).
6. Deploy. Your bot will be reachable at `https://<service-name>.onrender.com`.

The bot's `log_url` in every reply will auto-derive from your fork's
remote. If you want it to point at the Render host instead, set
`LOG_PUBLIC_URL=https://<service-name>.onrender.com/run.jsonl` in the
dashboard.

## Troubleshooting

- **"KeyError: 'GROQ_API_KEY'"** — you forgot to `source .venv/bin/activate`
  or didn't create `.env`. The bot reads env vars at startup.
- **Bot doesn't reply** — check that the polling loop started in the logs
  (you should see "Starting long-polling loop..."), and that your
  `TELEGRAM_BOT_TOKEN` is correct by hitting
  `https://api.telegram.org/bot<TOKEN>/getMe` in a browser.
- **Model output isn't valid JSON** — `agent.py` has a fallback extractor
  that pulls the first balanced `{...}` block. If that still fails, the bot
  replies with an error JSON and logs the raw model output to
  `run.jsonl` for inspection.
- **Groq 429 (rate limit)** — handled automatically: `agent.py` honors
  the `Retry-After` header and backs off exponentially with jitter (3
  retries). If persistent, lower `MAX_TOOL_ROUNDS` or upgrade to Groq's
  paid tier.
- **Log URL points to wrong repo** — the resolver reads `git remote
  get-url origin`. If you forked from someone else and want the log on
  your fork, push to your fork's remote first (`git remote set-url
  origin <your-fork-url>`).