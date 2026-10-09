"""PharmaSense AI - Streamlit chat UI (Step 8).  Run:  streamlit run app.py"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from pharmasense import config
from pharmasense.data_layer import TABLES, get_store
from pharmasense.observability import record_feedback
from pharmasense.orchestrator import PharmaSenseOrchestrator

st.set_page_config(page_title="PharmaSense AI", page_icon="💊", layout="wide")

EXAMPLES = [
    "Which Phase II oncology trials are below 60% enrollment?",
    "What has our internal research said about JAK2 inhibitors and cardiotoxicity?",
    "A site just reported a serious adverse event for Trial TRL-0034 - triage it.",
    "Which compounds are most similar to DKU-1001?",
    "Give me the full picture on compound DKU-1001: labs, trials, safety, and related literature.",
    "What dose of ibuprofen should I take for my headache?",
]
SOURCE_LABEL = {"company_data": "Source: PharmaSense company data", "general_knowledge": "Source: Groq model general knowledge (not company data)",
                "mixed": "Source: company data + Groq general knowledge", "refused": "Blocked by guardrail", "error": "Error"}
ROLES = ["Research Scientist", "Clinical Operations Manager", "Data Scientist", "Biostatistician",
         "Medical Science Liaison", "Regulatory Affairs Specialist"]


@st.cache_resource(show_spinner="Loading data, building the vector index and agents...")
def load_orchestrator() -> PharmaSenseOrchestrator:
    return PharmaSenseOrchestrator()


orch = load_orchestrator()

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.title("💊 PharmaSense AI")
    st.caption("Multi-agent GenAI assistant for pharma R&D and clinical operations")
    if orch.llm.available:
        st.success("Groq API key detected")
    else:
        st.error("GROQ_API_KEY missing - add it to the .env file and restart.")
    st.caption("Models  \nrouter: `%s`  \nagents: `%s`  \nwriter: `%s`" % (config.MODEL_ROUTER, config.MODEL_AGENT, config.MODEL_WRITER))
    role = st.selectbox("Your role", ROLES)
    st.markdown("**Try an example**")
    for i, ex in enumerate(EXAMPLES):
        if st.button(ex, key="ex%d" % i):
            st.session_state["pending"] = ex
    if st.button("Clear chat"):
        st.session_state["messages"] = []
        st.rerun()
    st.caption("All data is synthetic and fictional.")

if "messages" not in st.session_state:
    st.session_state["messages"] = []

tab_chat, tab_obs, tab_data, tab_eval = st.tabs(["💬 Assistant", "📊 Observability", "🗂️ Data", "🧪 Evaluation"])


def render_trace(res: dict) -> None:
    m = res.get("metrics", {})
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Latency", "%.1f s" % (m.get("latency_ms", 0) / 1000))
    c2.metric("Tokens", "{:,}".format(m.get("total_tokens", 0)))
    c3.metric("LLM calls", m.get("llm_calls", 0))
    c4.metric("Est. cost", "$%.4f" % m.get("cost_usd", 0))
    if res.get("plan"):
        st.markdown("**Router plan** (`%s`)" % res["plan"].get("router"))
        st.json(res["plan"], expanded=False)
    if res.get("agent_runs"):
        st.markdown("**Agent steps**")
        st.dataframe(pd.DataFrame([{"agent": a["agent"], "tools": ", ".join(a["tools"]) or "-", "latency_ms": a["latency_ms"],
                                    "tokens": a["prompt_tokens"] + a["completion_tokens"], "model": a["model"],
                                    "error": a["error"] or ""} for a in res["agent_runs"]]), hide_index=True)
    tools = [e for e in res.get("tool_events", [])]
    if tools:
        st.markdown("**Tool calls**")
        for e in tools:
            with st.expander("%s -> %s (%d ms)" % (e["agent"], e["tool"], e["latency_ms"])):
                st.code(e["arguments"], language="json")
                st.code(json.dumps(e["result"], indent=1, default=str)[:2500], language="json")
    if res.get("unverified_citations"):
        st.warning("Unverified citations removed from trust: %s" % res["unverified_citations"])


with tab_chat:
    st.header("Ask PharmaSense")
    for i, msg in enumerate(st.session_state["messages"]):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg["role"] == "assistant" and msg.get("result"):
                res = msg["result"]
                st.caption(SOURCE_LABEL.get(res.get("answer_source"), ""))
                for t in res.get("escalations", []):
                    st.error("Escalated to human review: %s (%s, %s)" % (t["ticket_id"], t["event_id"], t["priority"]))
                with st.expander("Agent trace, tools and cost"):
                    render_trace(res)
                fb = st.feedback("thumbs", key="fb%d" % i)
                if fb is not None and not msg.get("fb_sent"):
                    record_feedback(res["run_id"], 5 if fb == 1 else 1)
                    msg["fb_sent"] = True

    prompt = st.chat_input("Ask about trials, compounds, adverse events or research documents...")
    prompt = prompt or st.session_state.pop("pending", None)
    if prompt:
        st.session_state["messages"].append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("Routing to specialist agents..."):
                res = orch.ask(prompt, user_role=role, session_id=st.session_state.setdefault("sid", "SESS-UI"))
            st.markdown(res["answer"])
        st.session_state["messages"].append({"role": "assistant", "content": res["answer"], "result": res})
        st.rerun()

with tab_obs:
    st.header("Observability dashboard")
    seed = pd.read_csv(config.DATA_DIR / "agent_interaction_logs.csv")
    seed["source"] = "seed (provided dataset)"
    frames = [seed]
    if config.LIVE_LOG_FILE.exists():
        live = pd.read_csv(config.LIVE_LOG_FILE)
        live["source"] = "live (this app)"
        frames.append(live)
    allv = pd.concat(frames, ignore_index=True)
    choice = st.radio("Data", ["All", "seed (provided dataset)", "live (this app)"], horizontal=True)
    if choice != "All":
        allv = allv[allv["source"] == choice]
    if allv.empty:
        st.info("No live interactions yet - ask a question in the Assistant tab.")
    else:
        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("Interactions", len(allv))
        k2.metric("Avg latency", "%.0f ms" % allv["latency_ms"].mean())
        k3.metric("Total tokens", "{:,}".format(int(allv["tokens_used"].sum())))
        k4.metric("Escalation rate", "%.1f%%" % (allv["escalated_flag"].astype(str).str.lower().eq("true").mean() * 100))
        rating = pd.to_numeric(allv["feedback_rating"], errors="coerce")
        k5.metric("Avg rating", "%.2f" % rating.mean() if rating.notna().any() else "-")
        a, b = st.columns(2)
        a.subheader("Calls per agent")
        a.bar_chart(allv["agent_invoked"].value_counts())
        b.subheader("Avg latency (ms) per agent")
        b.bar_chart(allv.groupby("agent_invoked")["latency_ms"].mean())
        c, d = st.columns(2)
        c.subheader("Tool usage")
        c.bar_chart(allv["tool_called"].str.split(",").explode().value_counts())
        d.subheader("Tokens per user role")
        d.bar_chart(allv.groupby("user_role")["tokens_used"].sum())
        if "cost_usd" in allv.columns and allv["cost_usd"].notna().any():
            st.caption("Estimated cost of live traffic at paid-tier Groq prices: $%.4f (free tier = $0)" % allv["cost_usd"].fillna(0).sum())
        with st.expander("Raw log"):
            st.dataframe(allv.tail(200))
    if config.ESCALATION_FILE.exists():
        st.subheader("Escalation tickets")
        st.dataframe(pd.read_json(config.ESCALATION_FILE, lines=True), hide_index=True)

with tab_data:
    st.header("Data explorer")
    store = get_store()
    t = st.selectbox("Table", TABLES)
    df = store.table(t)
    if t == "research_documents":
        df = df.assign(full_text=df["full_text"].str.slice(0, 120) + "...")
    st.caption("%d rows x %d columns" % df.shape)
    st.dataframe(df, hide_index=True)
    st.subheader("Referential-integrity check")
    st.dataframe(pd.DataFrame(store.validate_integrity(), columns=["relationship", "rows_checked", "violations"]), hide_index=True)
    st.subheader("Data dictionary (from the Excel workbook)")
    dd = store.dictionary.get("tables", {})
    if dd:
        st.dataframe(pd.DataFrame([{"table": k, **v} for k, v in dd.items()]), hide_index=True)

with tab_eval:
    st.header("Evaluation report")
    rep = config.ROOT / "eval" / "report.md"
    if rep.exists():
        st.markdown(rep.read_text(encoding="utf-8"))
        res_csv = config.ROOT / "eval" / "results.csv"
        if res_csv.exists():
            st.dataframe(pd.read_csv(res_csv), hide_index=True)
    else:
        st.info("No evaluation run yet. From the menu choose 'Run evaluation', or run:  python scripts/run_eval.py --limit 10")
    gs = json.loads((config.ROOT / "eval" / "golden_set.json").read_text(encoding="utf-8"))
    with st.expander("Golden set (%d questions)" % len(gs)):
        st.dataframe(pd.DataFrame(gs)[["id", "category", "question", "expected_agents"]], hide_index=True)
