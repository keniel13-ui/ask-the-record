---
name: check-live-parity
description: Check whether ask-the-record.vercel.app is serving the web/api-ask.py in this checkout, and whether the web copy of the loop has drifted from the graded CLI. Use before claiming the live site reflects a change, or after editing either ask file.
---

# Check live parity

This makes free, read-only calls only. Never POST to the live site: every POST bills the owner's
Gemini project.

1. Compare the deployed source with the local file:

   ```bash
   curl -s https://ask-the-record.vercel.app/api/ask | python3 -c 'import json,sys; print(json.load(sys.stdin)["source_sha256"])'
   shasum -a 256 web/api-ask.py
   ```

   If they match, the live site serves this file. If they differ, say so plainly. The deploy
   step is outside this repo, so a merged change is not live until the owner redeploys.

2. Compare the CLI and web copies. Check these items and report any difference:
   - `QUESTIONS` (web) against `FROZEN_QUESTIONS` (CLI)
   - `MODEL`, `KB`, `SNAPSHOT_DATE`, `MAX_TOOL_CALLS`, `VERDICTS`, both endpoints
   - `route()`
   - the contract checks: `contract_violations()` (web) against the checks in `ask()` (CLI)
   - `OUTPUT_RULES` hard rules

   If `tests/test_contract.py` exists, `python3 -m unittest discover -s tests` covers the
   constants and routing. The checks still need a read.

3. Compare the CLI with the last graded copy:

   ```bash
   shasum -a 256 harness/ask.py evidence/harness-graded-v8-2026-09-21-aethar/ask.py
   ```

   A mismatch means no graded run covers the current CLI. Report it. Never edit `evidence/`.
