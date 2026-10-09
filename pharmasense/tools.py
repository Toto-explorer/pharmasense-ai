"""Tool library (Step 5). Every tool is a plain, unit-testable function returning JSON-serialisable data."""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from . import config
from .data_layer import DataStore, SQLValidationError, TABLES
from .guardrails import redact_patient_ids
from .rag import VectorIndex

_ESC_LOCK = threading.Lock()


def load_tool_specs() -> Dict[str, dict]:
    specs = json.loads(config.TOOL_SPEC_FILE.read_text(encoding="utf-8"))
    return {s["function"]["name"]: s for s in specs}


def _df_to_records(df: pd.DataFrame) -> List[dict]:
    df = df.copy()
    for c in df.columns:
        if str(df[c].dtype).startswith("datetime") or df[c].dtype == object:
            df[c] = df[c].apply(lambda v: v.isoformat()[:10] if hasattr(v, "isoformat") else v)
    df = df.astype(object).where(pd.notna(df), None)
    return json.loads(df.to_json(orient="records", date_format="iso"))


class ToolBox:
    def __init__(self, store: DataStore, index: VectorIndex):
        self.store = store
        self.index = index
        self.specs = load_tool_specs()
        self._registry: Dict[str, Callable[..., dict]] = {
            "sql_query_tool": self.sql_query_tool,
            "statistics_tool": self.statistics_tool,
            "compound_profile_tool": self.compound_profile_tool,
            "vector_search_tool": self.vector_search_tool,
            "citation_formatter_tool": self.citation_formatter_tool,
            "ae_severity_classifier_tool": self.ae_severity_classifier_tool,
            "escalation_notifier_tool": self.escalation_notifier_tool,
            "compound_similarity_tool": self.compound_similarity_tool,
        }
        self._feature_cache: Optional[tuple] = None

    # ------------------------------------------------------------------ dispatch
    def spec_for(self, names: List[str]) -> List[dict]:
        return [self.specs[n] for n in names]

    def execute(self, name: str, arguments: Any) -> dict:
        fn = self._registry.get(name)
        if fn is None:
            return {"error": "Unknown tool '%s'. Available: %s" % (name, ", ".join(self._registry))}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError:
                return {"error": "Tool arguments were not valid JSON."}
        if not isinstance(arguments, dict):
            return {"error": "Tool arguments must be a JSON object."}
        try:
            return fn(**arguments)
        except TypeError as e:
            return {"error": "Bad arguments for %s: %s" % (name, e)}
        except SQLValidationError as e:
            return {"error": "SQL rejected: %s" % e}
        except Exception as e:  # DuckDB binder errors etc. are returned to the LLM so it can self-correct
            return {"error": "%s: %s" % (type(e).__name__, str(e)[:400])}

    # ------------------------------------------------------------------ helpers
    def resolve_compound(self, ref: str) -> Optional[pd.Series]:
        comp = self.store.table("compounds")
        ref = (ref or "").strip()
        m = comp[(comp["compound_id"].str.lower() == ref.lower()) | (comp["compound_name"].str.lower() == ref.lower())]
        return m.iloc[0] if len(m) else None

    # ------------------------------------------------------------------ 1. SQL
    def sql_query_tool(self, query: str) -> dict:
        df = self.store.query(query)
        records = _df_to_records(df)
        return {"row_count": len(records), "truncated_at": config.SQL_MAX_ROWS if len(records) >= config.SQL_MAX_ROWS else None,
                "columns": list(df.columns), "rows": records}

    # ------------------------------------------------------------------ 2. statistics
    def statistics_tool(self, table: str, column: str, metric: str = "mean", group_by: Optional[str] = None,
                        column2: Optional[str] = None) -> dict:
        if table not in TABLES:
            return {"error": "Unknown table. Choose from %s" % TABLES}
        df = self.store.table(table)
        for c in [column, group_by, column2]:
            if c and c not in df.columns:
                return {"error": "Column '%s' not in %s. Columns: %s" % (c, table, list(df.columns))}
        metric = metric.lower()
        if metric == "pass_rate":
            if "pass_fail" not in df.columns:
                return {"error": "pass_rate only works on lab_results (pass_fail column)."}
            flag = (df["pass_fail"] == "Pass").astype(float)
            g = flag.groupby(df[group_by or column]) if (group_by or column) in df.columns else None
            res = g.mean().round(4).to_dict() if g is not None else {"all": float(flag.mean())}
            return {"metric": "pass_rate", "group_by": group_by or column, "result": res}
        if metric == "correlation":
            if not column2:
                return {"error": "correlation needs column2."}
            return {"metric": "pearson_correlation", "columns": [column, column2],
                    "result": round(float(df[column].corr(df[column2])), 4), "n": int(df[[column, column2]].dropna().shape[0])}
        funcs = {"mean": "mean", "median": "median", "std": "std", "min": "min", "max": "max", "sum": "sum", "count": "count"}
        if metric not in funcs:
            return {"error": "metric must be one of %s, pass_rate, correlation" % list(funcs)}
        if metric != "count" and not pd.api.types.is_numeric_dtype(df[column]):
            return {"error": "Column '%s' is not numeric." % column}
        if group_by:
            res = df.groupby(group_by)[column].agg(funcs[metric]).round(4).head(50)
            return {"metric": metric, "column": column, "group_by": group_by, "result": res.to_dict()}
        val = getattr(df[column], funcs[metric])()
        return {"metric": metric, "column": column, "result": round(float(val), 4)}

    # ------------------------------------------------------------------ 3. compound profile
    def compound_profile_tool(self, compound: str) -> dict:
        row = self.resolve_compound(compound)
        if row is None:
            return {"error": "Compound '%s' not found. Use a compound_id like CMP-0002 or a name like DKU-1001." % compound}
        cid = row["compound_id"]
        labs = self.store.table("lab_results")
        labs = labs[labs["compound_id"] == cid]
        lab_summary = [{"experiment_type": k, "n": int(len(g)), "pass_rate": round(float((g["pass_fail"] == "Pass").mean()), 3),
                        "mean_result": round(float(g["result_value"].mean()), 3), "unit": g["unit"].iloc[0]}
                       for k, g in labs.groupby("experiment_type")]
        trials = self.store.table("clinical_trials")
        trials = trials[trials["compound_id"] == cid].copy()
        trials["enrollment_pct"] = (trials["actual_enrollment"] * 100.0 / trials["target_enrollment"]).round(1)
        trial_ids = trials["trial_id"].tolist()
        ae = self.store.table("adverse_events")
        ae = ae[ae["trial_id"].isin(trial_ids)]
        docs = self.store.table("research_documents")
        docs = docs[docs["compound_id"] == cid]
        return {
            "compound": {k: (None if pd.isna(v) else (str(v) if not isinstance(v, (int, float)) else v))
                         for k, v in row.items() if k in ("compound_id", "compound_name", "chemical_class", "therapeutic_area",
                                                          "target_protein", "mechanism_of_action", "discovery_phase",
                                                          "molecular_weight_da", "solubility_mg_ml", "toxicity_score", "lead_scientist")},
            "lab_summary": lab_summary,
            "lab_totals": {"n": int(len(labs)), "failures": int((labs["pass_fail"] == "Fail").sum())},
            "trials": _df_to_records(trials[["trial_id", "trial_phase", "status", "target_enrollment", "actual_enrollment", "enrollment_pct"]]),
            "adverse_events": {"total": int(len(ae)), "serious": int((ae["seriousness"] == "Serious").sum()),
                               "fatal": int((ae["outcome"] == "Fatal").sum()),
                               "by_severity": ae["severity"].value_counts().to_dict(),
                               "top_terms": ae["adverse_event_term"].value_counts().head(3).to_dict()},
            "research_documents": {"count": int(len(docs)), "doc_ids": docs["doc_id"].head(8).tolist()},
        }

    # ------------------------------------------------------------------ 4. RAG
    def vector_search_tool(self, query: str, k: int = 5, doc_type: Optional[str] = None,
                           compound_id: Optional[str] = None) -> dict:
        return self.index.search(query, k=k, doc_type=doc_type, compound_id=compound_id)

    # ------------------------------------------------------------------ 5. citations
    def citation_formatter_tool(self, doc_ids: List[str]) -> dict:
        docs = self.store.table("research_documents").set_index("doc_id")
        out, missing = [], []
        for d in doc_ids or []:
            if d in docs.index:
                r = docs.loc[d]
                out.append("[%s] %s - %s, %s (%s%s)" % (d, r["title"], r["author"], r["date"], r["doc_type"],
                                                         "; compound %s" % r["compound_id"] if pd.notna(r["compound_id"]) else ""))
            else:
                missing.append(d)
        return {"citations": out, "not_found": missing}

    # ------------------------------------------------------------------ 6. AE classifier
    def ae_severity_classifier_tool(self, event_id: Optional[str] = None, adverse_event_term: Optional[str] = None,
                                    severity: Optional[str] = None, seriousness: Optional[str] = None,
                                    causality_assessment: Optional[str] = None, outcome: Optional[str] = None,
                                    trial_id: Optional[str] = None) -> dict:
        ae = self.store.table("adverse_events")
        rec: Dict[str, Any] = {}
        if event_id:
            m = ae[ae["event_id"].str.upper() == event_id.upper()]
            if m.empty:
                return {"error": "event_id '%s' not found." % event_id}
            rec = m.iloc[0].to_dict()
        for k, v in dict(adverse_event_term=adverse_event_term, severity=severity, seriousness=seriousness,
                         causality_assessment=causality_assessment, outcome=outcome, trial_id=trial_id).items():
            if v and not rec.get(k):
                rec[k] = v
        if not rec.get("seriousness") and not rec.get("severity"):
            return {"error": "Provide an event_id or at least severity/seriousness."}

        sev, ser = rec.get("severity"), rec.get("seriousness")
        cau, out = rec.get("causality_assessment") or "", rec.get("outcome") or ""
        related = cau in ("Related", "Possibly Related")
        reasons: List[str] = []
        if ser == "Serious" or out == "Fatal":
            priority, label = "P1", "CRITICAL"
            reasons.append("Serious event or fatal outcome: mandatory escalation to a human safety reviewer.")
        elif sev == "Severe" and related:
            priority, label = "P2", "HIGH"
            reasons.append("Severe event assessed as related/possibly related to study drug.")
        elif sev == "Severe" or (sev == "Moderate" and related) or out == "Resolved with Sequelae":
            priority, label = "P3", "MEDIUM"
            reasons.append("Severe-but-unrelated, moderate-and-related, or sequelae outcome: review within the normal cycle.")
        else:
            priority, label = "P4", "LOW"
            reasons.append("Mild/moderate and unrelated or unlikely related: routine monitoring.")
        if out == "Ongoing" and priority in ("P1", "P2"):
            reasons.append("Event is still ongoing.")

        ctx: Dict[str, Any] = {}
        tid = rec.get("trial_id")
        if tid:
            t_ae = ae[ae["trial_id"] == tid]
            ctx = {"trial_id": tid, "trial_total_events": int(len(t_ae)),
                   "trial_serious_events": int((t_ae["seriousness"] == "Serious").sum())}
            term = rec.get("adverse_event_term")
            if term:
                n_same = int((t_ae["adverse_event_term"] == term).sum())
                ctx["same_term_in_trial"] = n_same
                if n_same >= 3:
                    reasons.append("Possible signal: '%s' reported %d times in this trial." % (term, n_same))
        return {"event_id": rec.get("event_id"), "adverse_event_term": rec.get("adverse_event_term"),
                "inputs": {"severity": sev, "seriousness": ser, "causality": cau, "outcome": out},
                "priority": priority, "priority_label": label, "requires_escalation": priority == "P1",
                "rationale": reasons, "trial_context": ctx}

    # ------------------------------------------------------------------ 7. escalation (simulated)
    def escalation_notifier_tool(self, event_id: str, priority: str, reason: str, trial_id: Optional[str] = None) -> dict:
        """Simulated notifier: appends to runtime/escalations.jsonl (swap for a webhook / e-mail in production)."""
        with _ESC_LOCK:
            existing = {}
            if config.ESCALATION_FILE.exists():
                for line in config.ESCALATION_FILE.read_text(encoding="utf-8").splitlines():
                    try:
                        j = json.loads(line)
                        existing[j["event_id"]] = j
                    except Exception:
                        pass
            if event_id in existing:  # idempotent: one ticket per event
                j = dict(existing[event_id]); j["status"] = "already_escalated"
                return j
            ticket = {"ticket_id": "ESC-%s-%s" % (datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y%m%d"), uuid.uuid4().hex[:6].upper()),
                      "event_id": event_id, "trial_id": trial_id, "priority": priority, "reason": reason[:500],
                      "assigned_to": "Pharmacovigilance Safety Review Board", "created_utc": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"),
                      "status": "queued_for_human_review"}
            with open(config.ESCALATION_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(ticket) + "\n")
            return ticket

    # ------------------------------------------------------------------ 8. similarity
    def _features(self):
        if self._feature_cache is None:
            comp = self.store.table("compounds").reset_index(drop=True)
            blocks, weights = [], []
            for col, w in (("chemical_class", 1.0), ("therapeutic_area", 1.0), ("target_protein", 1.5), ("discovery_phase", 0.5)):
                d = pd.get_dummies(comp[col]).astype(float) * w
                blocks.append(d.values)
            num = comp[["molecular_weight_da", "solubility_mg_ml", "toxicity_score"]]
            z = ((num - num.mean()) / num.std()).fillna(0).values * 0.6
            blocks.append(z)
            X = np.hstack(blocks)
            norms = np.linalg.norm(X, axis=1, keepdims=True)
            norms[norms == 0] = 1
            self._feature_cache = (comp, X / norms)
        return self._feature_cache

    def compound_similarity_tool(self, compound: str, k: int = 5, same_therapeutic_area_only: bool = False) -> dict:
        if isinstance(same_therapeutic_area_only, str):  # LLMs sometimes send "false" as a string
            same_therapeutic_area_only = same_therapeutic_area_only.strip().lower() == "true"
        row = self.resolve_compound(compound)
        if row is None:
            return {"error": "Compound '%s' not found." % compound}
        comp, Xn = self._features()
        i = int(comp.index[comp["compound_id"] == row["compound_id"]][0])
        sims = Xn @ Xn[i]
        sims[i] = -1
        order = np.argsort(-sims)
        k = max(1, min(int(k or 5), 10))
        res = []
        for j in order:
            r = comp.iloc[j]
            if same_therapeutic_area_only and r["therapeutic_area"] != row["therapeutic_area"]:
                continue
            shared = [c for c in ("chemical_class", "therapeutic_area", "target_protein", "discovery_phase") if r[c] == row[c]]
            res.append({"compound_id": r["compound_id"], "compound_name": r["compound_name"], "similarity": round(float(sims[j]), 3),
                        "shared_attributes": shared, "chemical_class": r["chemical_class"], "target_protein": r["target_protein"],
                        "therapeutic_area": r["therapeutic_area"], "discovery_phase": r["discovery_phase"],
                        "toxicity_score": float(r["toxicity_score"])})
            if len(res) >= k:
                break
        return {"query_compound": {"compound_id": row["compound_id"], "compound_name": row["compound_name"],
                                   "chemical_class": row["chemical_class"], "target_protein": row["target_protein"],
                                   "therapeutic_area": row["therapeutic_area"], "toxicity_score": float(row["toxicity_score"])},
                "method": "cosine similarity over one-hot (class, area, target, phase) + standardised MW/solubility/toxicity",
                "neighbors": res}
