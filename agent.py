"""
agent.py — the "brain" of the bot.

Takes a short conversation (list of {"role": "user"/"assistant", "content": str})
where the LAST user message contains the actual data-analysis question (and the
exact JSON shape the grader wants), asks Claude to research + reason it out
(with live web search for MOSPI / public-dataset lookups), and returns a
final JSON-serialisable answer dict.

We deliberately do NOT trust the model to also fill in `log_url` correctly —
we splice that in ourselves after the fact, so a hallucinated or missing
log_url never breaks grading.
"""

import json
import os
import re
import time

import anthropic

MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-4-6")  # override via env if needed
MAX_TOKENS = 4096
MAX_SEARCH_ROUNDS = 6  # safety cap on server-side tool-use turns

SYSTEM_PROMPT = """You are a meticulous data analyst answering a single graded question.

Rules:
1. The conversation you are given may contain several messages; only the LAST
   user message is the actual question to answer. Earlier messages (if any)
   are context/setup for that same task (multi-turn framing) — use them.
2. The question text itself specifies the exact JSON object you must reply
   with, e.g. `{"answer": {"state": "<state name>"}, "log_url": "..."}`.
   Read that shape carefully and match it exactly: same keys, same nesting,
   same value types (string vs number vs list vs object).
3. If the question references a public dataset (MOSPI, data.gov.in, census,
   NFHS, etc.) and you are not certain of the figures from memory, use the
   web_search tool to look them up before answering. Prefer official
   government sources (mospi.gov.in, data.gov.in, censusindia.gov.in,
   rbi.org.in) when available. Cross-check surprising numbers.
4. Show your work implicitly by searching/reasoning, but your FINAL message
   must contain ONLY the JSON object requested — no markdown code fences, no
   explanation, no preamble, no trailing text. Just the raw JSON object,
   valid and parseable.
5. For the "log_url" field, just put the literal string "PLACEHOLDER" — it
   will be replaced automatically after you answer. Do not spend effort on it.
6. If you are asked for a number, give a plain number (not a string) unless
   the example shape in the question shows it as a string. Match the
   requested shape precisely.
"""


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
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    messages = [{"role": m["role"], "content": m["content"]} for m in history]

    transcript = []
    response = None
    for round_num in range(MAX_SEARCH_ROUNDS):
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=messages,
        )

        for block in response.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                transcript.append({"type": "text", "text": block.text})
            elif btype == "server_tool_use":
                transcript.append(
                    {"type": "tool_use", "name": block.name, "input": block.input}
                )
            elif btype == "web_search_tool_result":
                transcript.append({"type": "tool_result", "summary": "web_search results returned"})

        if response.stop_reason != "pause_turn":
            # model is done (end_turn / max_tokens / etc.) — no more server-side
            # tool rounds needed
            break

        # pause_turn: server tool needs another round; feed the assistant
        # turn back in and let the API continue automatically.
        messages.append({"role": "assistant", "content": response.content})
        time.sleep(0.5)

    raw_text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()

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
