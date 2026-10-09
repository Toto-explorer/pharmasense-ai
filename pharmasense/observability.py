"""Observability (Step 7): per-run Trace + CSV log with the same schema as agent_interaction_logs.csv."""
from __future__ import annotations

import csv
import threading
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from . import config

LOG_COLUMNS = ["log_id", "session_id", "timestamp", "user_role", "user_query", "agent_invoked", "tool_called",
                "response_summary", "latency_ms", "tokens_used", "feedback_rating", "escalated_flag",
                "model", "prompt_tokens", "completion_tokens", "cost_usd", "run_id"]
_WRITE_LOCK = threading.Lock()


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    pin, pout = config.PRICING.get(model, config.DEFAULT_PRICE)
    return (prompt_tokens * pin + completion_tokens * pout) / 1_000_000.0


class Trace:
    """Collects everything that happens during one user question. Thread-safe (parallel agents share it)."""

    def __init__(self, query: str, user_role: str = "Research Scientist", session_id: Optional[str] = None):
        self.run_id = "RUN-" + uuid.uuid4().hex[:8].upper()
        self.session_id = session_id or ("SESS-" + uuid.uuid4().hex[:4].upper())
        self.query = query
        self.user_role = user_role
        self.t0 = time.time()
        self.llm_calls: List[Dict] = []
        self.agent_runs: List[Dict] = []
        self.events: List[Dict] = []
        self._lock = threading.Lock()

    def record_llm(self, agent: str, model: str, latency_ms: int, prompt_tokens: int, completion_tokens: int, tools_requested: List[str]):
        with self._lock:
            self.llm_calls.append({"agent": agent, "model": model, "latency_ms": latency_ms, "prompt_tokens": prompt_tokens,
                                   "completion_tokens": completion_tokens, "tools_requested": tools_requested,
                                   "cost_usd": estimate_cost(model, prompt_tokens, completion_tokens)})

    def add_event(self, agent: str, kind: str, name: str, detail: str = "", latency_ms: int = 0):
        with self._lock:
            self.events.append({"t_ms": int((time.time() - self.t0) * 1000), "agent": agent, "kind": kind,
                                "name": name, "detail": detail[:600], "latency_ms": latency_ms})

    def add_agent_run(self, agent: str, wall_ms: int, tools: List[str], summary: str, escalated: bool, error: Optional[str] = None):
        with self._lock:
            calls = [c for c in self.llm_calls if c["agent"] == agent]
            self.agent_runs.append({
                "agent": agent, "latency_ms": wall_ms, "tools": tools, "summary": summary, "escalated": escalated, "error": error,
                "prompt_tokens": sum(c["prompt_tokens"] for c in calls), "completion_tokens": sum(c["completion_tokens"] for c in calls),
                "model": calls[-1]["model"] if calls else "", "cost_usd": sum(c["cost_usd"] for c in calls)})

    def totals(self) -> Dict:
        with self._lock:
            pt = sum(c["prompt_tokens"] for c in self.llm_calls)
            ct = sum(c["completion_tokens"] for c in self.llm_calls)
            return {"latency_ms": int((time.time() - self.t0) * 1000), "llm_calls": len(self.llm_calls),
                    "prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct,
                    "cost_usd": round(sum(c["cost_usd"] for c in self.llm_calls), 6),
                    "models": sorted({c["model"] for c in self.llm_calls})}

    def flush(self) -> None:
        """Append one row per agent invocation to runtime/live_interaction_logs.csv."""
        rows = []
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for a in self.agent_runs:
            rows.append({"log_id": "LIVE-" + uuid.uuid4().hex[:8].upper(), "session_id": self.session_id, "timestamp": ts,
                         "user_role": self.user_role, "user_query": self.query[:300], "agent_invoked": a["agent"],
                         "tool_called": ",".join(a["tools"]) if a["tools"] else "none",
                         "response_summary": (a["summary"] or "").replace("\n", " ")[:240], "latency_ms": a["latency_ms"],
                         "tokens_used": a["prompt_tokens"] + a["completion_tokens"], "feedback_rating": "",
                         "escalated_flag": bool(a["escalated"]), "model": a["model"], "prompt_tokens": a["prompt_tokens"],
                         "completion_tokens": a["completion_tokens"], "cost_usd": round(a["cost_usd"], 6), "run_id": self.run_id})
        if not rows:
            return
        with _WRITE_LOCK:
            new = not config.LIVE_LOG_FILE.exists()
            with open(config.LIVE_LOG_FILE, "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
                if new:
                    w.writeheader()
                w.writerows(rows)


def record_feedback(run_id: str, rating: int) -> None:
    path = config.RUNTIME_DIR / "feedback.csv"
    with _WRITE_LOCK:
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["run_id", "rating", "timestamp"])
            w.writerow([run_id, rating, datetime.now().isoformat(timespec="seconds")])
