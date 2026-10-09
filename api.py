"""REST API (Step 8). Run:  uvicorn api:app --host 0.0.0.0 --port 8000   -> docs at /docs"""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from pharmasense import __version__, config
from pharmasense.observability import record_feedback
from pharmasense.orchestrator import PharmaSenseOrchestrator

app = FastAPI(title="PharmaSense AI", version=__version__,
              description="Multi-agent GenAI assistant for pharma R&D and clinical operations (Groq-powered).")


@lru_cache(maxsize=1)
def get_orchestrator() -> PharmaSenseOrchestrator:
    return PharmaSenseOrchestrator()


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000, examples=["Which Phase II oncology trials are below 60% enrollment?"])
    user_role: str = "Research Scientist"
    session_id: Optional[str] = None


class FeedbackRequest(BaseModel):
    run_id: str
    rating: int = Field(..., ge=1, le=5)


@app.get("/health")
def health() -> Dict[str, Any]:
    o = get_orchestrator()
    return {"status": "ok", "version": __version__, "llm_configured": o.llm.available,
            "models": {"router": config.MODEL_ROUTER, "agent": config.MODEL_AGENT, "writer": config.MODEL_WRITER},
            "tables": {t: len(o.store.table(t)) for t in o.store.frames}}


@app.post("/ask")
def ask(req: AskRequest) -> Dict[str, Any]:
    out = get_orchestrator().ask(req.question, req.user_role, req.session_id)
    if out.get("error") == "missing_api_key":
        raise HTTPException(status_code=503, detail=out["answer"])
    return out


@app.post("/feedback")
def feedback(req: FeedbackRequest) -> Dict[str, str]:
    record_feedback(req.run_id, req.rating)
    return {"status": "recorded"}


@app.get("/tools")
def tools() -> List[dict]:
    return list(get_orchestrator().toolbox.specs.values())


@app.get("/metrics")
def metrics() -> Dict[str, Any]:
    if not config.LIVE_LOG_FILE.exists():
        return {"interactions": 0}
    df = pd.read_csv(config.LIVE_LOG_FILE)
    return {"interactions": int(len(df)), "avg_latency_ms": round(float(df["latency_ms"].mean()), 1),
            "total_tokens": int(df["tokens_used"].sum()), "escalations": int(df["escalated_flag"].sum()),
            "est_cost_usd": round(float(df["cost_usd"].sum()), 4),
            "by_agent": df["agent_invoked"].value_counts().to_dict()}
