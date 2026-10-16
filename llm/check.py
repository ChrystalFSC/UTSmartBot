"""Check that each chat provider in the chain works, including tool calling.

Usage:
    python -m llm.check                          # the chain from LLM_CHAIN in .env
    python -m llm.check groq openrouter:openrouter/free   # specific entries

Each provider gets one question that needs the staff lookup tool (two API
requests). Free model lists change often, so run this after changing LLM_CHAIN
or when a provider starts failing.
"""
import logging
import os
import sys
import time

from dotenv import load_dotenv

from .router import PROVIDERS, default_chain_spec, make_provider, parse_chain

SYSTEM = "You are a test assistant for a university chatbot. Use the tools for staff questions. Answer briefly."
QUESTION = "What is the phone number of the IT manager?"


def check(provider, model):
    instance = make_provider(provider, model, SYSTEM)
    if instance is None:
        return f"SKIPPED  no {PROVIDERS[provider]['key']} in .env"
    started = time.time()
    tools, text = [], []
    try:
        for event in instance.run([], QUESTION):
            if event["type"] == "tool":
                tools.append(event["name"])
            elif event["type"] == "text":
                text.append(event["text"])
    except Exception as error:
        return f"FAILED   {type(error).__name__}: {str(error)[:160]}"
    answer = "".join(text).strip().replace("\n", " ")
    seconds = time.time() - started
    if not tools:
        return f"PARTIAL  answered without calling a tool ({seconds:.1f}s): {answer[:80]}"
    if "348" not in answer:
        return f"PARTIAL  called {', '.join(tools)} but the answer lacks the number ({seconds:.1f}s): {answer[:80]}"
    return f"OK       called {', '.join(tools)} and answered correctly ({seconds:.1f}s)"


def main():
    logging.basicConfig(level=logging.ERROR)
    load_dotenv()
    spec = ",".join(sys.argv[1:]) or os.environ.get("LLM_CHAIN") or default_chain_spec()
    for provider, model in parse_chain(spec):
        print(f"{provider}:{model}".ljust(45), check(provider, model), flush=True)


if __name__ == "__main__":
    main()
