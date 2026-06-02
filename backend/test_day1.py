#!/usr/bin/env python3
"""
Day 1 smoke test — validates Splunk + Groq agent are working.
Usage: python scripts/test_day1.py  (from project root)
"""
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "backend", ".env"))

SPLUNK_HOST     = os.getenv("SPLUNK_HOST", "https://localhost:8089")
SPLUNK_USER     = os.getenv("SPLUNK_USER", "admin")
SPLUNK_PASSWORD = os.getenv("SPLUNK_PASSWORD", "changeme")
GROQ_API_KEY    = os.getenv("GROQ_API_KEY", "")
LLM_PROVIDER    = os.getenv("LLM_PROVIDER", "groq")

def check(label, fn):
    print(f"\n{'─'*52}\n  {label}\n{'─'*52}")
    try:
        r = fn()
        print(f"  ✓ PASS\n  {json.dumps(r)[:280]}")
        return True
    except Exception as e:
        print(f"  ✗ FAIL  →  {e}")
        return False

def test_splunk_connect():
    import requests, urllib3
    urllib3.disable_warnings()
    r = requests.get(f"{SPLUNK_HOST}/services/server/info",
                     auth=(SPLUNK_USER, SPLUNK_PASSWORD),
                     verify=False, params={"output_mode": "json"}, timeout=5)
    r.raise_for_status()
    info = r.json()["entry"][0]["content"]
    return {"splunk_version": info.get("version")}

def test_splunk_query():
    from main import splunk_search
    r = splunk_search("index=_internal | head 3 | table _time host sourcetype", earliest="-15m")
    return {"result_count": r["result_count"]}

def test_groq_api():
    from groq import Groq
    client = Groq(api_key=GROQ_API_KEY)
    r = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": "Reply with only: Ops Brain online."}],
        max_tokens=20,
    )
    return {"response": r.choices[0].message.content}

def test_agent_loop():
    from main import agent_loop
    r = agent_loop("List the available Splunk indexes and their event counts.")
    return {"iterations": r["iterations"],
            "tool_calls": sum(len(s["tool_calls"]) for s in r["trace"]),
            "provider": r.get("provider"),
            "answer_preview": r["answer"][:180]}

if __name__ == "__main__":
    tests = [
        ("Splunk connectivity",   test_splunk_connect),
        ("SPL query execution",   test_splunk_query),
        ("Groq API",              test_groq_api),
        ("Full agent loop",       test_agent_loop),
    ]
    passed = sum(check(l, f) for l, f in tests)
    print(f"\n{'═'*52}")
    print(f"  {passed}/{len(tests)} passed")
    print(f"{'═'*52}")
    if passed == len(tests):
        print("\n  Day 1 complete ✓  You're ready for Day 2.\n")
    else:
        print("\n  Fix the failing tests before moving on.\n")
    sys.exit(0 if passed == len(tests) else 1)
