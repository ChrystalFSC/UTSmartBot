"""Provider for OpenAI-compatible chat APIs: Groq, OpenRouter and Ollama.

They all accept the same /chat/completions request with "function" tools and
stream tool calls as deltas, so one implementation covers them.
"""
import json
import logging
import time

from openai import OpenAI

from .base import MAX_OUTPUT_TOKENS, MAX_TOOL_ROUNDS, STOPPED_TEXT, TEMPERATURE, call_tool, error_status

log = logging.getLogger("llm")

# Groq's free tier allows only ~7,000 input tokens per minute on some models, and a
# tool question needs two ~5,500-token requests. When there is no provider left to
# fall back to, wait for the rate limit instead of failing (at most this long).
MAX_RATE_LIMIT_WAIT = 65

# Models behind these APIs (gpt-oss, the free OpenRouter models) tend to answer from
# the reference passages instead of calling a tool. A reminder right after the
# question, where they pay most attention, works better than the system prompt alone.
TOOL_REMINDER = ("(Before answering: if this question is about fees or costs, a staff member or contact, "
                 "or which programmes to choose or list, call the matching tool first and answer from its result, "
                 "not from the passages.)")


class OpenAICompatProvider:
    def __init__(self, provider, model, base_url, api_key, system, tools, headers=None, timeout=60, client=None):
        self.provider = provider
        self.model = model
        self.system = system
        # max_retries=0: the router moves on to the next provider instead of waiting here.
        self.client = client or OpenAI(base_url=base_url, api_key=api_key or "none", timeout=timeout,
                                       max_retries=0, default_headers=headers)
        self.tools = [
            {"type": "function", "function": {"name": t["name"], "description": t["description"],
                                              "parameters": t["parameters"]}}
            for t in tools
        ]

    def run(self, history, prompt, last_resort=False):
        """last_resort: no other provider to fall back to, so wait out a short rate limit."""
        messages = [{"role": "system", "content": self.system}]
        messages += [{"role": "assistant" if t["role"] == "model" else "user", "content": t["text"]} for t in history]
        messages.append({"role": "user", "content": f"{prompt}\n\n{TOOL_REMINDER}" if self.tools else prompt})

        for round_ in range(MAX_TOOL_ROUNDS + 1):
            request = dict(model=self.model, messages=messages, tools=self.tools, stream=True,
                           temperature=TEMPERATURE, max_tokens=MAX_OUTPUT_TOKENS)
            if round_ == MAX_TOOL_ROUNDS:
                request["tool_choice"] = "none"  # last round: answer from the results gathered so far
            stream = self.create(last_resort, **request)
            text, calls = [], {}
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:  # reasoning models' hidden "reasoning" deltas are skipped
                    text.append(delta.content)
                    yield {"type": "text", "text": delta.content}
                # Tool calls arrive in pieces: the id and name first, the JSON arguments spread over chunks.
                for piece in delta.tool_calls or []:
                    call = calls.setdefault(piece.index, {"id": None, "name": "", "arguments": ""})
                    call["id"] = call["id"] or piece.id
                    if piece.function and piece.function.name:
                        call["name"] = call["name"] or piece.function.name
                    if piece.function and piece.function.arguments:
                        call["arguments"] += piece.function.arguments
            if not calls:
                return

            ordered = [calls[i] for i in sorted(calls)]
            for i, call in enumerate(ordered):
                call["id"] = call["id"] or f"call_{i}"
            messages.append({
                "role": "assistant",
                "content": "".join(text) or None,
                "tool_calls": [{"id": c["id"], "type": "function",
                                "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                               for c in ordered],
            })
            for call in ordered:
                event, result = call_tool(call["name"], parse_arguments(call["arguments"]))
                yield event
                messages.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": json.dumps(result, ensure_ascii=False)})
        yield {"type": "text", "text": STOPPED_TEXT}

    def create(self, last_resort, **request):
        try:
            return self.client.chat.completions.create(**request)
        except Exception as error:
            wait = retry_after(error)
            if not last_resort or wait is None or wait > MAX_RATE_LIMIT_WAIT:
                raise
            log.info("%s:%s is rate limited, waiting %.0fs", self.provider, self.model, wait)
            time.sleep(wait)
            return self.client.chat.completions.create(**request)


def retry_after(error):
    """Seconds a 429 response asks us to wait, or None."""
    if error_status(error) != 429:
        return None
    headers = getattr(getattr(error, "response", None), "headers", None) or {}
    try:
        return float(headers.get("retry-after"))
    except (TypeError, ValueError):
        return None


def parse_arguments(raw):
    try:
        args = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return args if isinstance(args, dict) else {}
