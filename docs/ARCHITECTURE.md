# PharmaSense AI - Architecture & Trade-offs (Step 9)

## 5-layer architecture
```
 User -> [ Application ]  Streamlit chat UI (app.py)  |  FastAPI (api.py)
              |
         [ Orchestration ]  guardrails.check_user_input -> Router/Planner (JSON plan)
              |               single | sequential (A -> B -> Writer) | parallel (fan-out threads -> Writer fan-in)
              |
         [ Agent + Tool ]   Trial Data Analyst | Literature Research | AE Triage | Compound Similarity | Report Writer
              |               bounded tool-calling loop (max 5 iterations), per-agent tool allow-list
              |
         [ Retrieval ]      VectorIndex (TF-IDF cosine, chunking, injection screening, min-score "I don't know")
              |
         [ Data ]           DuckDB (in-memory) <- 7 CSVs (Excel workbook = data dictionary + fallback)
 Cross-cutting: llm.py (single governed LLM entry: retry, fallback model, redaction, cost), observability.py (trace + CSV log)
```

## Key design decisions
| Decision | Why | Trade-off |
|---|---|---|
| Hand-rolled orchestrator instead of LangGraph/CrewAI | ~150 lines, every hand-off is explainable in an interview, no framework lock-in | You re-implement retries/state yourself; at larger scale a framework's checkpointing helps |
| One `call_llm()` choke point | Redaction, retries, model fallback, token/cost logging live in exactly one place (the "LLM Mesh" idea) | Slightly less flexible than per-agent SDK calls |
| Router on gpt-oss-20b, agents/writer on gpt-oss-120b | Routing is easy and latency-sensitive; tool calling and synthesis need the stronger model | Two models to monitor; fallback to the 20B model can reduce quality |
| Read-only SQL via DuckDB with allow-list validation + external access disabled | Text-to-SQL is the riskiest tool; defence in depth (parser rules + engine setting + LIMIT wrapper) | Regex validation is conservative: it can reject exotic but harmless queries |
| Safety rules enforced in code, not only in prompts | P1/Serious AEs are auto-escalated by `Agent._post_process` even if the model forgets; empty retrieval forces "I don't know" | More code paths to test (covered in `tests/`) |
| TF-IDF retrieval | No model download, deterministic, instant start on any laptop; corpus is small (250 docs, ~280 chars) | Misses purely semantic matches; swap in embeddings for larger corpora |
| Citations appended by code from verified doc ids | The LLM cannot invent a source; unknown ids are flagged (`unverified_citations`) | Source list is document-level, not span-level |

## Alternatives considered and rejected
1. **LangChain SQL agent / generic text-to-SQL chain** - less control over safety; harder to unit-test. Rejected for explicit tools + schema-in-prompt.
2. **Fine-tuned classifier for AE severity** - no labelled real data and regulators expect transparent rules; a rule engine with a rationale is auditable.
3. **Single mega-agent with all tools** - bigger prompt, tool-selection errors, no parallelism. Specialists are cheaper to evaluate.

## Limitations today
Lexical retrieval; feature-based (not structural) similarity; simulated escalation; in-process caches; single-node; free-tier rate limits; synthetic data only.

## What changes at 10x data / users
- **Retrieval:** sentence-transformers or hosted embeddings + pgvector/Chroma/FAISS, hybrid with BM25, re-ranker; chunk with overlap.
- **Data:** move DuckDB -> Postgres/Snowflake/BigQuery with a read-only role and row-level security; materialised views for common questions.
- **Serving:** stateless API replicas behind a queue; Redis for caching identical SQL/RAG calls; async LLM calls; streaming responses.
- **LLM:** paid Groq/other provider tier, per-user quotas, response cache, prompt-token trimming (schema pruning by routed intent).
- **Governance:** central LLM gateway (e.g. Dataiku LLM Mesh), PII service instead of regex, audit log to a warehouse, human-review queue (Jira/ServiceNow webhook) replacing the JSONL notifier.
- **Evaluation:** nightly golden-set regression in CI with RAGAS/DeepEval, drift alerts on pass rate, latency p95 and cost per query.
