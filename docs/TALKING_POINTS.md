# Interview talking points (Step 10)

Rehearsal order: **problem -> data -> architecture -> live demo -> results -> what I'd improve next.**

| Must-have skill | The decision I made | Why (trade-off) |
|---|---|---|
| LLM gateway / governance (LLM Mesh idea) | Every agent calls one `call_llm()` that redacts patient ids, retries, falls back to a smaller model and logs tokens/cost | One place to enforce policy and see cost; costs a little flexibility per agent |
| Warehouse (Snowflake idea) | DuckDB in-memory over CSVs behind a read-only SQL tool with validation and external access disabled | Zero setup for a demo; the tool interface is the same one a Snowflake read-only role would sit behind |
| RAG & vector databases | Chunker + TF-IDF index with min-score threshold, injection screening and code-appended citations | No model downloads and deterministic for a 250-doc corpus; I know the swap path (embeddings + FAISS/pgvector + hybrid + re-ranker) |
| Agent tooling & orchestration | Router returns a JSON plan; three patterns (single, sequential, parallel fan-out/fan-in) in a plain state machine | Easier to explain and debug than a framework; frameworks win when I need persistence/human-in-the-loop checkpoints |
| Prompt engineering | One 300-500 word brief per agent: scope, tool-selection rules, method, output format, refusal rules, worked example; schema + categorical values injected for SQL | Longer prompts cost tokens on every call, so the router (cheap model) is kept short and schemas are only given to SQL agents |
| Safety | Escalation of serious AEs enforced in code, not trusted to the prompt | A prompt can be ignored by the model; code cannot |
| Evaluation | 30-question golden set with ground truth computed from the data, plus optional LLM-judge | Deterministic checks are cheap and unbiased; judge scores add nuance but are noisy, so I report both |

**Numbers to quote after your own run** (fill from `eval/report.md`): pass rate ___ %, avg latency ___ s, avg tokens ___, est. cost/query $___.
**Cost optimisation first:** trim schema per routed intent and cache identical SQL/RAG calls; then move the router to rules for obvious intents.
