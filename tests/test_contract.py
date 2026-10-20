"""Tests for the output-contract guards and the retrieval loop as they exist.

All providers (Sanity Context MCP, Gemini) are mocked at urllib.request.urlopen.
No network, no real keys. Run: python3 -m unittest discover -s tests -v
"""
import io
import json
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from helpers import (FakeNet, gemini_call, gemini_text, http_error,  # noqa: E402
                     load_cli, load_web, mcp_ok)

web = load_web()
cli = load_cli()


def answer(verdict="STANDING", sources="claim_registry/b_series kbjnxAgyAimV",
           ans="Finding B8 has 1 locatable comment.", unc="snapshot only."):
    return (f"ANSWER: {ans}\nSOURCES: {sources}\nEVIDENCE DATE: 2026-09-19\n"
            f"VERDICT: {verdict}\nUNCERTAINTY: {unc}")


B8 = "How many locatable comments support finding B8?"


class Parity(unittest.TestCase):
    """The page claims to run the same loop as the graded CLI. Drift between the
    two copies would make that claim false without any test noticing."""

    def test_same_questions_and_constants(self):
        self.assertEqual(web.QUESTIONS, cli.FROZEN_QUESTIONS)
        for name in ("MODEL", "KB", "TEMPERATURE", "MAX_TOOL_CALLS", "SNAPSHOT_DATE",
                     "VERDICTS", "KB_ENDPOINT", "DATA_ENDPOINT"):
            self.assertEqual(getattr(web, name), getattr(cli, name), name)

    def test_routing_agrees(self):
        for q in web.QUESTIONS + ["is claim-foo-bar standing?", "what about A3?"]:
            self.assertEqual(web.route(q), cli.route(q), q)


class Routing(unittest.TestCase):
    def test_only_claim_ids_go_to_the_dataset(self):
        self.assertEqual(web.route("What is the status and expiry of claim-ledger-population?"), "data")
        for q in ("Who found finding B1, and what is the comment id?",
                  "Is the patch for finding B1 merged into origin/main?",
                  "How many of the 74 articles mention Kubernetes?",
                  "what does the word claim mean?"):
            self.assertEqual(web.route(q), "kb", q)


class VerdictParsing(unittest.TestCase):
    """A verdict that does not parse must be a violation, never an exemption."""

    def check(self, text):
        return web.contract_violations(text, "kb", "finding B8", 1, B8)

    def test_clean_answer_passes(self):
        self.assertEqual(self.check(answer()), [])

    def test_lowercase_or_unknown_verdict_is_a_violation(self):
        for bad in ("standing", "123", "PROBABLY"):
            v = self.check(answer(verdict=bad))
            self.assertTrue(any("is not a contract value" in x for x in v), (bad, v))

    def test_empty_verdict(self):
        self.assertIn("VERDICT is empty", self.check(answer(verdict="")))

    def test_two_verdict_lines(self):
        v = self.check(answer() + "\nVERDICT: RETRACTED")
        self.assertIn("expected exactly one VERDICT line, found 2", v)

    def test_bad_verdict_still_runs_evidence_checks(self):
        v = web.contract_violations(answer(verdict="maybe", sources="none"), "kb", "", 0, B8)
        self.assertIn("answered with a verdict but read nothing", v)
        self.assertIn("KB answer cites no entry path", v)

    def test_cli_matches_web_on_bad_verdict(self):
        def fake_gemini(*a, **k):
            return answer(verdict="standing"), ["claim_registry/b_series"]
        state = {"id": 100, "reads": 1, "retrieved": "finding B8"}
        with mock.patch.object(cli, "gemini", fake_gemini):
            _, _, v, inst = cli.ask(B8, {"_model_key": "t"}, {"kb": "", "data": ""}, "tok", state)
        self.assertEqual(inst, "kb")
        self.assertTrue(any("is not a contract value" in x for x in v), v)


class RequiredFields(unittest.TestCase):
    def test_missing_fields_and_empty_uncertainty(self):
        v = web.contract_violations("ANSWER: yes\nVERDICT: STANDING\nUNCERTAINTY:   ",
                                    "kb", "B8", 1, B8)
        self.assertIn("missing field SOURCES", v)
        self.assertIn("missing field EVIDENCE DATE", v)
        self.assertIn("UNCERTAINTY is empty", v)

    def test_kb_answer_must_cite_kb_id(self):
        v = web.contract_violations(answer(sources="claim_registry/b_series"), "kb", "B8", 1, B8)
        self.assertIn("KB answer does not cite the knowledge base id", v)

    def test_data_answer_must_carry_a_url(self):
        q = "What is the status and expiry of claim-ledger-population?"
        a = answer(ans="claim-ledger-population is standing", sources="claim-ledger-population")
        v = web.contract_violations(a, "data", "claim-ledger-population", 1, q)
        self.assertIn("dataset answer carries no resolvable URL", v)

    def test_insufficient_evidence_with_no_reads_is_allowed(self):
        a = answer(verdict="INSUFFICIENT_EVIDENCE", sources="none",
                   ans="The record does not contain it.")
        self.assertEqual(web.contract_violations(a, "kb", "", 0, web.QUESTIONS[4]), [])


class RetrievalFailuresNeverReachTheModel(unittest.TestCase):
    def test_is_error_and_empty_retrieval_are_failures(self):
        for mod in (web, cli):
            with self.assertRaisesRegex(mod.Failure, "isError"):
                mod.mcp_text({"isError": True, "content": [{"text": "boom"}]}, "t")
            with self.assertRaisesRegex(mod.Failure, "empty retrieval"):
                mod.mcp_text({"content": [{"text": "   "}]}, "t")

    def test_jsonrpc_error_is_a_failure(self):
        for mod in (web, cli):
            net = FakeNet([{"jsonrpc": "2.0", "id": 1, "error": {"code": -32601}}])
            with mock.patch.object(mod.urllib.request, "urlopen", net):
                with self.assertRaisesRegex(mod.Failure, "-32601"):
                    mod.mcp("tok", "tools/list", {}, 1, "https://x.test")

    def test_non_retryable_http_error_fails_once(self):
        for mod in (web, cli):
            net = FakeNet([http_error("https://x.test", 401)])
            with mock.patch.object(mod.urllib.request, "urlopen", net), \
                    mock.patch.object(mod.time, "sleep"):
                with self.assertRaisesRegex(mod.Failure, "HTTP 401"):
                    mod.post("https://x.test", {}, {}, "MCP initialize")
            self.assertEqual(len(net.requests), 1)

    def test_retry_is_bounded(self):
        net = FakeNet([http_error("https://x.test", 503)] * 3)
        with mock.patch.object(web.urllib.request, "urlopen", net), \
                mock.patch.object(web.time, "sleep"):
            with self.assertRaisesRegex(web.Failure, "HTTP 503"):
                web.post("https://x.test", {}, {}, "model")
        self.assertEqual(len(net.requests), 3)

    def test_web_run_aborts_on_failed_tool_call(self):
        err = {"jsonrpc": "2.0", "id": 11,
               "result": {"isError": True, "content": [{"text": "bad path"}]}}
        net = FakeNet([mcp_ok("{}"), mcp_ok("outline"),
                       gemini_call("knowledge_base_read", {"paths": ["x/y"]}), err])
        with mock.patch.object(web.urllib.request, "urlopen", net):
            with self.assertRaisesRegex(web.Failure, "isError"):
                web.run(B8, "s", "g")
        self.assertEqual(len(net.requests), 4, "no model call after a failed retrieval")


class CallBudget(unittest.TestCase):
    def test_web_budget_exhaustion_is_insufficient_evidence(self):
        script = [mcp_ok("{}"), mcp_ok("outline")]
        for i in range(web.MAX_TOOL_CALLS):
            script += [gemini_call("knowledge_base_read", {"paths": [f"a/p{i}"]}), mcp_ok(f"entry {i}")]
        script.append(gemini_call("knowledge_base_read", {"paths": ["a/one-too-many"]}))
        net = FakeNet(script)
        with mock.patch.object(web.urllib.request, "urlopen", net):
            out = web.run(B8, "s", "g")
        self.assertIn("VERDICT: INSUFFICIENT_EVIDENCE", out["answer"])
        self.assertEqual(out["violations"], ["call budget exhausted before an answer"])
        self.assertEqual(net.script, [])

    def test_cli_budget_exhaustion_is_insufficient_evidence(self):
        many = gemini_call("knowledge_base_read", {"paths": ["a/b"]})
        many["candidates"][0]["content"]["parts"] *= cli.MAX_TOOL_CALLS + 1
        net = FakeNet([many])
        with mock.patch.object(cli.urllib.request, "urlopen", net):
            text, _ = cli.gemini("k", "ctx", B8, lambda a: "x", cli.READ_TOOL, "")
        self.assertIn("VERDICT: INSUFFICIENT_EVIDENCE", text)

    def test_empty_tool_arguments_are_failures(self):
        net = FakeNet([mcp_ok("{}"), mcp_ok("ctx"), gemini_call("groq_query", {})])
        with mock.patch.object(web.urllib.request, "urlopen", net):
            with self.assertRaisesRegex(web.Failure, "no query"):
                web.run(web.QUESTIONS[3], "s", "g")


class CliMain(unittest.TestCase):
    def test_missing_tool_on_endpoint_is_failure_exit_1(self):
        net = FakeNet([mcp_ok("{}"), {"jsonrpc": "2.0", "id": 2,
                                      "result": {"tools": [{"name": "initial_context"}]}}])
        out = io.StringIO()
        with mock.patch.object(cli, "load_keys", return_value={
                "SANITY_CONTEXT_TOKEN": "s", "_model_key": "g", "_key_tier": "test"}), \
                mock.patch.object(cli.urllib.request, "urlopen", net), \
                mock.patch.object(sys, "argv", ["ask.py", "q"]), \
                mock.patch("sys.stdout", out):
            self.assertEqual(cli.main(), 1)
        self.assertIn("FAILURE: kb endpoint does not serve knowledge_base_read", out.getvalue())

    def test_no_arguments_prints_usage_exit_2(self):
        with mock.patch.object(sys, "argv", ["ask.py"]), mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(cli.main(), 2)


class WebHandler(unittest.TestCase):
    def setUp(self):
        web._HITS.clear()
        web._DAY.update(date=None, n=0)

    def make(self, body):
        h = web.handler.__new__(web.handler)
        h.headers = {"Content-Length": str(len(body))}
        h.rfile = io.BytesIO(body)
        h.sent = []
        h._send = lambda code, payload: h.sent.append((code, payload))
        return h

    def test_get_reports_questions_and_source_hash(self):
        h = self.make(b"")
        h.do_GET()
        code, payload = h.sent[0]
        self.assertEqual(code, 200)
        self.assertEqual(payload["questions"], web.QUESTIONS)
        self.assertRegex(payload["source_sha256"], r"^[0-9a-f]{64}$")

    def test_out_of_range_index_is_400(self):
        for body in (b'{"index": 5}', b'{"index": -1}', b'{"index": "0"}', b"{}"):
            h = self.make(body)
            h.do_POST()
            self.assertEqual(h.sent[0][0], 400, body)

    def test_missing_credentials_is_500_without_model_call(self):
        h = self.make(b'{"index": 0}')
        with mock.patch.dict(web.os.environ, {}, clear=True), \
                mock.patch.object(web, "run") as run:
            h.do_POST()
        self.assertEqual(h.sent[0], (500, {"error": "server is missing credentials"}))
        run.assert_not_called()

    def test_upstream_failure_is_502_no_answer(self):
        h = self.make(b'{"index": 0}')
        with mock.patch.dict(web.os.environ, {"SANITY_CONTEXT_TOKEN": "s", "GEMINI_PAID_KEY": "g"}), \
                mock.patch.object(web, "run", side_effect=web.Failure("MCP initialize: HTTP 401")):
            h.do_POST()
        code, payload = h.sent[0]
        self.assertEqual(code, 502)
        self.assertEqual(payload["note"], "no answer was generated")

    def test_throttle_trips_on_seventh_request_in_window(self):
        for _ in range(web.MAX_PER_WINDOW):
            web.throttle()
        with self.assertRaisesRegex(web.Failure, "^rate limit"):
            web.throttle()

    def test_successful_answer_payload_shape(self):
        final = answer()
        net = FakeNet([mcp_ok("{}"), mcp_ok("outline"),
                       gemini_call("knowledge_base_read", {"paths": ["claim_registry/b_series"]}),
                       mcp_ok("finding B8: 1 locatable comment"), gemini_text(final)])
        h = self.make(b'{"index": 2}')
        with mock.patch.dict(web.os.environ, {"SANITY_CONTEXT_TOKEN": "s", "GEMINI_PAID_KEY": "g"}), \
                mock.patch.object(web.urllib.request, "urlopen", net):
            h.do_POST()
        code, payload = h.sent[0]
        self.assertEqual(code, 200)
        self.assertEqual(payload["question"], B8)
        self.assertEqual(payload["violations"], [])
        self.assertEqual(payload["read"], ["claim_registry/b_series"])
        sent = json.loads(net.requests[3].data)
        self.assertEqual(sent["params"]["arguments"]["knowledgeBase"], web.KB)


if __name__ == "__main__":
    unittest.main()
