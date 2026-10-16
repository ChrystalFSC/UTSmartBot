"""Build the chat provider chain from .env and fall back between providers.

LLM_CHAIN lists providers to try in order, as provider:model pairs:

    LLM_CHAIN=gemini:gemini-3.5-flash,groq:openai/gpt-oss-120b,openrouter:openrouter/free,gemini:gemini-flash-lite-latest

Leave the model out (e.g. "groq") to use that provider's default. Entries whose
API key is missing are skipped. Without LLM_CHAIN the chain is GEMINI_MODEL
then GEMINI_FALLBACK_MODEL, as before.
"""
import json
import logging
import os
import time

from campus.tools import TOOL_SCHEMAS

from .base import error_status

log = logging.getLogger("llm")

PROVIDERS = {
    "gemini": {"key": "GEMINI_API_KEY", "model": "gemini-3.5-flash"},
    "groq": {"key": "GROQ_API_KEY", "model": "openai/gpt-oss-120b",
             "base_url": "https://api.groq.com/openai/v1"},
    "openrouter": {"key": "OPENROUTER_API_KEY", "model": "openrouter/free",
                   "base_url": "https://openrouter.ai/api/v1",
                   "headers": {"X-Title": "UTSmartBot"}},
    # Ollama runs models on your own machine; no key needed.
    "ollama": {"key": None, "model": "qwen3:4b", "base_url_env": "OLLAMA_BASE_URL",
               "base_url": "http://localhost:11434/v1", "timeout": 180},
}

# How long to skip a provider after an error, by HTTP status.
COOLDOWN_SECONDS = {
    402: 3600,  # out of credits
    429: 600,   # rate limit / daily quota
    401: 3600,  # bad API key
    403: 3600,
}
DEFAULT_COOLDOWN = 60  # overloaded (503), timeouts, Ollama not running, ...


class ModelUnavailable(Exception):
    """Every provider in the chain failed before answering."""


def parse_chain(spec):
    entries = []
    for item in (spec or "").split(","):
        item = item.strip()
        if not item:
            continue
        provider, _, model = item.partition(":")  # models can contain ":" (e.g. qwen3:4b)
        provider = provider.strip().lower()
        if provider not in PROVIDERS:
            raise SystemExit(f"Unknown provider {provider!r} in LLM_CHAIN. Use one of: {', '.join(PROVIDERS)}")
        entries.append((provider, model.strip() or PROVIDERS[provider]["model"]))
    return entries


def default_chain_spec():
    main = os.environ.get("GEMINI_MODEL", PROVIDERS["gemini"]["model"])
    fallback = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-flash-lite-latest")
    return f"gemini:{main},gemini:{fallback}"


def make_provider(provider, model, system, tools=TOOL_SCHEMAS):
    settings = PROVIDERS[provider]
    api_key = os.environ.get(settings["key"]) if settings["key"] else None
    if settings["key"] and not api_key:
        return None
    if provider == "gemini":
        from .gemini import GeminiProvider

        return GeminiProvider(model, api_key, system, tools)
    from .openai_compat import OpenAICompatProvider

    base_url = os.environ.get(settings.get("base_url_env", ""), settings["base_url"])
    return OpenAICompatProvider(provider, model, base_url, api_key, system, tools,
                                headers=settings.get("headers"), timeout=settings.get("timeout", 60))


def build_router(system, spec=None):
    spec = spec or os.environ.get("LLM_CHAIN") or default_chain_spec()
    providers, skipped = [], []
    for provider, model in parse_chain(spec):
        instance = make_provider(provider, model, system)
        if instance is None:
            skipped.append(f"{provider}:{model} (no {PROVIDERS[provider]['key']})")
        else:
            providers.append(instance)
    if skipped:
        log.warning("Skipping chat providers without an API key: %s", ", ".join(skipped))
    if not providers:
        raise SystemExit("No usable chat provider. Set LLM_CHAIN and the matching API key in .env "
                         "(e.g. GEMINI_API_KEY, GROQ_API_KEY or OPENROUTER_API_KEY), or run Ollama.")
    log.info("Chat chain: %s", " -> ".join(f"{p.provider}:{p.model}" for p in providers))
    return ChatRouter(providers)


class ChatRouter:
    def __init__(self, providers):
        self.providers = providers
        self.cooldown_until = {}  # provider index -> time before which it is skipped

    def available(self):
        now = time.time()
        ready = [p for i, p in enumerate(self.providers) if self.cooldown_until.get(i, 0) <= now]
        return ready or self.providers  # if everything is cooling down, try anyway

    def answer_events(self, history, prompt):
        """Stream an answer from the first provider that works.

        Falls back to the next provider on any error before answer text has been
        sent, including a failure after a tool call (e.g. a quota error on the
        second request of a tool round). Yields a {"type": "meta"} event naming
        the provider that is answering.
        """
        sent_text = False
        seen_tools = set()
        ready = self.available()
        for provider in ready:
            started = False
            try:
                for event in provider.run(history, prompt, last_resort=provider is ready[-1]):
                    if not started:
                        started = True
                        yield {"type": "meta", "provider": provider.provider, "model": provider.model}
                    if event["type"] == "tool":
                        # A provider taking over repeats the tool call; don't show it twice.
                        key = (event["name"], json.dumps(event["args"], sort_keys=True))
                        if key in seen_tools:
                            continue
                        seen_tools.add(key)
                    elif event["type"] == "text":
                        sent_text = True
                    yield event
            except Exception as error:
                self.cool_down(provider, error)
                if sent_text:
                    raise  # part of an answer is already on screen; let the app report it
                continue
            if sent_text:
                return
            log.warning("%s:%s returned an empty answer", provider.provider, provider.model)
        raise ModelUnavailable()

    def cool_down(self, provider, error):
        status = error_status(error)
        seconds = COOLDOWN_SECONDS.get(status, DEFAULT_COOLDOWN)
        self.cooldown_until[self.providers.index(provider)] = time.time() + seconds
        log.warning("%s:%s failed (%s), skipping it for %ds: %s",
                    provider.provider, provider.model, status or type(error).__name__, seconds, str(error)[:160])
