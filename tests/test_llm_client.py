import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pv.llm.client import OpenAICompat


class OpenAICompatConcurrency(unittest.TestCase):
    def test_zero_min_interval_allows_concurrent_requests(self):
        state = {"in_flight": 0, "max_in_flight": 0}
        lock = threading.Lock()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                with lock:
                    state["in_flight"] += 1
                    state["max_in_flight"] = max(state["max_in_flight"], state["in_flight"])
                time.sleep(0.1)
                body = json.dumps({
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                with lock:
                    state["in_flight"] -= 1

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        client = OpenAICompat(
            f"http://127.0.0.1:{server.server_port}/v1",
            "",
            "test-model",
            min_interval_s=0,
        )
        errors = []

        def call():
            try:
                client.chat([{"role": "user", "content": "hello"}], timeout=2)
            except Exception as exc:
                errors.append(exc)

        callers = [threading.Thread(target=call) for _ in range(2)]
        try:
            for caller in callers:
                caller.start()
            for caller in callers:
                caller.join(timeout=3)
        finally:
            server.shutdown()
            server_thread.join(timeout=2)
            server.server_close()

        self.assertFalse(errors)
        self.assertEqual(state["max_in_flight"], 2)


class OpenAICompatAuthorization(unittest.TestCase):
    def test_chat_sends_configured_bearer_token(self):
        received = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received["authorization"] = self.headers.get("Authorization")
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                body = json.dumps({
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = OpenAICompat(
                f"http://127.0.0.1:{server.server_port}/v1",
                "test-configured-key",
                "test-model",
            )
            result = client.chat([{"role": "user", "content": "hello"}], timeout=2)
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

        self.assertEqual(result.text, "ok")
        self.assertEqual(received["authorization"], "Bearer test-configured-key")
