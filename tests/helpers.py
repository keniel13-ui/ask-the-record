"""Shared test helpers. Stdlib only, no network, no real credentials.

`web/api-ask.py` has a hyphen in its name (Vercel serves it as api/ask.py), so
it is loaded by path rather than imported."""
import importlib.util
import io
import json
import pathlib
import urllib.error

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load(relpath, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_web():
    return load("web/api-ask.py", "api_ask_under_test")


def load_cli():
    return load("harness/ask.py", "ask_cli_under_test")


class FakeResponse:
    def __init__(self, body):
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode()

    def read(self, *a):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(url, code, body=b"upstream said no"):
    return urllib.error.HTTPError(url, code, "err", {}, io.BytesIO(body))


class FakeNet:
    """Stands in for urllib.request.urlopen. `script` is a list of responses
    (dict/bytes for a 200, or an exception instance to raise), consumed in order.
    Every Request is recorded so tests can inspect URLs, headers and bodies."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.timeouts = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        self.timeouts.append(timeout)
        if not self.script:
            raise AssertionError(f"unexpected request to {req.full_url}")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return FakeResponse(item)

    def payload(self, i):
        return json.loads(self.requests[i].data)


def mcp_ok(text):
    return {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": text}]}}


def gemini_text(text):
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}]}


def gemini_call(name, args):
    return {"candidates": [{"content": {"role": "model",
                                        "parts": [{"functionCall": {"name": name, "args": args}}]}}]}
