"""Data layer (Step 1): loads the 7 tables (CSV first, Excel workbook as fallback) into DuckDB,
validates referential integrity, and builds an LLM-friendly schema description."""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import duckdb
import pandas as pd

from . import config

TABLES = [
    "compounds", "clinical_trials", "trial_sites", "lab_results",
    "adverse_events", "research_documents", "agent_interaction_logs",
]
DATE_COLUMNS = {
    "compounds": ["synthesis_date", "created_at"],
    "clinical_trials": ["start_date", "planned_end_date", "actual_end_date"],
    "lab_results": ["result_date"],
    "adverse_events": ["event_date"],
    "research_documents": ["date"],
}
# Columns whose distinct values are listed in the schema prompt so the LLM filters correctly.
CATEGORICAL_HINTS = {
    "compounds": ["chemical_class", "therapeutic_area", "target_protein", "discovery_phase"],
    "clinical_trials": ["trial_phase", "therapeutic_area", "status", "sponsor"],
    "trial_sites": ["country", "site_status"],
    "lab_results": ["experiment_type", "unit", "pass_fail"],
    "adverse_events": ["adverse_event_term", "severity", "seriousness", "causality_assessment", "outcome"],
    "research_documents": ["doc_type"],
}
FOREIGN_KEYS = [
    ("clinical_trials", "compound_id", "compounds", "compound_id", False),
    ("trial_sites", "trial_id", "clinical_trials", "trial_id", False),
    ("lab_results", "compound_id", "compounds", "compound_id", False),
    ("adverse_events", "trial_id", "clinical_trials", "trial_id", False),
    ("adverse_events", "site_id", "trial_sites", "site_id", False),
    ("research_documents", "compound_id", "compounds", "compound_id", True),
    ("research_documents", "trial_id", "clinical_trials", "trial_id", True),
]


class SQLValidationError(ValueError):
    pass


_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|copy|pragma|install|load|export|import|"
    r"truncate|call|vacuum|checkpoint|read_\w+|glob|write_\w+)\b", re.IGNORECASE)


def validate_sql(query: str) -> str:
    """Allow a single read-only SELECT / WITH statement; raise otherwise."""
    q = re.sub(r"/\*.*?\*/", " ", query or "", flags=re.S)
    q = re.sub(r"--[^\n]*", " ", q).strip()
    q = q.rstrip(";").strip()
    if not q:
        raise SQLValidationError("Empty query.")
    if ";" in q:
        raise SQLValidationError("Only a single SQL statement is allowed.")
    if not re.match(r"^(select|with)\b", q, re.IGNORECASE):
        raise SQLValidationError("Only read-only SELECT / WITH queries are allowed.")
    # ignore string literals when scanning for forbidden keywords
    no_strings = re.sub(r"'(?:[^']|'')*'", "''", q)
    m = _FORBIDDEN.search(no_strings)
    if m:
        raise SQLValidationError("Forbidden keyword in query: %s" % m.group(0))
    return q


class DataStore:
    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir or config.DATA_DIR)
        self.frames: Dict[str, pd.DataFrame] = {}
        self.source: Dict[str, str] = {}
        self._lock = threading.Lock()
        self._load()
        self.con = duckdb.connect(":memory:")
        self._register()
        self.dictionary = self._load_dictionary()

    # ---------------- loading ----------------
    def _load(self) -> None:
        excel = self.data_dir / "pharmasense_synthetic_dataset.xlsx"
        for t in TABLES:
            csv = self.data_dir / (t + ".csv")
            if csv.exists():
                self.frames[t] = pd.read_csv(csv)
                self.source[t] = "csv"
            elif excel.exists():
                self.frames[t] = pd.read_excel(excel, sheet_name=t)
                self.source[t] = "excel"
            else:
                raise FileNotFoundError("Missing data for table '%s' (looked for %s and %s)" % (t, csv, excel))

    def _register(self) -> None:
        for t, df in self.frames.items():
            self.con.register("_df_" + t, df)
            self.con.execute('CREATE TABLE "%s" AS SELECT * FROM "_df_%s"' % (t, t))
            self.con.unregister("_df_" + t)
            for col in DATE_COLUMNS.get(t, []):
                try:
                    self.con.execute('ALTER TABLE "%s" ALTER "%s" TYPE DATE' % (t, col))
                except Exception:
                    pass
        try:
            self.con.execute("ALTER TABLE agent_interaction_logs ALTER timestamp TYPE TIMESTAMP")
        except Exception:
            pass
        try:  # block file-system access from SQL entirely
            self.con.execute("SET enable_external_access=false")
        except Exception:
            pass

    def _load_dictionary(self) -> Dict[str, object]:
        """Parse the README sheet of the Excel workbook into table + column descriptions."""
        out: Dict[str, object] = {"tables": {}, "columns": {}}
        excel = self.data_dir / "pharmasense_synthetic_dataset.xlsx"
        if not excel.exists():
            return out
        try:
            df = pd.read_excel(excel, sheet_name="README", header=None)
        except Exception:
            return out
        mode = None
        for _, row in df.iterrows():
            a = str(row[0]).strip() if pd.notna(row[0]) else ""
            b = str(row[1]).strip() if pd.notna(row[1]) else ""
            if a == "Sheet":
                mode = "tables"
                continue
            if a.startswith("Column Notes"):
                mode = "columns"
                continue
            if not a:
                continue
            if mode == "tables" and a in TABLES:
                out["tables"][a] = {"description": b, "keys": str(row[3]) if pd.notna(row[3]) else "",
                                    "related": str(row[4]) if pd.notna(row[4]) else ""}
            elif mode == "columns" and "." in a:
                out["columns"][a] = b
        return out

    # ---------------- querying ----------------
    def query(self, sql: str, max_rows: Optional[int] = None) -> pd.DataFrame:
        q = validate_sql(sql)
        limit = max_rows or config.SQL_MAX_ROWS
        with self._lock:
            cur = self.con.cursor()
            try:
                return cur.execute("SELECT * FROM (%s) AS _q LIMIT %d" % (q, int(limit))).df()
            finally:
                cur.close()

    def table(self, name: str) -> pd.DataFrame:
        return self.frames[name]

    # ---------------- integrity ----------------
    def validate_integrity(self) -> List[Tuple[str, int, int]]:
        """Return [(check_description, rows_checked, violations)]."""
        results = []
        for child, col, parent, pcol, nullable in FOREIGN_KEYS:
            c = self.frames[child][col]
            if nullable:
                c = c.dropna()
            valid = set(self.frames[parent][pcol])
            violations = int((~c.isin(valid)).sum())
            results.append(("%s.%s -> %s.%s" % (child, col, parent, pcol), int(len(c)), violations))
        return results

    def consistency_with_excel(self) -> List[Tuple[str, int, int]]:
        """Compare CSV row counts against the Excel workbook sheets."""
        excel = self.data_dir / "pharmasense_synthetic_dataset.xlsx"
        out = []
        if not excel.exists():
            return out
        for t in TABLES:
            try:
                n = len(pd.read_excel(excel, sheet_name=t))
            except Exception:
                continue
            out.append((t, len(self.frames[t]), n))
        return out

    # ---------------- schema text for prompts ----------------
    def schema_text(self, include_logs: bool = True) -> str:
        lines = ["DATABASE SCHEMA (DuckDB SQL dialect, read-only):"]
        for t in TABLES:
            if t == "agent_interaction_logs" and not include_logs:
                continue
            desc = self.dictionary.get("tables", {}).get(t, {}).get("description", "")
            lines.append("\nTABLE %s -- %s" % (t, desc))
            types = dict(self.con.execute(
                "SELECT column_name, data_type FROM information_schema.columns WHERE table_name='%s'" % t).fetchall())
            df = self.frames[t]
            for col in df.columns:
                if t == "research_documents" and col == "full_text":
                    lines.append("  - full_text (not queryable here; use vector_search_tool)")
                    continue
                note = self.dictionary.get("columns", {}).get("%s.%s" % (t, col), "")
                vals = ""
                if col in CATEGORICAL_HINTS.get(t, []):
                    uniq = [str(v) for v in df[col].dropna().unique().tolist()]
                    if len(uniq) <= 25:
                        vals = " values: " + " | ".join(uniq)
                lines.append("  - %s %s%s%s" % (col, types.get(col, ""), vals, (" -- " + note) if note else ""))
        lines.append("\nRELATIONSHIPS: " + "; ".join("%s.%s -> %s.%s" % (c, cc, p, pc) for c, cc, p, pc, _ in FOREIGN_KEYS))
        lines.append("HINTS: enrollment % = actual_enrollment * 100.0 / target_enrollment. "
                     "clinical_trials.status uses the exact text 'Active, not recruiting'. "
                     "Compound names look like 'DKU-1001' (compound_name) with ids like 'CMP-0002' (compound_id).")
        return "\n".join(lines)


_STORE: Optional[DataStore] = None
_STORE_LOCK = threading.Lock()


def get_store() -> DataStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = DataStore()
        return _STORE
