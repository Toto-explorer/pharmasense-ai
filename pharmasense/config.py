"""Central configuration. Everything is overridable through environment variables / .env."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
RUNTIME_DIR = Path(os.getenv("RUNTIME_DIR", ROOT / "runtime"))
INSTRUCTIONS_DIR = ROOT / "pharmasense" / "instructions"
TOOL_SPEC_FILE = ROOT / "pharmasense" / "tool_specs.json"
EXCEL_FILE = DATA_DIR / "pharmasense_synthetic_dataset.xlsx"
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

# ---- LLM (Groq free tier) -------------------------------------------------
# NOTE: Groq retired llama-3.3-70b-versatile and llama-3.1-8b-instant on 2026-08-16.
# Old IDs found in a .env are mapped to current ones automatically (see DEPRECATED_MODELS),
# and llm.py can also self-heal by asking Groq's /models endpoint for a working replacement.
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
DEPRECATED_MODELS = {
    "llama-3.3-70b-versatile": "openai/gpt-oss-120b",
    "llama-3.1-8b-instant": "openai/gpt-oss-20b",
    "llama3-70b-8192": "openai/gpt-oss-120b",
    "llama3-8b-8192": "openai/gpt-oss-20b",
}


def _model(env_name: str, default: str) -> str:
    m = os.getenv(env_name, default).strip() or default
    return DEPRECATED_MODELS.get(m, m)


MODEL_ROUTER = _model("MODEL_ROUTER", "openai/gpt-oss-20b")
MODEL_AGENT = _model("MODEL_AGENT", "openai/gpt-oss-120b")
MODEL_WRITER = _model("MODEL_WRITER", "openai/gpt-oss-120b")
MODEL_FALLBACK = _model("MODEL_FALLBACK", "openai/gpt-oss-20b")  # used when the main model is rate limited
REASONING_EFFORT = os.getenv("REASONING_EFFORT", "low").strip()  # gpt-oss only: low | medium | high ("" disables)
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "60"))

# ---- Agent loop / tools ---------------------------------------------------
MAX_TOOL_ITERATIONS = int(os.getenv("MAX_TOOL_ITERATIONS", "5"))
TOOL_OUTPUT_MAX_CHARS = int(os.getenv("TOOL_OUTPUT_MAX_CHARS", "3500"))
SQL_MAX_ROWS = int(os.getenv("SQL_MAX_ROWS", "100"))
MAX_PARALLEL_AGENTS = int(os.getenv("MAX_PARALLEL_AGENTS", "3"))
RAG_MIN_SCORE = float(os.getenv("RAG_MIN_SCORE", "0.08"))

# ---- Approximate list prices, USD per 1M tokens (input, output). -----------
# Groq's free tier costs you nothing; this is the "what would it cost on a paid plan" estimate.
PRICING = {
    "openai/gpt-oss-120b": (0.15, 0.60),
    "openai/gpt-oss-20b": (0.075, 0.30),
}
DEFAULT_PRICE = (0.15, 0.60)

LIVE_LOG_FILE = RUNTIME_DIR / "live_interaction_logs.csv"
ESCALATION_FILE = RUNTIME_DIR / "escalations.jsonl"

AGENT_DISPLAY = {
    "router": "Router / Planner Agent",
    "trial_data_analyst": "Trial Data Analyst Agent",
    "literature_research": "Literature & Document Research Agent",
    "ae_triage": "Adverse Event Triage Agent",
    "compound_similarity": "Compound Similarity Agent",
    "report_writer": "Report Writer Agent",
    "general_knowledge": "General Knowledge Agent",
}
