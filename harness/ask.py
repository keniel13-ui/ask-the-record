#!/usr/bin/env python3
"""ask.py — a question goes in, a bounded answer comes out.

Reads ONLY from the Sanity Context MCP endpoint. No local corpus, no cache, no
fallback to the model's own knowledge. Built to HARNESS_CONTRACT_2026-09-20.md
(frozen c7aeec04..., Amendment 1, custody A10).

    python3 ask.py "Who found finding B1, and what is the comment id?"
    python3 ask.py --all        # run the five frozen questions

Every answer carries ANSWER / SOURCES / EVIDENCE DATE / VERDICT / UNCERTAINTY.
Auth or retrieval failure prints FAILURE and exits non-zero. It never degrades
into a model-written answer.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

KB_ENDPOINT = ("https://api.sanity.io/v1/context/organizations/"
               "od9141taf/mcp/self-correcting-systems")
DATA_ENDPOINT = ("https://api.sanity.io/v1/context/organizations/"
                 "od9141taf/mcp/self-correcting-systems-data")


def route(question):
    """A19 (Aethar, breaker). Dataset ONLY for claim-* document-field questions.
    B-codes, A-codes, shas and Forem ids stay on the Knowledge Base — routing them
    to GROQ would leave Path One demonstrating KB mode on an abstention."""
    return "data" if re.search(r"\bclaim-[a-z0-9-]+", question) else "kb"


KB = "kbjnxAgyAimV"
MODEL = "gemini-3.6-flash"        # A17: per-PROJECT catalogue; billed project serves 3.6
TEMPERATURE = 0
MAX_TOOL_CALLS = 6                # A2: exceeded -> INSUFFICIENT_EVIDENCE, never a guess
VERDICTS = ("STANDING", "RETRACTED", "SUPERSEDED", "UNBUILT", "EXPIRED",
            "NO_EXPIRY_SET", "INSUFFICIENT_EVIDENCE")
SNAPSHOT_DATE = "2026-09-20"      # A7: asserted by the harness; the endpoint does not prove it

FROZEN_QUESTIONS = [
    "Who found finding B1, and what is the comment id?",
    "Is the patch for finding B1 merged into origin/main?",
    "How many locatable comments support finding B8?",
    "What is the status and expiry of claim-ledger-population?",
    "How many of the 74 articles mention Kubernetes?",
]


class Failure(Exception):
    """Anything that must print FAILURE rather than an answer."""


def load_keys():
    """Exported environment variables win; the dotfiles are a fallback.
    The published README documents `export`, and before 2026-09-21 this
    function ignored the environment entirely."""
    keys = {}
    for name in ("SANITY_CONTEXT_TOKEN", "GEMINI_PAID_KEY", "GEMINI_API_KEY"):
        if os.environ.get(name):
            keys[name] = os.environ[name]
    for path in ("~/.kairos_env", "~/.env"):
        p = os.path.expanduser(path)
        if not os.path.exists(p):
            continue
        for line in open(p):
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                keys.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    if not keys.get("SANITY_CONTEXT_TOKEN"):
        raise Failure("SANITY_CONTEXT_TOKEN not found in ~/.kairos_env or ~/.env")
    # A16: prefer the billed key; record which tier a graded run actually used
    if keys.get("GEMINI_PAID_KEY"):
        keys["_model_key"] = keys["GEMINI_PAID_KEY"]
        keys["_key_tier"] = "GEMINI_PAID_KEY (billed)"
    elif keys.get("GEMINI_API_KEY"):
        keys["_model_key"] = keys["GEMINI_API_KEY"]
        keys["_key_tier"] = "GEMINI_API_KEY (free tier — rate limited)"
    else:
        raise Failure("no Gemini key found in ~/.kairos_env or ~/.env")
    return keys


RETRY_CODES = (429, 500, 502, 503, 504)
RETRIES = 4


def post(url, payload, headers, what):
    """Retries transient upstream failures. An exhausted retry is still a FAILURE —
    it never degrades into an answer."""
    body = json.dumps(payload).encode()
    last = None
    for attempt in range(RETRIES):
        req = urllib.request.Request(url, data=body, method="POST")
        for k, v in headers.items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode(errors="replace")
            last = f"{what}: HTTP {e.code} — {detail}"
            if e.code not in RETRY_CODES or attempt == RETRIES - 1:
                raise Failure(last)
            wait = 5 * (2 ** attempt)
            print(f"  [retry {attempt + 1}/{RETRIES - 1}] {what} HTTP {e.code}, "
                  f"waiting {wait}s", file=sys.stderr)
            time.sleep(wait)
        except Exception as e:
            last = f"{what}: {e}"
            if attempt == RETRIES - 1:
                raise Failure(last)
            time.sleep(5 * (2 ** attempt))
    raise Failure(last or f"{what}: exhausted retries")


def mcp(token, method, params, req_id, endpoint):
    d = post(endpoint, {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params},
             {"Authorization": f"Bearer {token}",
              "Content-Type": "application/json",
              "Accept": "application/json, text/event-stream"},
             f"MCP {method}")
    if "error" in d:
        raise Failure(f"MCP {method}: {json.dumps(d['error'])[:300]}")
    return d["result"]


def mcp_text(result, what="MCP tool"):
    """R1: a tool result flagged isError is a FAILURE. It is never handed to the model."""
    if result.get("isError"):
        body = "".join(c.get("text", "") for c in result.get("content", []))
        raise Failure(f"{what}: endpoint returned isError — {body[:300]}")
    text = "".join(c.get("text", "") for c in result.get("content", []))
    if not text.strip():
        raise Failure(f"{what}: empty retrieval")
    return text


# ---------------------------------------------------------------- Gemini

READ_TOOL = {
    "name": "knowledge_base_read",
    "description": ("Read full entries from the knowledge base by path. Paths come verbatim "
                    "from the outline in the context block. Read several related entries in "
                    "one call rather than many round-trips."),
    "parameters": {
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Entry paths taken verbatim from the outline.",
            }
        },
        "required": ["paths"],
    },
}

GROQ_TOOL = {
    "name": "groq_query",
    "description": (
        "Query the dataset with GROQ. Schema types & verified fields: "
        "- 'claim': _id, text, status, expiryStatus, sourceUrl "
        "- 'finding': _id, title, foundBy, commentIds, status "
        "- 'patch': _id, findings[]._ref, sha, inMain. "
        "Canonical query patterns: "
        "*[_type == 'claim' && _id == 'claim-ledger-population'][0]{status, expiryStatus}, "
        "*[_type == 'patch' && 'finding-B1' in findings[]._ref][0]{sha, inMain}, "
        "*[_type == 'finding' && _id == 'finding-B8'][0]{title, commentIds, status}"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A valid GROQ query projecting exact document fields.",
            }
        },
        "required": ["query"],
    },
}

OUTPUT_RULES = f"""
You are answering ONE question. You have no conversation history.

Use knowledge_base_read to fetch the entries you judge relevant. You choose the paths — nothing
selected them for you. You may call it at most {MAX_TOOL_CALLS} times.

Then answer in EXACTLY this shape, these five labels, nothing before or after:

ANSWER: <the claim, stated plainly>
SOURCES: <entry path(s) you read, and any source URLs carried in the entry text>
EVIDENCE DATE: <the asOf or date recorded IN the entry. Never today's date. If the entry
carries no date, write: none recorded in the entry>
VERDICT: <one of STANDING, RETRACTED, SUPERSEDED, UNBUILT, EXPIRED, NO_EXPIRY_SET,
INSUFFICIENT_EVIDENCE>
UNCERTAINTY: <what this answer cannot establish. Never empty.>

Hard rules:

- If the retrieved entries do not answer the question, VERDICT is INSUFFICIENT_EVIDENCE and
  ANSWER says the record does not contain it. Do not answer from your own knowledge. Do not
  estimate. A number you did not read is a fabrication.
- Status and expiry are SEPARATE facts. A claim can be STANDING and have no expiry set. Report
  both; never collapse one into the other.
- Anything about mutable external state — whether a branch merged, whether an article changed,
  whether a person replied — is a SNAPSHOT CLAIM. Say so, give the snapshot date
  ({SNAPSHOT_DATE}), and say the record cannot see changes after it.
- Report the number of receipts that exist, not the number claimed.
"""


def gemini(key, system_text, question, call_tool, tool_decl, extra_rules):
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{MODEL}:generateContent?key={key}")
    contents = [{"role": "user", "parts": [{"text": question}]}]
    calls = 0
    trace = []

    while True:
        payload = {
            "systemInstruction": {"parts": [{"text": system_text + OUTPUT_RULES + extra_rules}]},
            "contents": contents,
            "tools": [{"functionDeclarations": [tool_decl]}],
            "generationConfig": {"temperature": TEMPERATURE},
        }
        d = post(url, payload, {"Content-Type": "application/json"}, "Gemini")
        cands = d.get("candidates") or []
        if not cands:
            raise Failure(f"Gemini returned no candidates: {json.dumps(d)[:300]}")
        parts = cands[0].get("content", {}).get("parts", []) or []

        fcs = [p["functionCall"] for p in parts if "functionCall" in p]
        if not fcs:
            text = "".join(p.get("text", "") for p in parts if "text" in p).strip()
            if not text:
                raise Failure("Gemini returned an empty answer")
            return text, trace

        if calls + len(fcs) > MAX_TOOL_CALLS:
            return (f"ANSWER: The record was not resolved within the {MAX_TOOL_CALLS}-call "
                    f"budget, so no answer is given.\nSOURCES: {', '.join(trace) or 'none'}\n"
                    f"EVIDENCE DATE: none recorded in the entry\n"
                    f"VERDICT: INSUFFICIENT_EVIDENCE\n"
                    f"UNCERTAINTY: The budget stopped retrieval before the question was "
                    f"answered. This is a harness limit, not evidence the record is silent.",
                    trace)

        contents.append({"role": "model", "parts": parts})
        responses = []
        for fc in fcs:
            calls += 1
            args = fc.get("args") or {}
            label = args.get("query") or ", ".join(args.get("paths") or [])
            trace.append(label)
            responses.append({"functionResponse": {
                "name": fc["name"],
                "response": {"content": call_tool(args)},
            }})
        contents.append({"role": "user", "parts": responses})


# ---------------------------------------------------------------- run

def ask(question, keys, contexts, token, state):
    """A19: one instrument per question. Never both tool sets on one question."""
    instrument = route(question)

    if instrument == "data":
        endpoint, tool_decl = DATA_ENDPOINT, GROQ_TOOL
        extra = (
            "\n\nThis endpoint queries the dataset directly with GROQ.\n"
            "Schema & Type Routing Rules:\n"
            "- Questions naming 'claim-*' MUST query _type == 'claim' by exact _id.\n"
            "- Questions about patch/merge MUST query _type == 'patch' filtering 'finding-<ID>' in findings[]._ref.\n"
            "- SOURCES must include the document's sourceUrl field — a resolvable http(s) URL.\n"
        )

        def call_tool(args):
            q = (args or {}).get("query")
            if not q:
                raise Failure("model called groq_query with no query")
            state["id"] += 1
            text = mcp_text(mcp(token, "tools/call",
                                {"name": "groq_query", "arguments": {"query": q}},
                                state["id"], endpoint), f"groq_query {q[:60]}")
            state["reads"] += 1
            state["retrieved"] += text
            return text
    else:
        endpoint, tool_decl = KB_ENDPOINT, READ_TOOL
        extra = (f"\n\nThis endpoint serves a Knowledge Base. SOURCES must cite the entry "
                 f"path(s) you read and the knowledge base id {KB}. A per-claim URL is not "
                 f"available in this instrument; do not invent one.\n")

        def call_tool(args):
            paths = (args or {}).get("paths") or []
            if not paths:
                raise Failure("model called knowledge_base_read with no paths")
            state["id"] += 1
            text = mcp_text(mcp(token, "tools/call",
                                {"name": "knowledge_base_read",
                                 "arguments": {"knowledgeBase": KB, "paths": paths}},
                                state["id"], endpoint), f"knowledge_base_read {paths}")
            state["reads"] += 1
            state["retrieved"] += text
            return text

    answer, trace = gemini(keys["_model_key"], contexts[instrument], question,
                           call_tool, tool_decl, extra)
    violations = []

    missing = [f for f in ("ANSWER:", "SOURCES:", "EVIDENCE DATE:", "VERDICT:", "UNCERTAINTY:")
               if f not in answer]
    if missing:
        violations.append(f"missing field(s): {', '.join(missing)}")

    unc = re.search(r"UNCERTAINTY:\s*(.*)", answer, re.S)
    if unc is not None and not unc.group(1).strip():
        violations.append("UNCERTAINTY is empty")

    # A verdict that does not parse is a VIOLATION, never an exemption.
    lines = re.findall(r"^[ \t]*VERDICT:[ \t]*(.*)$", answer, re.M)
    v = None
    if len(lines) != 1:
        violations.append(f"expected exactly one VERDICT line, found {len(lines)}")
    else:
        raw = lines[0].strip()
        if not raw:
            violations.append("VERDICT is empty")
        elif raw not in VERDICTS:
            violations.append(f"VERDICT {raw[:40]!r} is not a contract value")
        else:
            v = raw
    evidence_bearing = v != "INSUFFICIENT_EVIDENCE"

    if evidence_bearing and state["reads"] == 0:
        violations.append("answered with a verdict but read zero entries")

    # R7 keys off the INSTRUMENT (A19), not off every answer
    src = re.search(r"SOURCES:\s*(.*?)(?=\nEVIDENCE DATE:|$)", answer, re.S)
    src_text = src.group(1) if src else ""
    if evidence_bearing:
        if instrument == "data" and not re.search(r"https?://\S+", src_text):
            violations.append("dataset answer carries no resolvable URL (A19/R7)")
        if instrument == "kb":
            if not re.search(r"\b[a-z0-9_]+/[a-z0-9_]+", src_text):
                violations.append("KB answer cites no entry path (A19/R7)")
            if KB not in src_text:
                violations.append(f"KB answer does not cite the kb id {KB} (A19/R7)")

    for ident in re.findall(r"\b(claim-[a-z0-9-]+|[AB]\d+)\b", question):
        if evidence_bearing and ident not in state["retrieved"]:
            violations.append(f"question names '{ident}' but it is absent from retrieved text")
        if evidence_bearing and ident not in answer:
            violations.append(f"question names '{ident}' but the answer never names it (R8)")

    if violations:
        answer += "\n\n[HARNESS] CONTRACT VIOLATION — " + "; ".join(violations)
    return answer, trace, violations, instrument


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    questions = FROZEN_QUESTIONS if args[0] == "--all" else [" ".join(args)]

    try:
        keys = load_keys()
        token = keys["SANITY_CONTEXT_TOKEN"]
        state = {"id": 100, "reads": 0, "retrieved": ""}
        contexts = {}
        for name, ep, need in (("kb", KB_ENDPOINT, ("initial_context", "knowledge_base_read")),
                               ("data", DATA_ENDPOINT, ("initial_context", "groq_query"))):
            mcp(token, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                      "clientInfo": {"name": "ask.py", "version": "2.0"}}, 1, ep)
            served = [t["name"] for t in mcp(token, "tools/list", {}, 2, ep).get("tools", [])]
            for needed in need:
                if needed not in served:
                    raise Failure(f"{name} endpoint does not serve {needed}; serves {served}")
            contexts[name] = mcp_text(mcp(token, "tools/call",
                                          {"name": "initial_context", "arguments": {}}, 3, ep),
                                      f"{name} initial_context")
    except Failure as e:
        print(f"FAILURE: {e}")
        return 1

    print(f"model {MODEL} · temp {TEMPERATURE} · snapshot {SNAPSHOT_DATE}")
    print(f"key: {keys['_key_tier']}")
    print(f"kb   endpoint · {len(contexts['kb']):,} chars context · kb {KB}")
    print(f"data endpoint · {len(contexts['data']):,} chars context · GROQ over production")
    print("routing: A19 — claim-* questions to dataset, everything else to the knowledge base\n")

    bad = 0
    for i, q in enumerate(questions, 1):
        state["reads"] = 0
        state["retrieved"] = ""
        print("=" * 78)
        print(f"Q{i}: {q}")
        print("=" * 78)
        try:
            answer, trace, violations, instrument = ask(q, keys, contexts, token, state)
        except Failure as e:
            print(f"FAILURE: {e}\n")
            bad += 1
            continue
        if violations:
            bad += 1
        print(f"[instrument: {instrument}]")
        print(answer)
        print(f"\n[read: {' | '.join(t[:70] for t in trace) if trace else 'none'}]\n")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
