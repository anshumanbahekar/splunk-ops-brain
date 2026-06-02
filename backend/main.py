"""
Splunk Ops Brain — Backend
Multi-LLM: Groq (free, dev) → Claude (demo/production)
Switch via LLM_PROVIDER env var: "groq" | "claude"
"""

import os
import json
import time
import uuid
import requests
import urllib3
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = FastAPI(title="Splunk Ops Brain")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Config ───────────────────────────────────────────────────────────────────
SPLUNK_HOST     = os.getenv("SPLUNK_HOST", "https://localhost:8089")
SPLUNK_USER     = os.getenv("SPLUNK_USER", "admin")
SPLUNK_PASSWORD = os.getenv("SPLUNK_PASSWORD", "changeme")
LLM_PROVIDER    = os.getenv("LLM_PROVIDER", "groq")   # "groq" | "claude"
GROQ_API_KEY    = os.getenv("GROQ_API_KEY", "")
ANTHROPIC_KEY   = os.getenv("ANTHROPIC_API_KEY", "")

# ─── LLM clients (lazy init) ──────────────────────────────────────────────────
_groq_client   = None
_claude_client = None

def get_groq():
    global _groq_client
    if _groq_client is None:
        from groq import Groq
        _groq_client = Groq(api_key=GROQ_API_KEY)
    return _groq_client

def get_claude():
    global _claude_client
    if _claude_client is None:
        import anthropic
        _claude_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    return _claude_client

# ─── Splunk helpers ────────────────────────────────────────────────────────────

def splunk_search(spl: str, earliest: str = "-24h", latest: str = "now", max_results: int = 50) -> dict:
    auth = (SPLUNK_USER, SPLUNK_PASSWORD)
    spl_full = f"search {spl}" if not spl.strip().lower().startswith("search") else spl

    resp = requests.post(
        f"{SPLUNK_HOST}/services/search/jobs",
        auth=auth, verify=False,
        data={"search": spl_full, "earliest_time": earliest,
              "latest_time": latest, "output_mode": "json"},
    )
    resp.raise_for_status()
    sid = resp.json()["sid"]

    for _ in range(30):
        time.sleep(1)
        r = requests.get(f"{SPLUNK_HOST}/services/search/jobs/{sid}",
                         auth=auth, verify=False, params={"output_mode": "json"})
        if r.json()["entry"][0]["content"]["dispatchState"] in ("DONE", "FAILED"):
            break

    r = requests.get(f"{SPLUNK_HOST}/services/search/jobs/{sid}/results",
                     auth=auth, verify=False,
                     params={"output_mode": "json", "count": max_results})
    results = r.json().get("results", [])
    return {"spl": spl, "result_count": len(results), "results": results}


def get_splunk_alerts() -> dict:
    auth = (SPLUNK_USER, SPLUNK_PASSWORD)
    r = requests.get(f"{SPLUNK_HOST}/services/alerts/fired_alerts",
                     auth=auth, verify=False, params={"output_mode": "json", "count": 20})
    entries = r.json().get("entry", [])
    return {"alert_count": len(entries), "alerts": [
        {"name": e["name"],
         "triggered_at": e["content"].get("trigger_time_rendered", ""),
         "severity": e["content"].get("severity", "")}
        for e in entries
    ]}


def get_index_summary() -> dict:
    auth = (SPLUNK_USER, SPLUNK_PASSWORD)
    r = requests.get(f"{SPLUNK_HOST}/services/data/indexes",
                     auth=auth, verify=False, params={"output_mode": "json", "count": 30})
    entries = r.json().get("entry", [])
    return {"indexes": [
        {"name": e["name"],
         "total_events": e["content"].get("totalEventCount", 0),
         "current_size_mb": round(e["content"].get("currentDBSizeMB", 0), 1)}
        for e in entries if not e["name"].startswith("_")
    ]}


def run_tool(name: str, inputs: dict) -> str:
    try:
        if name == "run_spl_query":
            result = splunk_search(inputs["spl"],
                                   earliest=inputs.get("earliest", "-24h"),
                                   latest=inputs.get("latest", "now"))
        elif name == "get_alerts":
            result = get_splunk_alerts()
        elif name == "get_index_summary":
            result = get_index_summary()
        else:
            result = {"error": f"Unknown tool: {name}"}
    except Exception as e:
        result = {"error": str(e)}
    return json.dumps(result, indent=2)


# ─── Tool schemas (OpenAI format — used by Groq) ──────────────────────────────
TOOLS_OPENAI = [
    {
        "type": "function",
        "function": {
            "name": "run_spl_query",
            "description": "Execute a Splunk SPL query and return results. Use for log search, anomaly detection, event correlation, and incident investigation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "spl":      {"type": "string", "description": "SPL query, e.g. 'index=main ERROR | stats count by host | sort -count'"},
                    "earliest": {"type": "string", "description": "Time lower bound, e.g. '-1h', '-24h'. Default: -24h"},
                    "latest":   {"type": "string", "description": "Time upper bound. Default: now"},
                },
                "required": ["spl"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_alerts",
            "description": "Fetch recently fired Splunk alerts. Call this first in any investigation.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_index_summary",
            "description": "List available Splunk indexes and event counts. Use to discover available data sources.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

# Anthropic format — used by Claude
TOOLS_ANTHROPIC = [
    {
        "name": "run_spl_query",
        "description": "Execute a Splunk SPL query and return results. Use for log search, anomaly detection, event correlation, and incident investigation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "spl":      {"type": "string", "description": "SPL query"},
                "earliest": {"type": "string", "description": "Time lower bound, e.g. '-1h'. Default: -24h"},
                "latest":   {"type": "string", "description": "Time upper bound. Default: now"},
            },
            "required": ["spl"],
        },
    },
    {
        "name": "get_alerts",
        "description": "Fetch recently fired Splunk alerts.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_index_summary",
        "description": "List available Splunk indexes and event counts.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]

SYSTEM_PROMPT = """You are Splunk Ops Brain — an elite AI operations analyst.

Investigate questions by querying Splunk, correlating data, and producing clear answers like a senior SRE.

Approach:
1. Check what data/alerts exist first
2. Form hypotheses, run targeted SPL queries to test them
3. Correlate across sources, dig deeper on interesting findings
4. End with a structured summary: what/when/which systems/root cause/next steps

SPL tips: scope by time, use | stats | timechart | top for aggregation, | sort -count for ranking."""


# ─── Agent loops ──────────────────────────────────────────────────────────────

def agent_loop_groq(user_message: str, history: list, max_iterations: int = 10) -> dict:
    """Groq-powered agent loop (OpenAI-compatible tool use)."""
    client = get_groq()
    messages = [{"role": "system", "content": SYSTEM_PROMPT + "\n\nIMPORTANT: After gathering enough data from tool calls, you MUST write your final answer as a text response and stop calling tools. Do not call more than 4 tools per investigation."}]
    messages += history
    messages.append({"role": "user", "content": user_message})
    trace = []

    for iteration in range(max_iterations):
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            tools=TOOLS_OPENAI,
            tool_choice="auto",
            max_tokens=4096,
        )
        msg = response.choices[0].message
        step = {"iteration": iteration + 1, "tool_calls": [], "text": msg.content or ""}
        trace.append(step)

        # No tool calls → done
        if not msg.tool_calls:
            break

        # Record + execute tool calls
        tool_results_msgs = []
        for tc in msg.tool_calls:
            inputs = json.loads(tc.function.arguments)
            step["tool_calls"].append({"name": tc.function.name, "inputs": inputs})
            result = run_tool(tc.function.name, inputs)
            tool_results_msgs.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

        messages.append(msg)
        messages.extend(tool_results_msgs)

    final_answer = trace[-1].get("text", "Investigation complete.")
    return {"answer": final_answer, "trace": trace, "iterations": len(trace),
            "provider": "groq", "model": "llama-3.3-70b-versatile"}


def agent_loop_claude(user_message: str, history: list, max_iterations: int = 10) -> dict:
    """Claude-powered agent loop (Anthropic tool use)."""
    client = get_claude()
    messages = list(history) + [{"role": "user", "content": user_message}]
    trace = []

    for iteration in range(max_iterations):
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS_ANTHROPIC,
            messages=messages,
        )
        step = {"iteration": iteration + 1, "tool_calls": [], "text": ""}
        for block in response.content:
            if block.type == "text":
                step["text"] = block.text
            elif block.type == "tool_use":
                step["tool_calls"].append({"name": block.name, "inputs": block.input})
        trace.append(step)

        if response.stop_reason == "end_turn":
            break

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": run_tool(block.name, block.input),
                })
        messages.append({"role": "user", "content": tool_results})

    final_answer = trace[-1].get("text", "Investigation complete.")
    return {"answer": final_answer, "trace": trace, "iterations": len(trace),
            "provider": "claude", "model": "claude-opus-4-5"}


def agent_loop(user_message: str, history: list = None, max_iterations: int = 10) -> dict:
    """Route to the configured LLM provider."""
    h = history or []
    if LLM_PROVIDER == "claude":
        return agent_loop_claude(user_message, h, max_iterations)
    return agent_loop_groq(user_message, h, max_iterations)


# ─── API endpoints ─────────────────────────────────────────────────────────────

class InvestigateRequest(BaseModel):
    question: str
    history: list = []

@app.post("/investigate")
async def investigate(req: InvestigateRequest):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")
    return agent_loop(req.question, history=req.history)

@app.get("/health")
async def health():
    try:
        summary = get_index_summary()
        return {"status": "ok", "splunk_indexes": len(summary["indexes"]),
                "llm_provider": LLM_PROVIDER}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@app.get("/")
async def root():
    return {"name": "Splunk Ops Brain", "status": "running",
            "provider": LLM_PROVIDER, "docs": "/docs"}
