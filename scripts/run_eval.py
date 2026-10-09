"""Evaluation harness (Step 7): runs the golden set through the live system and writes eval/report.md + eval/results.csv.

Examples
  python scripts/run_eval.py --limit 8                 # quick smoke run (saves free-tier tokens)
  python scripts/run_eval.py --category RAG
  python scripts/run_eval.py --judge                   # adds LLM-as-judge faithfulness / relevance (1-5)
  python scripts/run_eval.py --offline                 # no API key: wiring test with a scripted fake LLM
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd  # noqa: E402

from pharmasense import config  # noqa: E402
from pharmasense.llm import LLMClient  # noqa: E402
from pharmasense.orchestrator import PharmaSenseOrchestrator  # noqa: E402

UNKNOWN_PHRASES = ["don't know", "do not know", "couldn't find", "could not find", "no relevant", "not find any"]


def numbers_in(text):
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", text.replace(",", ""))]


def score_case(case, res):
    ans = res["answer"]
    low = ans.lower()
    used_agents = {a["key"] for a in res.get("agents", [])}
    used_tools = {t for a in res.get("agents", []) for t in a["tools_used"]}
    checks = {}
    if case.get("expect_refusal"):
        checks["refusal"] = bool(res["refused"])
        return checks
    if res.get("error"):
        checks["no_error"] = False
        return checks
    checks["non_empty"] = len(ans.strip()) > 20  # nobody leaves empty-handed
    if case.get("expect_general") or case.get("expect_fallback"):
        checks["general_fallback"] = res.get("answer_source") in ("general_knowledge", "mixed") and "General knowledge" in ans
    if case.get("expected_contains_any"):
        checks["contains_any"] = any(x.lower() in low for x in case["expected_contains_any"])
    if case.get("expected_agents"):
        checks["routing"] = set(case["expected_agents"]) <= used_agents
    if case.get("expected_pattern"):
        checks["pattern"] = (res.get("plan") or {}).get("pattern") == case["expected_pattern"]
    if case.get("expected_tools"):
        checks["tool_use"] = bool(used_tools & set(case["expected_tools"]))
    for item in case.get("expected_numbers", []):
        nums = numbers_in(ans)
        checks["number_%s" % item["any_of"][0]] = any(abs(n - v) <= item.get("tol", 0) + 1e-9 for n in nums for v in item["any_of"])
    if case.get("expected_contains"):
        checks["contains"] = all(x.lower() in low for x in case["expected_contains"])
    if case.get("expected_not_contains"):
        checks["not_contains"] = not any(x.lower() in low for x in case["expected_not_contains"])
    if case.get("require_citation"):
        checks["citation"] = bool(re.search(r"DOC-\d{5}", ans)) and not res["unverified_citations"]
    if "expect_escalation" in case:
        checks["escalation"] = bool(res["escalations"]) == case["expect_escalation"]
    return checks


def judge(llm, case, res):
    evidence = json.dumps([{"tool": e["tool"], "result": e["result"]} for e in res.get("tool_events", [])], default=str)[:3500]
    prompt = ("You are a strict evaluator. Given the QUESTION, the EVIDENCE returned by tools and the ANSWER, rate 1-5:\n"
              "faithfulness (is every claim supported by the evidence?) and relevance (does it answer the question?).\n"
              'Reply with JSON only: {"faithfulness": int, "relevance": int}\n\nQUESTION: %s\n\nEVIDENCE: %s\n\nANSWER: %s'
              % (case["question"], evidence, res["answer"][:1800]))
    try:
        out = llm.call_llm([{"role": "user", "content": prompt}], config.MODEL_FALLBACK, json_mode=True, temperature=0.0, max_tokens=500)
        j = json.loads(out.content)
        return int(j["faithfulness"]), int(j["relevance"])
    except Exception:
        return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--category")
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--sleep", type=float, default=2.0, help="seconds between questions (free-tier rate limits)")
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()

    cases = json.loads((ROOT / "eval" / "golden_set.json").read_text(encoding="utf-8"))
    if args.category:
        cases = [c for c in cases if c["category"].lower() == args.category.lower()]
    if args.ids:
        cases = [c for c in cases if c["id"] in args.ids]
    if args.limit:
        # spread the limited sample across categories instead of taking only the first rows
        by_cat = {}
        for c in cases:
            by_cat.setdefault(c["category"], []).append(c)
        picked, i = [], 0
        while len(picked) < args.limit and any(by_cat.values()):
            for k in list(by_cat):
                if by_cat[k] and len(picked) < args.limit:
                    picked.append(by_cat[k].pop(0))
        cases = picked

    if args.offline:
        sys.path.insert(0, str(ROOT / "tests"))
        from fake_llm import FakeGroq
        llm = LLMClient(client=FakeGroq())
    else:
        llm = LLMClient()
        if not llm.available:
            sys.exit("GROQ_API_KEY missing - put it in .env (or use --offline for a wiring test).")
    orch = PharmaSenseOrchestrator(llm=llm)

    rows = []
    for n, case in enumerate(cases, 1):
        print("[%d/%d] %s %s" % (n, len(cases), case["id"], case["question"][:70]), flush=True)
        t0 = time.time()
        res = orch.ask(case["question"], user_role="Evaluation")
        checks = score_case(case, res)
        faith = rel = None
        if args.judge and not case.get("expect_refusal") and not res.get("error"):
            faith, rel = judge(llm, case, res)
        m = res["metrics"]
        rows.append({"id": case["id"], "category": case["category"], "question": case["question"],
                     "passed": all(checks.values()) if checks else False,
                     "failed_checks": ",".join(k for k, v in checks.items() if not v),
                     "agents": ",".join(a["key"] for a in res.get("agents", [])), "pattern": (res.get("plan") or {}).get("pattern", ""),
                     "latency_s": round(m["latency_ms"] / 1000, 2), "tokens": m["total_tokens"], "cost_usd": m["cost_usd"],
                     "faithfulness": faith, "relevance": rel, "answer_preview": res["answer"][:200].replace("\n", " ")})
        print("   ->", "PASS" if rows[-1]["passed"] else "FAIL (%s)" % rows[-1]["failed_checks"], "%.1fs" % (time.time() - t0), flush=True)
        time.sleep(args.sleep if not args.offline else 0)

    df = pd.DataFrame(rows)
    df.to_csv(ROOT / "eval" / "results.csv", index=False)
    by_cat = df.groupby("category")["passed"].agg(["sum", "count"])
    lines = ["# PharmaSense AI - Evaluation report", "",
             "Mode: %s | questions: %d | models: router `%s`, agent `%s`, writer `%s`" % (
                 "OFFLINE wiring test (fake LLM)" if args.offline else "live Groq", len(df), config.MODEL_ROUTER, config.MODEL_AGENT, config.MODEL_WRITER), "",
             "## Summary", "",
             "- **Pass rate: %.0f%%** (%d / %d)" % (df.passed.mean() * 100, df.passed.sum(), len(df)),
             "- Avg latency: %.1f s | p95: %.1f s" % (df.latency_s.mean(), df.latency_s.quantile(0.95)),
             "- Avg tokens / question: %d | avg est. cost / question: $%.4f (paid-tier price equivalent; Groq free tier = $0)" % (df.tokens.mean(), df.cost_usd.mean())]
    if df.faithfulness.notna().any():
        lines.append("- LLM-judge faithfulness: %.2f / 5 | relevance: %.2f / 5" % (df.faithfulness.mean(), df.relevance.mean()))
    lines += ["", "## Pass rate by category", "", "| Category | Passed | Total |", "|---|---|---|"]
    lines += ["| %s | %d | %d |" % (k, r["sum"], r["count"]) for k, r in by_cat.iterrows()]
    bad = df[~df.passed]
    lines += ["", "## Flagged failures", ""]
    lines += ["- **%s** (%s): %s - failed: `%s`" % (r.id, r.category, r.question, r.failed_checks) for r in bad.itertuples()] or ["None"]
    lines += ["", "_Checks: routing, tool use, exact numbers/IDs computed from the CSVs, citation presence, "
              "mandatory escalation for serious AEs, non-empty answers, labelled general-knowledge fallback, refusal of prompt injection._"]
    (ROOT / "eval" / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:12]))
    print("\nSaved eval/report.md and eval/results.csv")


if __name__ == "__main__":
    main()
