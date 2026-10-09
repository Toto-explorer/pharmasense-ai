"""RAG layer (Step 3): chunking + TF-IDF vector index over research_documents.full_text.

TF-IDF (sparse vectors + cosine) runs with zero model downloads, which keeps the setup instant on
Windows. The VectorIndex interface (`search`) is deliberately tiny so you can swap in
sentence-transformers + FAISS/Chroma later without touching any agent code (see docs/ARCHITECTURE.md).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from . import config
from .guardrails import screen_injection

# tiny domain query-expansion so "cardiotoxicity" also finds "hERG" notes, etc.
SYNONYMS = {
    "cardiotoxicity": "hERG cardiac QT",
    "cardiotoxic": "hERG cardiac QT",
    "cardiac": "hERG cardiotoxicity",
    "herg": "cardiotoxicity cardiac",
    "pk": "pharmacokinetic",
    "pharmacokinetics": "pharmacokinetic PK",
    "stability": "metabolic microsomal",
    "liver": "hepatic enzymes",
    "toxicity": "cytotoxicity safety",
    "safety": "toxicity genotoxicity",
    "binding": "affinity SPR",
}


def chunk_text(text: str, max_words: int = 300, overlap: int = 40) -> List[str]:
    """Word-window chunker (~300 words ~ 400 tokens). Short documents stay as a single chunk."""
    words = (text or "").split()
    if len(words) <= max_words:
        return [" ".join(words)] if words else []
    chunks, start = [], 0
    while start < len(words):
        chunks.append(" ".join(words[start:start + max_words]))
        if start + max_words >= len(words):
            break
        start += max_words - overlap
    return chunks


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    text: str
    meta: Dict[str, object]


class VectorIndex:
    def __init__(self, docs_df, compounds_df):
        names = compounds_df.set_index("compound_id")[["compound_name", "target_protein"]].to_dict("index")
        self.chunks: List[Chunk] = []
        for _, r in docs_df.iterrows():
            cinfo = names.get(r["compound_id"], {})
            meta = {
                "doc_id": r["doc_id"], "title": r["title"], "doc_type": r["doc_type"], "author": r["author"],
                "date": str(r["date"]), "compound_id": r["compound_id"],
                "compound_name": cinfo.get("compound_name", ""), "target_protein": cinfo.get("target_protein", ""),
                "trial_id": None if str(r["trial_id"]) == "nan" else r["trial_id"], "tags": r["tags"],
            }
            for i, ch in enumerate(chunk_text(str(r["full_text"]))):
                self.chunks.append(Chunk("%s#%d" % (r["doc_id"], i), r["doc_id"], ch, meta))
        # Index text = body + title + tags + compound context, so metadata is searchable too.
        corpus = ["%s. %s Tags: %s. %s %s" % (c.meta["title"], c.text, c.meta["tags"],
                                              c.meta["compound_name"], c.meta["target_protein"]) for c in self.chunks]
        self.vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True, min_df=1)
        self.matrix = self.vectorizer.fit_transform(corpus)

    @staticmethod
    def expand_query(q: str) -> str:
        extra = [SYNONYMS[w] for w in re.findall(r"[a-zA-Z0-9\-]+", q.lower()) if w in SYNONYMS]
        return q + " " + " ".join(extra)

    def search(self, query: str, k: int = 5, doc_type: Optional[str] = None,
               compound_id: Optional[str] = None, min_score: Optional[float] = None) -> Dict[str, object]:
        min_score = config.RAG_MIN_SCORE if min_score is None else min_score
        k = max(1, min(int(k or 5), 10))
        qv = self.vectorizer.transform([self.expand_query(query)])
        scores = (self.matrix @ qv.T).toarray().ravel()
        order = np.argsort(-scores)
        results, flagged, seen = [], 0, set()
        for idx in order:
            s = float(scores[idx])
            if s < min_score:
                break
            ch = self.chunks[idx]
            if doc_type and str(ch.meta["doc_type"]).lower() != doc_type.lower():
                continue
            if compound_id and ch.meta["compound_id"] != compound_id:
                continue
            if ch.doc_id in seen:
                continue
            suspicious, _ = screen_injection(ch.text)
            if suspicious:  # never hand potentially hostile text to the LLM
                flagged += 1
                continue
            seen.add(ch.doc_id)
            m = ch.meta
            results.append({"doc_id": ch.doc_id, "title": m["title"], "doc_type": m["doc_type"], "date": m["date"],
                            "compound_id": m["compound_id"], "compound_name": m["compound_name"],
                            "trial_id": m["trial_id"], "score": round(s, 3), "text": ch.text})
            if len(results) >= k:
                break
        out: Dict[str, object] = {"query": query, "results": results, "flagged_injection_chunks": flagged}
        if not results:
            out["note"] = "No relevant documents found. Do not guess - tell the user nothing relevant was found."
        return out
