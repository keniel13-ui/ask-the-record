# CLAUDE.md

Guidance for Claude Code (and other agents) working in this repository.

## What this repo is

An agent over a public Sanity record of published work and the defects other engineers found in
it. It answers only from retrieved evidence or returns `VERDICT: INSUFFICIENT_EVIDENCE`. It is also
an **evidence bundle**: the graded runs under `evidence/` back claims made in `README.md`.

- `harness/ask.py`: the graded CLI. It is built to `harness/CONTRACT.md`.
- `web/api-ask.py`: the Vercel function behind https://ask-the-record.vercel.app, served as
  `api/ask.py` per `web/vercel.json`. `web/index.html` is the static page.
- `studio/`: the Sanity Studio schema (TypeScript) for project `u58x3mt0`.
- `evidence/`: verdicts, transcripts, and frozen copies of each graded `ask.py`.

See `docs/ARCHITECTURE.md` for the loop and `docs/DEVELOPMENT.md` for the verified commands.

## Rules that matter here

- **Never edit `evidence/` or `harness/CONTRACT.md`.** They are historical records. The contract
  changes only through a dated amendment written by its owner. If something there looks wrong,
  say so in the PR. Do not fix it.
- **Do not change README claims about graded runs** (counts, verdicts, which version held) unless
  a new graded run supports the change. Correcting a statement that has become stale is fine, if
  the PR says so.
- **Two copies, one contract.** `harness/ask.py` and `web/api-ask.py` duplicate the routing, the
  output rules and the contract checks on purpose: the web file is deployed standalone. When you
  change a check in one, change the other, or explain why not.
- **Fail closed.** Any retrieval or transport problem must raise `Failure`. A failure never
  degrades into a model-written answer, and a verdict that does not parse is a violation, never an
  exemption.
- **Standard library only** for the Python. Do not add runtime dependencies.
- **No paid calls, no real keys.** Tests mock `urllib.request.urlopen`. Never run
  `harness/ask.py` with real credentials, or POST to the live site, unless the owner asks. Each
  call bills the owner's Gemini project.
- **Deployment.** Do not rename `web/api-ask.py` or edit `web/vercel.json` without a reason. Any
  change to `web/api-ask.py` changes the `source_sha256` that `GET /api/ask` reports, so the live
  hash will differ from `main` until someone redeploys.
- The throttle in `web/api-ask.py` is per instance. It is documented as a speed bump. Do not
  describe it as a rate limit.

## Commands

```bash
python3 harness/ask.py                       # usage, exit 2
python3 -m py_compile harness/ask.py web/api-ask.py
cd studio && npm install && npx tsc --noEmit -p .
```

Tests and lint arrive with the `improve/testing-and-ci` PR
(`python3 -m unittest discover -s tests -v`, `ruff check .`).
