# Setup

Quick checklist for getting the bot running locally.

## 1. Clone and create a virtualenv

```bash
git clone https://github.com/<your-username>/data-analyst-bot.git
cd data-analyst-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Get the three required API keys

You need **your own** keys (don't share them with anyone, and don't commit
them to git):

1. **Telegram bot token** — talk to [@BotFather](https://t.me/BotFather) on
   Telegram, send `/newbot`, follow the prompts. Pick a username ending in
   `bot`. BotFather gives you a token like `123456:ABC...`.

2. **Groq API key** — sign up at [console.groq.com](https://console.groq.com/keys)
   (free tier is fine) and create a key. Put it in `GROQ_API_KEY`.

3. **Tavily API key** — sign up at [tavily.com](https://tavily.com) (free
   tier is fine, gives you 1000 searches/month) and create a key. Put it in
   `TAVILY_API_KEY`.

## 3. Create your `.env`

```bash
cp .env.example .env
```

Open `.env` in your editor and fill in:

- `TELEGRAM_BOT_TOKEN` — the BotFather token from step 2.1.
- `GROQ_API_KEY` — the Groq key from step 2.2.
- `TAVILY_API_KEY` — the Tavily key from step 2.3.

**Leave `PUBLIC_LOG_URL` as the placeholder** for now — you'll only know its
real value after deploying (see DEPLOYMENT section in README.md). The bot
will still run locally without it being correct.

**Do not commit `.env`.** It's already in `.gitignore`, but double-check
before every `git add` / `git commit`.

## 4. Run the bot

```bash
python3 app.py
```

You should see:

```
 * Running on http://0.0.0.0:8000
Starting long-polling loop...
```

Open Telegram, send your bot any message, and watch the logs. Each incoming
message + reply is appended as one JSON line to `logs/run.jsonl`.

## 5. Verify it works end-to-end

Send this to your bot (the worked example from the original project prompt):

> Which state has the highest maternal mortality rate based on MOSPI data?
> Reply with ONLY this JSON object and nothing else:
> `{"answer": {"state": "<state name>"}, "log_url": "<public wget-able URL to your agent's JSONL log>"}`

You should get back exactly one JSON object with the answer. Check
`logs/run.jsonl` — there should be a new line with the full transcript
(model text, tool calls, tool results).

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
  `logs/run.jsonl` for inspection.
- **Groq 429 (rate limit)** — happens occasionally on the free tier during
  bursts. `agent.py` retries once automatically. If persistent, lower
  `MAX_TOOL_ROUNDS` or upgrade Groq tier.