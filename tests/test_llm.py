"""Tests for the chat providers and fallback chain, using fake API clients (no keys needed).

Run with:  python -m unittest discover tests
"""
import json
import os
import unittest
from types import SimpleNamespace as NS
from unittest import mock

from campus.tools import TOOL_SCHEMAS
from llm.base import MAX_TOOL_ROUNDS
from llm.openai_compat import TOOL_REMINDER, OpenAICompatProvider
from llm.router import ChatRouter, ModelUnavailable, build_router, parse_chain


def text_chunk(text):
    return NS(choices=[NS(delta=NS(content=text, tool_calls=None))])


def tool_chunk(index, id=None, name=None, arguments=None):
    piece = NS(index=index, id=id, function=NS(name=name, arguments=arguments))
    return NS(choices=[NS(delta=NS(content=None, tool_calls=[piece]))])


class FakeOpenAIClient:
    """Plays back one scripted stream per chat.completions.create call and records the requests."""

    def __init__(self, *streams):
        self.streams = list(streams)
        self.requests = []
        self.tool_choices = []
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs["messages"])))  # snapshot
        self.tool_choices.append(kwargs.get("tool_choice"))
        stream = self.streams.pop(0)
        if isinstance(stream, Exception):
            raise stream
        return iter(stream)


class OpenAICompatProviderTest(unittest.TestCase):
    def make(self, client):
        return OpenAICompatProvider("groq", "test-model", "http://unused", "key", "SYSTEM", TOOL_SCHEMAS, client=client)

    def test_plain_answer(self):
        client = FakeOpenAIClient([text_chunk("Hello "), text_chunk("there")])
        events = list(self.make(client).run([{"role": "model", "text": "earlier"}], "Hi"))
        self.assertEqual("".join(e["text"] for e in events), "Hello there")
        roles = [m["role"] for m in client.requests[0]]
        self.assertEqual(roles, ["system", "assistant", "user"])  # Gemini-style "model" becomes "assistant"

    def test_tool_call_streamed_in_pieces(self):
        client = FakeOpenAIClient(
            [  # round 1: the model calls lookup_staff, arguments split over chunks
                tool_chunk(0, id="call_1", name="lookup_staff"),
                tool_chunk(0, arguments='{"query": "IT '),
                tool_chunk(0, arguments='manager"}'),
            ],
            [text_chunk("The IT manager's number is 084-367348.")],  # round 2: the answer
        )
        events = list(self.make(client).run([], "Who is the IT manager?"))

        self.assertEqual(events[0]["type"], "tool")
        self.assertEqual(events[0]["args"], {"query": "IT manager"})
        self.assertEqual(events[0]["label"], "Staff directory")
        self.assertIn("084-367348", events[-1]["text"])

        second = client.requests[1]
        self.assertEqual(second[-2]["tool_calls"][0]["function"]["name"], "lookup_staff")
        self.assertEqual(second[-1]["role"], "tool")
        self.assertEqual(second[-1]["tool_call_id"], "call_1")
        self.assertIn("shukri@uts.edu.my", second[-1]["content"])  # the real tool ran

    def test_bad_tool_arguments_do_not_crash(self):
        client = FakeOpenAIClient(
            [tool_chunk(0, id="c", name="estimate_fees", arguments="{not json")],
            [text_chunk("Sorry, which programme?")],
        )
        events = list(self.make(client).run([], "fees?"))
        self.assertEqual(events[0]["args"], {})
        self.assertIn("error", json.loads(client.requests[1][-1]["content"]))

    def test_last_round_must_answer(self):
        rounds = [[tool_chunk(0, id=f"c{i}", name="estimate_fees", arguments='{"programme": "BCS"}')]
                  for i in range(MAX_TOOL_ROUNDS)]
        client = FakeOpenAIClient(*rounds, [text_chunk("RM52,440")])
        events = list(self.make(client).run([], "fees?"))
        self.assertEqual(client.tool_choices, [None] * MAX_TOOL_ROUNDS + ["none"])
        self.assertEqual(events[-1]["text"], "RM52,440")

    def test_tool_name_with_stray_space(self):
        client = FakeOpenAIClient([tool_chunk(0, id="c", name=" lookup_staff", arguments='{"query": "IT manager"}')],
                                  [text_chunk("ok")])
        events = list(self.make(client).run([], "IT manager?"))
        self.assertEqual(events[0]["name"], "lookup_staff")
        self.assertIn("shukri@uts.edu.my", client.requests[1][-1]["content"])

    def test_tool_reminder_follows_question(self):
        client = FakeOpenAIClient([text_chunk("Hi")])
        list(self.make(client).run([], "Fees for BCS?"))
        self.assertTrue(client.requests[0][-1]["content"].startswith("Fees for BCS?"))
        self.assertIn(TOOL_REMINDER, client.requests[0][-1]["content"])

    def test_waits_out_rate_limit_only_as_last_resort(self):
        limited = RateLimitError(retry_after="2")
        client = FakeOpenAIClient(limited, [text_chunk("ok")])
        with mock.patch("llm.openai_compat.time.sleep") as sleep:
            events = list(self.make(client).run([], "q", last_resort=True))
        sleep.assert_called_once_with(2.0)
        self.assertEqual(events[-1]["text"], "ok")

        client = FakeOpenAIClient(RateLimitError(retry_after="2"), [text_chunk("ok")])
        with self.assertRaises(RateLimitError):
            list(self.make(client).run([], "q"))  # another provider can answer instead

        client = FakeOpenAIClient(RateLimitError(retry_after="3600"), [text_chunk("ok")])
        with self.assertRaises(RateLimitError):
            list(self.make(client).run([], "q", last_resort=True))  # daily quota: don't hang


class RateLimitError(Exception):
    def __init__(self, retry_after):
        super().__init__("rate limited")
        self.status_code = 429
        self.response = NS(headers={"retry-after": retry_after})


class FakeProvider:
    def __init__(self, name, events=(), error=None, status=None):
        self.provider, self.model = name, f"{name}-model"
        self.events, self.error, self.status = list(events), error, status
        self.calls = 0

    def run(self, history, prompt, last_resort=False):
        self.calls += 1
        self.last_resort = last_resort
        yield from self.events
        if self.error:
            raise FakeAPIError(self.error, self.status)


class FakeAPIError(Exception):
    def __init__(self, message, status):
        super().__init__(message)
        self.status_code = status


TEXT = {"type": "text", "text": "answer"}
TOOL = {"type": "tool", "name": "estimate_fees", "label": "Fee estimator", "args": {"programme": "BCS"}}


class ChatRouterTest(unittest.TestCase):
    def test_falls_back_and_cools_down(self):
        limited = FakeProvider("gemini", error="quota", status=429)
        backup = FakeProvider("groq", [TEXT])
        router = ChatRouter([limited, backup])

        events = list(router.answer_events([], "q"))
        self.assertEqual(events, [{"type": "meta", "provider": "groq", "model": "groq-model"}, TEXT])

        list(router.answer_events([], "q"))
        self.assertEqual(limited.calls, 1)  # skipped while cooling down
        self.assertEqual(backup.calls, 2)

    def test_falls_back_after_tool_call_without_repeating_it(self):
        # Gemini's free tier often fails on the second request of a tool round.
        first = FakeProvider("gemini", [TOOL], error="quota", status=429)
        second = FakeProvider("groq", [TOOL, TEXT])
        events = list(ChatRouter([first, second]).answer_events([], "q"))
        self.assertEqual([e["type"] for e in events], ["meta", "tool", "meta", "text"])
        self.assertEqual(events[2]["provider"], "groq")
        self.assertEqual((first.last_resort, second.last_resort), (False, True))

    def test_error_after_text_is_raised(self):
        router = ChatRouter([FakeProvider("a", [TEXT], error="dropped", status=500), FakeProvider("b", [TEXT])])
        with self.assertRaises(FakeAPIError):
            list(router.answer_events([], "q"))

    def test_empty_answer_tries_next(self):
        events = list(ChatRouter([FakeProvider("a"), FakeProvider("b", [TEXT])]).answer_events([], "q"))
        self.assertEqual(events[0]["provider"], "b")

    def test_all_fail(self):
        router = ChatRouter([FakeProvider("a", error="x", status=503), FakeProvider("b", error="y", status=402)])
        with self.assertRaises(ModelUnavailable):
            list(router.answer_events([], "q"))


class ChainConfigTest(unittest.TestCase):
    def test_parse_chain(self):
        self.assertEqual(
            parse_chain("gemini:gemini-3.5-flash, ollama:qwen3:4b,groq,openrouter:openrouter/free"),
            [("gemini", "gemini-3.5-flash"), ("ollama", "qwen3:4b"),
             ("groq", "openai/gpt-oss-120b"), ("openrouter", "openrouter/free")],
        )
        with self.assertRaises(SystemExit):
            parse_chain("chatgpt:gpt-5")

    def test_providers_without_keys_are_skipped(self):
        env = {"GROQ_API_KEY": "test-key", "OPENROUTER_API_KEY": ""}
        with mock.patch.dict(os.environ, env):
            router = build_router("SYSTEM", "openrouter,groq,ollama")
        self.assertEqual([p.provider for p in router.providers], ["groq", "ollama"])

    def test_no_usable_provider(self):
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": ""}):
            with self.assertRaises(SystemExit):
                build_router("SYSTEM", "groq")


if __name__ == "__main__":
    unittest.main()
