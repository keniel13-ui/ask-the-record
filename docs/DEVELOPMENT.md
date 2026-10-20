# Local development

Every command here was run on macOS with Python 3.9.6 and Node 22.14.0. None of them calls a paid
API.

## CLI harness (`harness/ask.py`)

Standard library only, so there is nothing to install.

```bash
python3 harness/ask.py              # prints usage, exits 2
python3 harness/ask.py "Who found finding B1, and what is the comment id?"
```

Without credentials the second command prints
`FAILURE: SANITY_CONTEXT_TOKEN not found in ~/.kairos_env or ~/.env` and exits 1. A real run needs
`SANITY_CONTEXT_TOKEN` (org-level, Context Viewer) and `GEMINI_PAID_KEY` or `GEMINI_API_KEY`,
either exported or in `~/.kairos_env` / `~/.env`. A real run calls Gemini and is billed to that
key.

## Serverless function (`web/api-ask.py`)

You can serve the handler with the standard library for a local check. This serves the API only,
not `index.html`.

```bash
python3 -c 'import importlib.util; from http.server import HTTPServer; s = importlib.util.spec_from_file_location("ask", "web/api-ask.py"); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); HTTPServer(("127.0.0.1", 8787), m.handler).serve_forever()'
```

In a second terminal:

```bash
curl -s http://127.0.0.1:8787/api/ask            # questions, model, snapshot, source_sha256
curl -s -X POST -H 'Content-Type: application/json' -d '{"index":0}' http://127.0.0.1:8787/api/ask
```

With no credentials in the environment, the POST returns
`{"error": "server is missing credentials"}` and makes no upstream call.

### Is the live site serving this source?

`GET /api/ask` reports the sha256 of the deployed file. Compare it with your checkout:

```bash
curl -s https://ask-the-record.vercel.app/api/ask | python3 -c 'import json,sys; print(json.load(sys.stdin)["source_sha256"])'
shasum -a 256 web/api-ask.py
```

On 2026-10-19 both printed `a345f0f143fb8290de70ff6c642546d39bd1307617a1d5f1b4e999d8da547b4f`.
The deploy step that publishes `web/api-ask.py` as `api/ask.py` is not in this repo.

## Studio (`studio/`)

```bash
cd studio
npm install
npx tsc --noEmit -p .     # typechecks the schema (exit 0)
npm run dev               # sanity dev, needs a Sanity login
```

No lockfile is committed, so `npm install` resolves the caret ranges in `package.json` at
install time.

## Tests

There are no tests on `main` at the time of writing. The open PR `improve/testing-and-ci` adds
`python3 -m unittest discover -s tests -v`, with every provider mocked.
