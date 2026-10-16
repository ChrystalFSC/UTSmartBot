"""Measure how well UTSmartBot finds and answers UTS questions.

Usage:
    python -m evals.run                  # retrieval + full answers (calls the chat model for every question)
    python -m evals.run --retrieval-only # fast: only checks the search step, no API calls
    python -m evals.run --only tool-     # run questions whose id starts with "tool-"

To compare chat providers, pin the chain to one model, e.g. (bash):
    LLM_CHAIN=groq:openai/gpt-oss-120b python -m evals.run
The report lists which provider:model answered each question.

Each question in evals/questions.jsonl lists strings the answer must contain
("expect"), or marks it as something the bot should admit it does not know
("expect_unsure"). "expect_tool" also requires the bot to have called that tool.

- Retrieval hit: the expected strings appear in the passages given to the model.
- Answer pass: the expected strings appear in the bot's reply (or, for
  unanswerable questions, the bot says it is not sure instead of inventing).
"""
import argparse
import json
import os
import re
import time
from pathlib import Path

QUESTIONS_PATH = Path(__file__).with_name("questions.jsonl")
UNSURE_MARKERS = (
    "not sure", "don't have", "do not have", "not available", "no information", "not mentioned",
    "couldn't find", "could not find", "unable to find", "not provided", "contact uts", "tidak pasti",
    "not listed", "isn't listed", "no mention", "not specif", "does not contain", "do not contain",
    "doesn't contain", "don't contain", "not included", "not in the information", "no details",
)
# Models format text differently: curly apostrophes, non-breaking hyphens, narrow
# no-break spaces as thousands separators ("65 210"), etc. Fold them first.
UNICODE_FOLD = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", "​": "",
})


def normalize(text):
    return " ".join(text.translate(UNICODE_FOLD).lower().split())


def compact(text):
    """Letters, digits, @ and . only, so '084-367 348' matches '084-367348' and '65 210' matches '65,210'."""
    return re.sub(r"[^a-z0-9@.]", "", normalize(text))


def parse_events(body):
    """The /ask stream is one JSON event per line; return the answer text, tools used and model."""
    text, tools, model = [], [], None
    for line in body.splitlines():
        if line.strip():
            event = json.loads(line)
            if event["type"] == "text":
                text.append(event["text"])
            elif event["type"] == "tool":
                tools.append(event["name"])
            elif event["type"] == "meta":
                model = f"{event['provider']}:{event['model']}"
    return "".join(text), tools, model


def contains_all(text, expected):
    """Every expected item must appear; an item can be a list of acceptable variants
    (e.g. ["25.01.2027", "25 January 2027"])."""
    plain, packed = normalize(text), compact(text)
    found = lambda e: normalize(e) in plain or compact(e) in packed  # noqa: E731
    return all(any(found(v) for v in e) if isinstance(e, list) else found(e) for e in expected)


def is_unsure(text):
    return any(marker in normalize(text) for marker in UNSURE_MARKERS)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--retrieval-only", action="store_true", help="skip generating answers")
    parser.add_argument("--only", help="run only questions whose id starts with this (e.g. tool-)")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between answer requests (free-tier rate limits)")
    parser.add_argument("--retries", type=int, default=0, help="retry a question this many times if no answer came back")
    parser.add_argument("--retry-wait", type=float, default=60, help="seconds to wait before a retry")
    args = parser.parse_args()

    # The evaluation asks many questions in a row; the visitor limits are for the public site.
    for name in ("RATE_LIMIT_PER_MINUTE", "RATE_LIMIT_PER_DAY", "RATE_LIMIT_GLOBAL_PER_DAY"):
        os.environ[name] = "0"
    # Imported here so --help works without an API key.
    import UTSmartBot as bot

    questions = [json.loads(line) for line in QUESTIONS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.only:
        questions = [q for q in questions if q["id"].startswith(args.only)]

    client = bot.app.test_client()
    retrieval_hits = retrieval_total = answer_passes = degraded = 0
    models_used = {}  # which provider:model answered, for comparing LLM_CHAIN settings
    answer_times = []
    failures = []

    for q in questions:
        history = bot.clean_history(q.get("history", []))
        passages = bot.retriever.search(bot.retrieval_query(history, q["question"]), k=bot.TOP_K)
        context = "\n".join(p["text"] for p in passages)
        keyword_only = bot.retriever.keyword_only
        degraded += keyword_only

        retrieval_ok = None
        # Tool questions are answered from structured data (e.g. computed totals that
        # appear in no passage), so only answers are checked for them.
        if "expect" in q and "expect_tool" not in q:
            retrieval_total += 1
            retrieval_ok = contains_all(context, q["expect"])
            retrieval_hits += retrieval_ok

        answer_ok, answer, tools_used, model = None, "", [], None
        if not args.retrieval_only:
            started = time.time()
            response = client.post("/ask", json={"message": q["question"], "history": q.get("history", [])})
            for _ in range(args.retries):
                # Free tiers often allow only a few requests or tokens per minute.
                if response.status_code == 200 and "Sorry, something went wrong" not in response.get_data(as_text=True):
                    break
                print(f"  (no answer for {q['id']}, waiting {args.retry_wait:.0f}s and retrying)", flush=True)
                time.sleep(args.retry_wait)
                started = time.time()
                response = client.post("/ask", json={"message": q["question"], "history": q.get("history", [])})
            answer_times.append(time.time() - started)
            if response.status_code == 200:
                answer, tools_used, model = parse_events(response.get_data(as_text=True))
            else:
                answer = response.get_data(as_text=True)
            models_used[model] = models_used.get(model, 0) + 1
            if response.status_code != 200:
                answer_ok = False
            elif q.get("expect_unsure"):
                answer_ok = is_unsure(answer)
            else:
                answer_ok = contains_all(answer, q["expect"])
            if q.get("expect_tool") and q["expect_tool"] not in tools_used:
                answer_ok = False
            answer_passes += answer_ok
            time.sleep(args.delay)

        marks = []
        if retrieval_ok is not None:
            marks.append("search " + ("ok  " if retrieval_ok else "MISS"))
        if answer_ok is not None:
            marks.append("answer " + ("ok  " if answer_ok else "FAIL"))
        tools_note = (f"  tools: {', '.join(tools_used)}" if tools_used else "") + (f"  [{model}]" if model else "")
        print(f"{'  '.join(marks):<24} {q['id']}{tools_note}" + ("  (keyword search only)" if keyword_only else ""))
        if retrieval_ok is False or answer_ok is False:
            failures.append((q, answer))

    print()
    if retrieval_total:
        print(f"Retrieval hit rate: {retrieval_hits}/{retrieval_total} ({retrieval_hits / retrieval_total:.0%})")
    if not args.retrieval_only and questions:
        print(f"Answer pass rate:   {answer_passes}/{len(questions)} ({answer_passes / len(questions):.0%})")
    if answer_times:
        ordered = sorted(answer_times)
        print(f"Answer time:        median {ordered[len(ordered) // 2]:.1f}s, slowest {ordered[-1]:.1f}s")
    if models_used:
        print("Answered by:        " + ", ".join(f"{m or 'none (error)'} x{n}" for m, n in models_used.items()))
    if degraded:
        print(f"WARNING: {degraded} question(s) ran without embedding search (API quota or outage), "
              "so these results understate real quality. Re-run when the quota resets.")

    for q, answer in failures:
        print(f"\n--- {q['id']}: {q['question']}")
        print(f"expected: {'(unsure)' if q.get('expect_unsure') else q['expect']}"
              + (f" using {q['expect_tool']}" if q.get("expect_tool") else ""))
        if answer:
            print(answer.strip()[:600])


if __name__ == "__main__":
    main()
