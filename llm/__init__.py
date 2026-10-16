"""Chat model providers with tool calling and a fallback chain.

- ``llm.gemini``:        Google Gemini (google-genai SDK)
- ``llm.openai_compat``: any OpenAI-compatible API: Groq, OpenRouter, Ollama
- ``llm.router``:        builds the chain from LLM_CHAIN and falls back between providers

Every provider exposes ``run(history, prompt)``, a generator of events:
    {"type": "text", "text": ...}                        part of the answer
    {"type": "tool", "name": ..., "label": ..., "args": ...}   a tool is being called
"""
