# Guardrails & Evaluation Checklist (pre-demo)

| Category | Item | Where it is enforced | Test |
|---|---|---|---|
| Grounding | Every RAG answer cites its sources | Writer/Literature prompts + `_collect()` appends verified source list | `test_rag_citations`, eval `citation` check |
| Grounding | Empty retrieval never leads to a guess: falls back to a clearly labelled general-knowledge answer | `rag.py` min-score, `no_evidence` flag, `GENERAL_BANNER` | `test_no_internal_documents_falls_back_to_general`, golden Q16 |
| Grounding | Hallucinated DOC ids are detected | `unverified_citations` in orchestrator | `test_rag_citations` |
| Safety | Patient codes tokenised before reaching the LLM | `LLMClient._sanitize` (all messages incl. tool results) + output sanitiser | `test_redaction` |
| Safety | Serious / P1 AE always escalated | `Agent._post_process` auto-escalation (code) | `test_ae_guardrail_auto_escalates` |
| Safety | SQL is read-only and single-statement | `validate_sql` + DuckDB `enable_external_access=false` | `test_sql_rejected` |
| Safety | Personal medical questions answered in safe mode (general info, no personal dose/diagnosis, see a doctor) | `check_user_input` -> `medical` flag, General Knowledge Agent medical rules | `test_medical_gets_safe_general_answer` |
| Usability | Never an empty reply | `_finish()` last-resort message, writer fallback | `test_empty_answer_never_returned` |
| Robustness | Prompt-injection screened in user input and retrieved docs | `screen_injection` in `check_user_input` and `rag.search` | `test_injection_and_medical` |
| Robustness | Malformed tool calls / rate limits handled | `ToolCallFormatError` retry, back-off, fallback model | `llm.py` |
| Evaluation | 30-question golden set, ground truth computed from CSVs | `scripts/build_golden_set.py`, `scripts/run_eval.py` | `eval/report.md` |
| Observability | model, tokens, latency, tool, escalation, cost per call | `observability.Trace` -> `runtime/live_interaction_logs.csv`, dashboard tab | `test_logging` |
| Cost | Cost per query stated | `metrics.cost_usd` (paid-tier equivalent), report summary | eval report |
