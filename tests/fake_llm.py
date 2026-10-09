"""A scripted stand-in for the Groq SDK so the whole pipeline can be tested offline (no API key, no network)."""
import json
from types import SimpleNamespace as NS

from pharmasense.orchestrator import route_heuristic


def _resp(content="", tool_calls=None):
    msg = NS(content=content, tool_calls=tool_calls)
    return NS(choices=[NS(message=msg)], usage=NS(prompt_tokens=120, completion_tokens=40))


def _tc(i, name, args):
    return NS(id="call_%d" % i, function=NS(name=name, arguments=json.dumps(args)))


class FakeGroq:
    def __init__(self):
        self.calls = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        msgs = kw["messages"]
        system = msgs[0]["content"].split("\n")[0]  # first heading identifies the agent
        if kw.get("response_format"):  # router
            q = msgs[-1]["content"]
            plan = route_heuristic(q)
            return _resp(json.dumps(plan))
        has_tool_result = any(m["role"] == "tool" for m in msgs)
        tools = kw.get("tools")
        if tools and not has_tool_result:
            if "Trial Data Analyst" in system:
                return _resp("", [_tc(1, "sql_query_tool", {"query": "SELECT COUNT(*) AS n FROM clinical_trials WHERE status='Recruiting'"})])
            if "Literature" in system:
                return _resp("", [_tc(1, "vector_search_tool", {"query": "JAK2 inhibitors cardiotoxicity", "k": 3})])
            if "Adverse Event" in system:
                return _resp("", [_tc(1, "ae_severity_classifier_tool", {"event_id": "AE-00023"})])
            if "Compound Similarity" in system:
                return _resp("", [_tc(1, "compound_similarity_tool", {"compound": "DKU-1001", "k": 3})])
        if has_tool_result:
            last = [m for m in msgs if m["role"] == "tool"][-1]["content"]
            if "DOC-" in last:
                import re
                d = re.findall(r"DOC-\d{5}", last)[0]
                return _resp("Internal notes discuss this topic [%s]." % d)
            return _resp("Result based on tools: " + last[:120])
        return _resp("Merged report. " + msgs[-1]["content"][:200])
