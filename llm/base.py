"""Pieces shared by all providers."""
import json
import logging

from campus.tools import TOOL_LABELS, run_tool

log = logging.getLogger("llm")

MAX_TOOL_ROUNDS = 3  # tool calls the model may chain before it must answer
TEMPERATURE = 0.3
MAX_OUTPUT_TOKENS = 2048
STOPPED_TEXT = "\n\n_Sorry, I couldn't finish looking that up. Please try rephrasing._"


def call_tool(name, args):
    """Run a tool and return (event for the UI, result for the model)."""
    name = (name or "").strip()  # flash-lite sometimes sends " estimate_fees"
    event = {"type": "tool", "name": name, "label": TOOL_LABELS.get(name, name), "args": args}
    result = run_tool(name, args)
    log.info("Tool %s(%s) -> %s", name, args, json.dumps(result)[:200])
    return event, result


def error_status(error):
    """HTTP status of an SDK error, if any (google-genai uses .code, openai uses .status_code)."""
    for attr in ("code", "status_code"):
        value = getattr(error, attr, None)
        if isinstance(value, int):
            return value
    return None
