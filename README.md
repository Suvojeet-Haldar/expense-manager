---
title: Live Expense Allocations
emoji: 💰
colorFrom: blue
colorTo: green
sdk: streamlit
sdk_version: 1.49.1
app_file: app.py
pinned: false
---

Live Expense Allocations — Streamlit app for tracking and allocating expenses.

This Space runs a Streamlit app (`app.py`).  
Secrets (MongoDB URI, authenticator hashes, cookie key, etc.) are stored in the Space **Settings → Secrets**.

**How to run locally**
```bash
# set your local env vars or keep a local config for dev, then:
streamlit run app.py