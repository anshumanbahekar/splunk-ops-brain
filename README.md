# Splunk Ops Brain

An agentic AI command center for Splunk. Ask it anything about your operational data — it autonomously investigates, runs SPL queries, correlates findings, and gives you answers like a senior SRE.

## Quick start

1. Install Splunk Enterprise free trial
2. cd backend && pip install -r requirements.txt
3. Copy .env.example to .env and fill in credentials
4. python scripts/test_day1.py
5. uvicorn main:app --reload

## License
MIT
