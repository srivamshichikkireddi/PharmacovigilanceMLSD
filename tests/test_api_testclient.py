"""Runs only where FastAPI is installed (Docker image / your venv). Covers the HTTP layer for real."""
import json, unittest
try:
    from fastapi.testclient import TestClient
    HAVE = True
except Exception:  # pragma: no cover
    HAVE = False

from tests.fake_redis import FakeRedis
from tests.common import CASE41


@unittest.skipUnless(HAVE, "fastapi not installed")
class HTTP(unittest.TestCase):
    def setUp(self):
        import pv.api as api
        from pv.store import Store
        api._store = Store(FakeRedis()); self.api, self.c = api, TestClient(api.app)

    def test_submit_202_and_poll(self):
        r = self.c.post("/reports", json=CASE41, headers={"X-Client-Id": "t"}); self.assertEqual(r.status_code, 202)
        self.assertEqual(self.c.get(r.json()["status_url"]).json()["status"], "queued")

    def test_malformed_is_422_quarantined_and_starts_no_run(self):
        r = self.c.post("/reports", json=dict(CASE41, patient_age=-5)); self.assertEqual(r.status_code, 422)
        self.assertFalse(r.json()["agent_run_started"]); self.assertEqual(self.api.store().queue_depth(), 0)
        self.assertEqual(self.c.get(f"/quarantine/{r.json()['quarantine_id']}").status_code, 200)

    def test_contract_health_metrics(self):
        self.assertEqual(self.c.get("/health").json(), {"status": "ok"}); self.assertIn("properties", self.c.get("/contract").json())
        self.assertEqual(self.c.get("/metrics").status_code, 200)

    def test_feedback_validation(self):
        rid = self.c.post("/reports", json=CASE41).json()["run_id"]
        self.assertEqual(self.c.post(f"/runs/{rid}/feedback", json={"satisfactory": False}).status_code, 422)
        self.assertEqual(self.c.post(f"/runs/{rid}/feedback", json={"satisfactory": False, "reason": "wrong clause"}).status_code, 200)
        self.assertEqual(self.c.get("/feedback/summary").json()["total"], 1)
