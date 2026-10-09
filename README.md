# PharmaSense AI - Agentic GenAI Data Scientist Portfolio Project

A production-style **multi-agent GenAI + RAG system** for a fictional pharma R&D / clinical-operations team, built from
`agentic_genai_project_guide.pdf` and powered by the **free Groq API**. It uses all 7 provided tables (CSV) plus the Excel workbook
(data dictionary + fallback source). All data is synthetic.

## Quick start (Hinglish)
1. Zip ko **extract** karo (right-click -> Extract All).
2. Free Groq key banao: https://console.groq.com/keys (credit card nahi chahiye).
3. **`setup_and_run.bat`** par double-click karo. Ye khud: Python check -> virtual env -> dependencies install -> key maangega aur `.env` me save karega -> health check -> menu.
4. Menu me `1` dabao -> browser me **http://localhost:8501** khulega. Example buttons se questions try karo.

Linux / macOS: `./setup_and_run.sh`   |   Docker: menu option 6 ya `docker compose up --build`

## What you get (maps 1:1 to the guide)
| Guide step | Where |
|---|---|
| 1 Data foundation | `pharmasense/data_layer.py` (DuckDB, FK validation, Excel data dictionary), `data/` |
| 2 Governed LLM access | `pharmasense/llm.py` - single `call_llm()`; retries, fallback model, redaction, cost accounting |
| 3 RAG layer | `pharmasense/rag.py` - chunking + TF-IDF vector index, `vector_search_tool` |
| 4 Agents + instructions | `pharmasense/instructions/*.md` (6 briefs), `pharmasense/tool_specs.json` (8 JSON tool specs) |
| 5 Tools | `pharmasense/tools.py` (SQL, stats, profile, RAG, citations, AE classifier, escalation, similarity) |
| 6 Orchestration | `pharmasense/orchestrator.py` - Router -> single / sequential / parallel fan-out + Report Writer |
| 7 Guardrails, eval, observability | `guardrails.py`, `observability.py`, `scripts/run_eval.py`, `eval/golden_set.json` (30 questions) |
| 8 Deploy & demo | `app.py` (Streamlit), `api.py` (FastAPI), `Dockerfile`, `docker-compose.yml` |
| 9 Architecture & trade-offs | `docs/ARCHITECTURE.md` |
| 10 Interview narrative | `docs/TALKING_POINTS.md`, `docs/GUARDRAILS_CHECKLIST.md` |

## Nobody leaves empty-handed
If the company data cannot answer - a general science question ("What is a JAK2 inhibitor?"), no internal documents found, SQL cannot
help, or an agent fails - the **General Knowledge Agent** answers from the Groq model's own knowledge. The answer is always prefixed with
a visible banner ("General knowledge answer - not from PharmaSense company data") and tagged in the UI, so internal facts and model
knowledge are never mixed up. Personal-health questions are answered in a **safe mode** (general educational info, no personal dosing,
"ask a doctor/pharmacist"). Only prompt-injection attempts are refused.

## Agents
Router/Planner -> **Trial Data Analyst** (SQL/stats), **Literature & Document Research** (RAG, cited), **Adverse Event Triage**
(classify + auto-escalate), **Compound Similarity**, **Report Writer** (merges parallel/sequential outputs), and the **General Knowledge Agent** (Groq fallback).

## Example questions
- Which Phase II oncology trials are below 60% enrollment?
- What has our internal research said about JAK2 inhibitors and cardiotoxicity?
- A site just reported a serious adverse event for Trial TRL-0034 - triage it.
- Give me the full picture on compound DKU-1001: labs, trials, safety, and related literature.
- Which compounds are most similar to DKU-1001?

## Configuration (`.env`)
| Variable | Default | Notes |
|---|---|---|
| `GROQ_API_KEY` | - | free key from console.groq.com |
| `MODEL_ROUTER` | `openai/gpt-oss-20b` | fast + cheap routing |
| `MODEL_AGENT` / `MODEL_WRITER` | `openai/gpt-oss-120b` | strong tool calling and writing |
| `MODEL_FALLBACK` | `openai/gpt-oss-20b` | used automatically on rate-limit / daily-token cap |
| `REASONING_EFFORT` | `low` | gpt-oss reasoning depth; `low` saves free-tier tokens |

Everything runs on Groq's **free tier** (no payment method). Groq retired `llama-3.3-70b-versatile` and `llama-3.1-8b-instant` on
2026-08-16: old IDs in a `.env` are mapped to the new ones automatically, and if Groq ever removes a model again the app asks Groq's
`/models` endpoint and switches to a working one by itself.

**Free-tier tip:** limits are per-minute and per-day tokens (see console.groq.com/docs/rate-limits and your console's Limits page). One
"full picture" question makes ~10 LLM calls. On a rate limit the app retries, then falls back to the smaller model. For heavy use set
`MODEL_AGENT=openai/gpt-oss-20b`.

## Commands
```
python scripts/check_setup.py                 # data + FK integrity + Groq connectivity
python -m pytest -q tests                     # 32 offline tests (fake LLM, no key needed)
python scripts/run_eval.py --limit 10         # live evaluation -> eval/report.md, eval/results.csv
python scripts/run_eval.py --judge            # full golden set + LLM-judge faithfulness/relevance
python scripts/run_eval.py --offline          # wiring test only (scripted fake LLM; scores are NOT meaningful)
python scripts/build_golden_set.py            # regenerate ground truths from the CSVs
uvicorn api:app --port 8000                   # REST API  (POST /ask, GET /health /tools /metrics)
```

## Deploying
- **Local container:** `docker compose up --build` -> UI :8501, API :8000 (`runtime/` is mounted so logs and tickets persist).
- **Streamlit Community Cloud / Hugging Face Spaces:** push this folder to GitHub, point to `app.py`, add `GROQ_API_KEY` as a secret.
- **Render / Railway / Fly.io:** use the Dockerfile; set `GROQ_API_KEY` as an environment variable; expose port 8501 (UI) or run `api:app` (API).
- Never commit `.env` (already in `.gitignore`).

## Honest limitations
- Retrieval is TF-IDF (lexical + small synonym map), not neural embeddings - zero downloads, instant start, good enough for 250 short
  documents. Swap `VectorIndex` for sentence-transformers + FAISS/Chroma when you need semantic recall (see `docs/ARCHITECTURE.md`).
- Compound similarity uses portfolio attributes, not molecular fingerprints.
- The escalation notifier is simulated (JSONL ticket file). Cost figures are paid-tier equivalents; Groq's free tier costs nothing.
- Agent briefs are ~300-500 words each (concise but complete: scope, tools, method, output format, refusal rules, worked example).
