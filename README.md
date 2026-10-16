---
title: UTSmartBot
emoji: 🎓
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
short_description: AI assistant for the University of Technology Sarawak
---

# UTSmartBot

UTSmartBot is a Flask-based AI chatbot designed to answer questions related to the University of Technology Sarawak (UTS). It works with several AI providers (Google Gemini, Groq, OpenRouter or a local Ollama model) and serves as a friendly and intelligent virtual assistant capable of providing information on admissions, faculties, facilities, and general campus life.

***Latest Update: 16/10/2026***

## Features
- **Retrieval-Augmented Answers (RAG):** Instead of sending the whole knowledge base with every question, the bot searches it and gives Gemini only the most relevant passages (about 95% fewer tokens per question).
- **Hybrid Search:** Combines a local open-source embedding model (meaning, no API quota)  with BM25 keyword search (names, codes, extension numbers), merged with reciprocal rank fusion. Abbreviations defined in the data (e.g. *BCS = Bachelor of Computer Science*) are expanded automatically.
- **Tool Calling:** For exact answers the chat model calls Python tools built on structured data extracted from the knowledge base:
  - **Staff lookup:** 248 staff with emails, phone numbers, roles and departments (e.g. *"email of the bursar"*, *"who is the IT manager"*). Roles from the website's management team page are marked current and preferred over older ones.
  - **Programme finder:** 35 programmes filtered by interest, level, budget, school or qualification, with official entry requirements (e.g. *"I like computers and games"*, *"degrees under RM3,100/year for B40"*).
  - **Fee estimator:** exact totals from the current UTS fee schedule for Sarawakians (free tuition on the 16 FTES programmes for eligible students, 50% bursary otherwise), students from West Malaysia & Sabah (30% or 25% bursary) and international students, plus postgraduate scholarship and study mode and registration fees, calculated in code so the model never does the maths.
  The chat shows which tool was used (e.g. *"✓ Fee estimator · BCS · sarawakian b40"*).
- **Citations:** Answers cite their sources inline (`[1]`), with a "Sources" list under each reply.
- **Measured Accuracy:** An evaluation set of UTS questions (`evals/`) checks search hit rate and answer correctness, including questions the bot should refuse to guess.
- **Multiple AI Providers with Fallback:** The chat model can be Gemini, Groq, OpenRouter or Ollama, set with `LLM_CHAIN`. If one is rate-limited, out of quota or down, the next answers automatically, even halfway through a tool call. Each answer shows which model replied.
- **Streaming Answers:** Replies appear token by token.
- **Private Conversations:** Each browser keeps its own chat history, so the server is stateless and users never share a conversation.
- **Rich Formatting:** Answers are rendered as Markdown (lists, tables, links), sanitised with DOMPurify.
- **Responsive Web Interface:** Works on desktop and mobile, with suggested questions and a "New chat" button.
- **Docker Ready:** Runs under Gunicorn in a container, ready for Cloud Run, Render or Fly.io. The image includes the embedding model, so it needs about 3 GB of disk and at least 3 GB of RAM.

## How It Works
```
question ──► expand abbreviations ──► BM25 search ─┐
                                   └► embedding search ─┴► rank fusion ──► top passages
                                                                              (+ neighbouring chunks)
top passages + chat history ──► Gemini ──┬──► streamed answer with [n] citations
                                          └──► tool call (staff / programmes / fees)
                                                 └─► result sent back to Gemini ──► answer
```

## Project Structure
```
UTSmartBot.py            Flask app: /, /ask (streaming), /healthz
rag/documents.py         Loads knowledge files / URLs and splits them into chunks
rag/ingest.py            Builds the search index (python -m rag.ingest)
rag/embeddings.py        Embedding backends: local open-source model (default) or Gemini
rag/retriever.py         Hybrid search used by the app
knowledge/               Source documents (.md, .txt, .html, .pdf) + sources.txt URL list
knowledge/uts_website/   Snapshot of www.uts.edu.my pages (python -m rag.fetch)
knowledge_archive/       Original 2024 knowledge base before outdated sections were removed (not indexed)
rag/fetch.py             Downloads the pages in knowledge/sources.txt
data/                    Generated search index (commit it so deployments need no build step)
evals/questions.jsonl    Evaluation questions with expected answers
evals/run.py             Evaluation runner (python -m evals.run)
campus/extract.py        Builds structured data from the knowledge base (python -m campus.extract)
campus/data/             programmes.json and staff.json used by the tools
campus/tools.py          Staff lookup, programme finder, fee estimator + their JSON schemas
llm/gemini.py            Gemini chat provider
llm/openai_compat.py     Groq / OpenRouter / Ollama chat provider (OpenAI-compatible API)
llm/router.py            Provider chain from LLM_CHAIN, with fallback and cooldowns
llm/check.py             Tests each provider, including tool calling (python -m llm.check)
tests/                   Unit tests for data, tools, providers and fallback (python -m unittest discover tests)
templates/index.html     Chat UI
static/style.css         Styles
Dockerfile               Production container (Gunicorn)
```

## Updating the Knowledge Base
The bot's information comes from three places:

| Source | What it holds | Refresh with |
|---|---|---|
| `knowledge/uts_website/` | Snapshot of www.uts.edu.my (fee schedule, management team, programmes, admissions, academic calendars as text) and the staff pages of the school and department sites (scm, set, sbm, sbe, sfs, sps, sdsc, cir, itd, arc) | `python -m rag.fetch` |
| `knowledge/UTS_knowledge_base.md` | The original 2024-2025 knowledge base: staff lists, entry requirements, facilities, hostel rules, policies | edit by hand |
| `knowledge/*.md` | Extra notes, e.g. the FTES scheme summary (a first line `Source: https://...` sets the citation link) | edit by hand |

Sections of the original knowledge base that the website now covers (fees, academic calendar, management team) were moved out so search cannot return the old figures; the untouched original is kept in `knowledge_archive/`, which is not indexed. Entry requirements on the website load by script, so they still come from the older knowledge base. Staff found on the current school pages are marked current, and their phone numbers replace older ones; people listed only in the older knowledge base are flagged as possibly out of date. Emails that the school sites hide from automated readers are not decoded, so some current staff have no email in the data.

To refresh after UTS updates its website:
1. Download the pages listed in `knowledge/sources.txt` (add URLs there to include more pages; `www.uts.edu.my`'s robots.txt allows this):
   ```bash
   python -m rag.fetch
   ```
2. Rebuild the structured data for the tools (fees and management come from the website snapshot) and run the tests:
   ```bash
   python -m campus.extract
   ```
   ```bash
   python -m unittest discover tests
   ```
   The extractor prints anything it could not match (e.g. a programme without entry requirements) so it can be checked by hand. It also writes plain-sentence summaries of the fee schedule and management team (`knowledge/uts_website/summary_*.md`), which search finds far more easily than table cells. If UTS changed its figures, some tests will fail and show exactly what changed.
3. Rebuild the search index (only new or changed chunks are re-embedded):
   ```bash
   python -m rag.ingest
   ```
4. Check nothing got worse:
   ```bash
   python -m evals.run
   ```
   Use `--retrieval-only` for a quick search-only check, or `--only tool-` to run just the tool questions.

You can also drop your own `.md`, `.txt`, `.html` (saved from a browser) or `.pdf` files into `knowledge/` and run step 3.

## Embedding Model
Search uses **`intfloat/multilingual-e5-large`**, an open-source multilingual model (English, Malay, Chinese and more) that runs on your own machine with [fastembed](https://github.com/qdrant/fastembed). It is downloaded once (~2.2 GB) into `.models/` and then works offline, with no API key and no daily limit.

It was chosen by running the search evaluation (34 questions) against each candidate on a 4-thread laptop CPU:

| Model | Search hit rate | Time per question | One-time indexing |
|---|---|---|---|
| `intfloat/multilingual-e5-large` (default) | **33/34** | 0.23 s | ~25 min |
| `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | 30/34 | 0.01 s | 25 s |
| `minishlab/potion-multilingual-128M` | 28/34 | < 0.01 s | 1 s |
| Keyword search only | 30/33 | – | – |

To use another model (or `gemini-embedding-001`, which is limited to about 1,000 requests per day on the free tier), set `EMBEDDING_MODEL` in `.env` and rebuild the index with `python -m rag.ingest`. The index records which model built it, and the app refuses to start with a mismatched model rather than giving silently bad search results.

## Chat Model Providers
Search runs locally, so only the chat model needs an API. Free tiers are limited (Gemini allows as few as 20 requests per day for `gemini-3.5-flash`, and a question that uses a tool takes two requests), so the bot can chain several providers:

| Provider | `LLM_CHAIN` entry | Key | Notes |
|---|---|---|---|
| Google Gemini | `gemini:gemini-3.5-flash` | `GEMINI_API_KEY` | Strong tool calling and Malay; low free daily limits |
| [Groq](https://console.groq.com) | `groq:openai/gpt-oss-120b` | `GROQ_API_KEY` | Very fast open models; free tier with per-model limits |
| [OpenRouter](https://openrouter.ai) | `openrouter:openrouter/free` | `OPENROUTER_API_KEY` | `openrouter/free` picks a free model that supports tools; free models change often |
| [Ollama](https://ollama.com) | `ollama:qwen3:4b` | none | Runs on your own machine; unlimited but slow without a good GPU |

Example `.env`:
```env
GEMINI_API_KEY=...
GROQ_API_KEY=...
OPENROUTER_API_KEY=...
LLM_CHAIN=gemini:gemini-3.5-flash,groq:openai/gpt-oss-120b,openrouter:openrouter/free,gemini:gemini-flash-lite-latest
```

How the chain behaves:
- Providers are tried in order; entries without an API key are skipped.
- A provider that fails is skipped for a while: 10 minutes after a rate limit (429), an hour after a credits or key error (402/401/403), and 1 minute after other errors.
- If a provider fails after calling a tool but before writing any answer (common when a free quota runs out on the second request), the next provider takes over without showing the tool twice.
- The last provider in the chain has nothing to fall back to, so on a short rate limit (Groq's per-minute token cap) it waits up to about a minute and retries instead of failing.
- Groq, OpenRouter and Ollama get a one-line reminder after each question to call the matching tool for fees, staff and programme lists; without it they often answered from the search passages. After three tool rounds the model must answer from what it has looked up.
- Without `LLM_CHAIN`, the bot uses `GEMINI_MODEL` then `GEMINI_FALLBACK_MODEL`, as before.

### Provider comparison (16/10/2026, free tiers)
All 43 evaluation questions, with the same local search, one provider at a time, after the tool-calling fixes (clearer tool descriptions, the reminder for OpenAI-compatible providers, waiting out Groq's per-minute limit):

| Provider:model | Answers correct | Median / slowest answer | Notes |
|---|---|---|---|
| `openrouter:openrouter/free` | **43/43** | 22 s / 76 s | Now calls the tools on every tool question; was 40/43. Slow, because each request goes to whichever free model is available |
| `gemini:gemini-flash-lite-latest` | 42/43 | 3.2 s / 13 s | Same score as before. The one miss was a fee follow-up that ran out of tool rounds; it now has to answer instead. A rerun the same day lost 4 questions to Google 503 "high demand" errors (no wrong answers) |
| `groq:openai/gpt-oss-20b` | 7/7 tool + business questions* | 59 s / 80 s | Calls the right tool on every one |
| `groq:qwen/qwen3.8-27b` | 7/7 tool + business questions* | 87 s / 99 s | Calls the right tool on every one |
| `groq:openai/gpt-oss-120b` | 34/43 before the fixes* | 1.4 s / 2.8 s | Before the fixes it often skipped the tools (one wrong international fee, totals without registration, one wrong dean). Not re-measured: its daily token limit was used up |

\* Groq's free tier allows 200,000 tokens per day and only about 7,000 input tokens per minute per model, and each question uses about 5,500 tokens (two requests when a tool is called). So a full 43-question run does not fit in one day, and a tool question waits about a minute between its two requests. The Groq rows show the 6 `tool-` questions plus `business-programmes`, which all failed or skipped tools before the fixes. Groq works best as the last fallback.
`gemini-3.5-flash` was not included because its 20-requests-per-day free quota was used up that day. OpenRouter's free tier allows 50 requests per day (1,000 after buying 10 credits), roughly one full evaluation run.

Recommended chain, best first: `LLM_CHAIN=gemini:gemini-3.5-flash,gemini:gemini-flash-lite-latest,openrouter:openrouter/free,groq:openai/gpt-oss-20b`

Free models and limits change often, so after setting up keys run:
```bash
python -m llm.check
```
It asks each provider a question that needs the staff lookup tool and reports `OK`, `PARTIAL` (answered without the tool, or wrongly) or `FAILED`. To compare providers on the full evaluation set, pin the chain to one of them, e.g. `LLM_CHAIN=groq:openai/gpt-oss-120b python -m evals.run`. The report shows which model answered each question.

## Setup Instructions

Follow these steps to run the chatbot on your local machine:

### 1. Prerequisites
Ensure you have Python installed on your computer. You can download it from [python.org](https://www.python.org/).

### 2. Clone the Repository
Download the project code to your local machine and navigate into the project directory:
```bash
git clone <your-github-repo-url>
cd UTSmartBot
```

### 3. Install Dependencies
Install the required Python packages using `pip`:
```bash
pip install -r requirements.txt
```

### 4. Setup the Environment Variables
This project requires a Google Gemini API key to function. 
1. Get a free API key from [Google AI Studio](https://aistudio.google.com/app/apikey).
2. In the root folder of the project, copy `.env.example` to a new file named `.env`.
3. Add your API key to the `.env` file like this:
```env
GEMINI_API_KEY=your_actual_api_key_here
```

### 5. Run the Application
The search index is already built in `data/`. If you change anything in `knowledge/`, rebuild it first with `python -m rag.ingest`.

Start the Flask development server by running:
```bash
python UTSmartBot.py
```

### 6. Test the Chatbot
Open your web browser and go to:
[http://127.0.0.1:5000](http://127.0.0.1:5000)

You can now start asking the bot questions about UTS!

## Run with Docker
```bash
docker build -t utsmartbot .
```
```bash
docker run --rm -p 7860:7860 --env-file .env -e BEHIND_PROXY=0 utsmartbot
```
Then open [http://localhost:7860](http://localhost:7860). The image includes the embedding model (about 3 GB), so the first build takes a while.

## Deploy to Hugging Face Spaces (free)
Spaces' free *CPU basic* hardware (2 vCPU, 16 GB RAM) is enough for the local embedding model; the block at the top of this README tells Hugging Face to build the `Dockerfile` and use port 7860.

1. Create a Space at [huggingface.co/new-space](https://huggingface.co/new-space): choose **Docker** (blank template) and **CPU basic**.
2. In the Space's **Settings → Variables and secrets**, add your keys as **secrets**: `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, and `LLM_CHAIN` (see `.env.example`). Never commit `.env`.
3. Push this repository to the Space (it is a git repository too). Use a Hugging Face [access token](https://huggingface.co/settings/tokens) with write access as the password:
   ```bash
   git remote add space https://huggingface.co/spaces/<your-username>/<space-name>
   ```
   ```bash
   git push space main
   ```
4. Watch the **Logs** tab while it builds (about 10 minutes the first time), then open the Space's link.

Free Spaces sleep after about two days without visitors and wake up on the next visit, which takes a minute or two.

### Usage limits
A public link lets anyone use your API keys, so questions are limited per visitor and for the whole site (see `rate_limit.py`). Change the limits with Space variables, or `0` to turn one off:

| Variable | Default | Meaning |
|---|---|---|
| `RATE_LIMIT_PER_MINUTE` | 6 | Questions per visitor per minute |
| `RATE_LIMIT_PER_DAY` | 60 | Questions per visitor per day |
| `RATE_LIMIT_GLOBAL_PER_DAY` | 500 | Questions for the whole site per day |

## Deploy to Google Cloud Run
Needs billing enabled on the Google Cloud project (the free tier then covers small usage).
```bash
gcloud run deploy utsmartbot --source . --region asia-southeast1 --memory 4Gi --allow-unauthenticated --set-env-vars GEMINI_API_KEY=your_key
```
For production, store the keys in Secret Manager and use `--set-secrets` instead.

## Notes for Evaluators/Interviewers
- The `.env` file is excluded from version control (via `.gitignore`) for security purposes to prevent API key exposure. 
- Please make sure to use your own API keys (see `.env.example`) to test the application locally.
