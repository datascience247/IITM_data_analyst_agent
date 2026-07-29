"""
agent.py — the "brain" of the bot.

Takes a short conversation (list of {"role": "user"/"assistant", "content": str})
where the LAST user message contains the actual data-analysis question (and the
exact JSON shape the grader wants), asks a Groq-hosted LLM (default
openai/gpt-oss-120b) to research + reason it out using Tavily for live web
lookups (MOSPI / data.gov.in / census / NFHS etc.), and returns a final
JSON-serialisable answer dict.

We deliberately do NOT trust the model to also fill in `log_url` correctly —
we splice that in ourselves after the fact, so a hallucinated or missing
log_url never breaks grading.
"""

import json
import os
import re
import time

from dotenv import load_dotenv

# Load .env from the current working directory so secrets don't have to be
# exported manually each shell session. Existing process env vars WIN
# (override=False) — this matters on deploy hosts like Render where secrets
# are injected as real env vars and .env doesn't exist.
load_dotenv(override=False)

from openai import OpenAI
from tavily import TavilyClient

# --- config -----------------------------------------------------------------

# Groq is OpenAI-compatible; point the SDK at their base URL.
GROQ_BASE_URL = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

# openai/gpt-oss-120b is OpenAI's open-weight reasoning model served on Groq.
# Other Groq-hosted options that work with this code: "llama-3.3-70b-versatile",
# "llama-3.1-8b-instant", "mixtral-8x7b-32768".
MODEL = os.environ.get("AGENT_MODEL", "openai/gpt-oss-120b")

# Reasoning models (gpt-oss-*) use max_completion_tokens, NOT max_tokens.
# Groq free-tier TPM for openai/gpt-oss-120b is 8000 — input tokens + this
# budget must fit. 4000 leaves headroom for system prompt + tool def +
# question text + a few rounds of tool results, while still giving the
# reasoning model enough room for its chain-of-thought + final answer.
MAX_COMPLETION_TOKENS = int(os.environ.get("MAX_COMPLETION_TOKENS", "4000"))
MAX_TOOL_ROUNDS = int(os.environ.get("MAX_TOOL_ROUNDS", "6"))  # safety cap on tool-use turns

TAVILY_API_KEY = os.environ["TAVILY_API_KEY"]
TAVILY_MAX_RESULTS = int(os.environ.get("TAVILY_MAX_RESULTS", "5"))

# --- system prompt ----------------------------------------------------------

SYSTEM_PROMPT = """You are a data analyst answering one graded question. Only the LAST user message in the conversation is the actual question.

Rules:
1. The question text specifies the exact JSON shape to reply with, e.g. `{"answer": {"state": "..."}, "log_url": "..."}`. Match keys, nesting, and value types (string vs number vs list vs object) exactly.
2. If the question references a public dataset (MOSPI, data.gov.in, census, NFHS, RBI) and you are not certain of the figures, call the tavily_search tool first. Prefer official .gov.in sources.
3. Your FINAL message must contain ONLY the JSON object — no markdown, no fences, no preamble, no trailing text. Must START with `{` and END with `}`.
4. For "log_url", output the literal string "PLACEHOLDER" — it gets replaced automatically.
5. Numbers: plain numbers unless the example shape shows strings.
"""

# --- tool definitions -------------------------------------------------------

TAVILY_TOOL = {
    "type": "function",
    "function": {
        "name": "tavily_search",
        "description": (
            "Search the live web for current data. Use this whenever the question "
            "references a public dataset (MOSPI, data.gov.in, census, NFHS, RBI, "
            "etc.) and you are not certain of the exact figures from memory. "
            "Prefer official .gov.in sources when possible."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query, e.g. 'highest maternal mortality rate state India MOSPI 2020'",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}


def _call_tavily(query: str) -> str:
    """Execute a Tavily search and return a stringified summary for the model."""
    client = TavilyClient(api_key=TAVILY_API_KEY)
    try:
        result = client.search(
            query=query,
            max_results=TAVILY_MAX_RESULTS,
            # include_answer gives Tavily's own synthesised short answer when it
            # can — useful for factoid-style questions.
            include_answer=True,
        )
    except Exception as e:
        return f"[tavily_search error: {e!r}]"

    parts = []
    if result.get("answer"):
        parts.append(f"Synthesised answer: {result['answer']}")
    for i, hit in enumerate(result.get("results", []), 1):
        title = hit.get("title", "")
        url = hit.get("url", "")
        content = hit.get("content", "")
        # Trim content so we don't blow the model's context window.
        if len(content) > 800:
            content = content[:800] + "…"
        parts.append(f"[{i}] {title}\n    {url}\n    {content}")
    return "\n\n".join(parts) if parts else "[no results]"


TOOL_DISPATCH = {
    "tavily_search": lambda args: _call_tavily(args.get("query", "")),
}


# --- JSON extraction (model-agnostic) ---------------------------------------

def _extract_json(text: str) -> dict:
    """Pull the first balanced {...} JSON object out of a string."""
    text = text.strip()
    # strip common code-fence wrapping
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # fall back: find first balanced brace group
    start = text.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in model output: {text!r}")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                return json.loads(candidate)
    raise ValueError(f"Unbalanced JSON in model output: {text!r}")


# --- main entry point -------------------------------------------------------

def answer_question(history: list[dict], real_log_url: str) -> dict:
    """
    history: [{"role": "user"|"assistant", "content": "..."}], last item is
             the question to answer.
    real_log_url: the actual public URL we want in the final reply.

    Returns a dict with keys:
      final_json   -> the exact dict to send back to Telegram
      raw_text     -> the model's raw final text (for logging)
      transcript   -> list of {type, summary} for logging tool use
    """
    client = OpenAI(api_key=GROQ_API_KEY, base_url=GROQ_BASE_URL)

    # Build the messages list. Groq/OpenAI doesn't take a separate `system=`
    # arg on chat.completions — system prompt is just the first message.
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend({"role": m["role"], "content": m["content"]} for m in history)

    transcript = []
    final_text = ""

    for round_num in range(MAX_TOOL_ROUNDS + 1):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=[TAVILY_TOOL],
                # Reasoning models (gpt-oss-*) need max_completion_tokens, not
                # max_tokens — the API rejects max_tokens on these models.
                max_completion_tokens=MAX_COMPLETION_TOKENS,
                # We don't want the model streaming for our use case.
            )
        except Exception as e:
            # Groq free tier occasionally 429s. Retry once after a short wait.
            err_str = str(e).lower()
            if "429" in err_str or "rate" in err_str or "try again" in err_str:
                time.sleep(2)
                response = client.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    tools=[TAVILY_TOOL],
                    max_completion_tokens=MAX_COMPLETION_TOKENS,
                )
            else:
                raise

        choice = response.choices[0]
        msg = choice.message

        # Record the assistant turn for the transcript log.
        if msg.content:
            transcript.append({"type": "text", "text": msg.content})
            final_text = msg.content  # last text we see is the final answer

        tool_calls = msg.tool_calls or []
        if not tool_calls or choice.finish_reason != "tool_calls":
            # Model is done — either end-of-turn or a non-tool finish reason.
            break

        # Model wants to call tool(s). Append its assistant turn, then append
        # one tool result message per call, then loop.
        # OpenAI requires the assistant turn to be re-sent exactly as returned
        # (including any tool_calls field) so the API can correlate tool_call_ids.
        messages.append(msg)

        for tc in tool_calls:
            fn_name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            transcript.append({"type": "tool_use", "name": fn_name, "input": args})

            handler = TOOL_DISPATCH.get(fn_name)
            if handler is None:
                tool_output = f"[unknown tool: {fn_name}]"
            else:
                try:
                    tool_output = handler(args)
                except Exception as e:
                    tool_output = f"[tool {fn_name} error: {e!r}]"

            transcript.append({"type": "tool_result", "summary": tool_output[:500]})

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_output,
                }
            )

        time.sleep(0.3)  # tiny courtesy pause

    raw_text = (final_text or "").strip()
    if not raw_text:
        # Shouldn't happen with a reasoning model, but guard anyway.
        raise ValueError("Model produced no text content")

    parsed = _extract_json(raw_text)
    if "answer" not in parsed:
        # model forgot the wrapper — wrap whatever it gave us
        parsed = {"answer": parsed}
    parsed["log_url"] = real_log_url

    return {
        "final_json": parsed,
        "raw_text": raw_text,
        "transcript": transcript,
    }
