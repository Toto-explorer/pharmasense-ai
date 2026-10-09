"""Guardrails (Step 7): identifier tokenisation, prompt-injection screening, refusal rules."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import List, Tuple

_SALT = "pharmasense-demo-salt"
PATIENT_RE = re.compile(r"\bPT-\d{4,6}\b")

REFUSAL_MEDICAL = (
    "I can't give personal medical advice, diagnoses or dosing recommendations. "
    "PharmaSense AI only supports R&D and clinical-operations questions about the company's own "
    "compound, trial, safety and research data. Please consult a qualified clinician for medical questions."
)
REFUSAL_INJECTION = (
    "That request tries to override my operating instructions, so I can't act on it. "
    "Ask me a question about compounds, clinical trials, adverse events or internal research documents instead."
)
REFUSAL_EMPTY = "Please type a question about compounds, trials, adverse events or research documents."

# --- prompt-injection patterns (applied to user input AND to retrieved documents) ---
INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|prompts?|rules|messages)",
    r"disregard (all |any |the )?(previous|prior|above|earlier|system)",
    r"forget (all |your |the )?(previous|prior|above|earlier)? ?(instructions|rules)",
    r"(reveal|show|print|repeat|output) (me )?(your|the) (system|hidden|initial) (prompt|instructions|message)",
    r"you are now (a|an|in)\b",
    r"\bnew instructions?\s*:",
    r"<\s*/?\s*(system|assistant|developer)\s*>",
    r"\bjailbreak\b",
    r"override (your|the) (safety|guardrails?|rules|instructions)",
    r"do anything now",
]
_INJECTION_RE = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]

# --- out-of-scope medical advice (personal use) ---
MEDICAL_ADVICE_PATTERNS = [
    r"\b(what|how much|which)\b.{0,30}\b(dose|dosage|dosing)\b.{0,40}\b(should|can|do) i\b",
    r"\bshould i (take|use|stop|start|skip)\b",
    r"\bcan i (take|use|mix|combine)\b.{0,40}\b(with|and)\b",
    r"\bwhat .{0,20}\b(dose|dosage)\b.{0,30}\b(for my|should i)\b",
    r"\b(diagnose|treat|cure)\b.{0,20}\b(me|my)\b",
    r"\bmy (symptoms?|headache|pain|fever|infection|condition)\b",
    r"\bis it safe for me\b",
]
_MEDICAL_RE = [re.compile(p, re.IGNORECASE) for p in MEDICAL_ADVICE_PATTERNS]


def tokenize_patient_id(match: "re.Match") -> str:
    digest = hashlib.sha256((_SALT + match.group(0)).encode()).hexdigest()[:6].upper()
    return "PT-TKN-" + digest


def redact_patient_ids(text: str) -> str:
    """Replace raw patient codes (PT-12345) with stable irreversible tokens (PT-TKN-9F2A1C)."""
    if not text:
        return text
    return PATIENT_RE.sub(tokenize_patient_id, text)


def screen_injection(text: str) -> Tuple[bool, List[str]]:
    """Return (is_suspicious, matched_patterns)."""
    hits = [rx.pattern for rx in _INJECTION_RE if rx.search(text or "")]
    return (len(hits) > 0, hits)


@dataclass
class GuardResult:
    allowed: bool
    reason: str = ""
    message: str = ""
    medical: bool = False  # personal-health question: answered in "safe mode" (general info, no personal dosing)


def check_user_input(question: str) -> GuardResult:
    q = (question or "").strip()
    if not q:
        return GuardResult(False, "empty", REFUSAL_EMPTY)
    if len(q) > 4000:
        return GuardResult(False, "too_long", "Question is too long; please shorten it to under 4000 characters.")
    suspicious, _ = screen_injection(q)
    if suspicious:
        return GuardResult(False, "prompt_injection", REFUSAL_INJECTION)
    if any(rx.search(q) for rx in _MEDICAL_RE):
        return GuardResult(True, "medical_safe_mode", medical=True)
    return GuardResult(True)


def sanitize_output(text: str) -> str:
    return redact_patient_ids(text)
