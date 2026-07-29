# data-analyst-bot

A Telegram bot backed by a Claude agent. It receives a plain-text data-analysis
question (optionally as part of a short multi-turn thread), researches the
answer (web search enabled, useful for MOSPI / data.gov.in / census-style
lookups), and replies with **exactly one JSON object**:

```json
{"answer": <shaped as asked>, "log_url": "https://your-host/run.jsonl"}
```

Every run is appended as one line of JSON to `logs/run.jsonl`, which is
served publicly at `GET /run.jsonl` by the same process.

## How it works

- `bot.py` — long-polls the Telegram `getUpdates` API (no webhook/HTTPS
  callback config needed on Telegram's side).
- `agent.py` — calls the Claude API (`web_search` tool enabled) with the
  chat's message history, and forces the model's final output into the exact
  `{"answer": ..., "log_url": ...}` shape. `log_url` is always overwritten
  with your real public URL after the fact, so the model can never break that
  part.
- `storage.py` — tiny in-memory per-chat message history (so multi-turn
  threads work) + append-only JSONL logger.
- `app.py` — Flask app that starts the polling loop in a background thread
  and serves `/run.jsonl` (and `/` for health checks).

## 1. Run it locally first

```bash
git clone <your-repo-url>
cd data-analyst-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in real values, see below
export $(cat .env | xargs)
python3 app.py
```

Then message your bot on Telegram and watch the logs.

### Getting the two required secrets

1. **Telegram bot token** — open a chat with **@BotFather** on Telegram,
   send `/newbot`, follow the prompts. It gives you a token
   (`123456:ABC...`) and you pick the bot's username — **it must end in
   `bot`**, e.g. `navyaa_dataanalyst_bot`.
2. **Anthropic API key** — from the [Claude Console](https://console.claude.com)
   (Settings → API Keys). Put it in `ANTHROPIC_API_KEY`.

`PUBLIC_LOG_URL` should be the URL your log will be reachable at *after*
deployment (see step 3) — set it before your first graded run, since that's
literally what gets sent back in every answer.

## 2. Push to GitHub

```bash
git init
git add .
git commit -m "data-analyst telegram bot"
gh repo create data-analyst-bot --public --source=. --push
# or manually: create an empty public repo on github.com, then
# git remote add origin https://github.com/<you>/data-analyst-bot.git
# git push -u origin main
```

## 3. Deploy so it's reachable during grading

Any host that (a) runs a long-lived process and (b) gives you a public HTTPS
URL works. Two easy free-tier options:

### Option A — Render.com
1. New → Web Service → connect your GitHub repo.
2. Environment: Docker (it'll pick up the `Dockerfile` automatically).
3. Add environment variables: `TELEGRAM_BOT_TOKEN`, `ANTHROPIC_API_KEY`,
   `PUBLIC_LOG_URL` (use the `*.onrender.com` URL Render assigns you,
   `+ /run.jsonl`), optionally `AGENT_MODEL`.
4. **Enable a persistent disk** (Render dashboard → Disks) mounted at
   `/app/logs`, so your log survives restarts/redeploys. Without it the log
   still works, just resets on redeploy.
5. Deploy. Render will keep the service warm as long as you're on a paid
   instance type, or ping the free tier periodically (see note below).

### Option B — Railway.app / Fly.io
Same idea: point it at the Dockerfile, set the three env vars, add a volume
mounted at `/app/logs` for persistence, deploy.

> **Free-tier idle note:** some free tiers spin services down after
> inactivity. If your grading window may hit a cold instance, either use a
> paid "always on" tier, or add a cheap external uptime pinger (e.g.
> UptimeRobot hitting `/` every 5 minutes) to keep it warm. This doesn't
> affect Telegram delivery either way — long polling just resumes fetching
> updates whenever the process is up, so messages sent while you're asleep
> are still answered once you wake back up **as long as Telegram hasn't
> discarded them** (it holds them ~24h by default).

## 4. Test against the official grading harness

```bash
git clone https://github.com/Jivraj-18/tds-p1-t2-2026-telegram-bot
cd tds-p1-t2-2026-telegram-bot
# follow its README to point it at your bot username, add your own
# questions to evals/questions.json, and run it
```

## 5. Register

Submit, comma-separated:
- GitHub repo URL: `https://github.com/<you>/data-analyst-bot`
- Telegram bot username: `your_bot_username_bot`

## Notes / things you may want to tune

- **Model**: defaults to `claude-sonnet-4-6` via `AGENT_MODEL` env var. Swap
  to a cheaper/faster model if you're cost-constrained, or a stronger one for
  harder quantitative questions — check
  [docs.claude.com](https://docs.claude.com/en/docs/about-claude/models/overview)
  for current model IDs.
- **Multi-turn**: every incoming message is appended to that chat's history
  (last 20 messages kept) and the bot replies to *every* message using full
  context — matching "a short sequence of messages; answer the last one."
- **Robustness**: if the model's raw output isn't clean JSON, `agent.py`
  extracts the first balanced `{...}` block; if parsing still fails, the bot
  replies with an error JSON (logged) rather than crashing silently.
- **Log durability**: the log is a flat file on disk. Use a persistent disk/
  volume on your host, or extend `storage.append_log` to also push to
  S3/GCS/a Gist if you want it to survive full redeploys without a volume.
