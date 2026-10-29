"""LLM stub for load tests (D6) and the circuit-breaker demo (item 8). OpenAI-compatible, stdlib only.
Returns FIXED responses after a realistic delay, so a load test measures OUR system, not a provider's
rate limits. Admin toggle:  POST /admin {"mode": "ok" | "fail" | "slow", "delay": 1.2}"""
import json, os, random, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"mode": "ok", "delay": float(os.environ.get("STUB_DELAY_S", "1.2")), "calls": 0}
FIXED = {"terms": ["exfoliative dermatitis", "toxic epidermal necrolysis", "Stevens-Johnson syndrome"],
         "action": "continue", "summary": "Assessment drafted from the cited passages for review by a qualified person."}


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)

    def do_GET(self): self._send(200, {"status": "ok", **STATE})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); body = self.rfile.read(n).decode() if n else "{}"
        if self.path == "/admin":
            STATE.update(json.loads(body)); return self._send(200, STATE)
        STATE["calls"] += 1
        if STATE["mode"] == "fail":
            return self._send(503, {"error": "stub provider unavailable"})
        d = STATE["delay"] * (5 if STATE["mode"] == "slow" else 1)
        time.sleep(max(0.0, random.gauss(d, d * 0.15)))
        text = json.dumps(FIXED)
        self._send(200, {"choices": [{"message": {"role": "assistant", "content": text}}],
                         "usage": {"prompt_tokens": 420 + len(body) // 8, "completion_tokens": 70}})

    def log_message(self, *a): pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 9000), H).serve_forever()
