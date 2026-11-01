"""Load test (D6). Each virtual user submits a report and polls until the run is terminal; the
metric of interest is the 'E2E run' row = submission to FINAL RESULT, not the 202 response.

Stepped ramp finds the concurrency at which the SLO breaks (docs/SLO.md, declared BEFORE measuring).
Run:  docker compose -f docker-compose.yml -f docker-compose.loadtest.yml up --build --scale agent-worker=3 -d
      docker compose -f docker-compose.yml -f docker-compose.loadtest.yml run --rm locust \
         -f /mnt/locust/locustfile.py --host http://api:8000 --headless --csv /mnt/locust/results/w3 --csv-full-history -t 7m --stop-timeout 240"""
import itertools, json, os, random, time, uuid
from locust import HttpUser, LoadTestShape, between, events, task

SCENARIOS = json.load(open("/mnt/locust/scenarios.json")) if __import__("os").path.exists("/mnt/locust/scenarios.json") else []
VALID = [s["report"] for s in SCENARIOS if s["kind"] == "valid"] or [{
    "patient_age": 35, "patient_sex": "female", "suspect_drug": "Tegretol (carbamazepine)", "start_date": "2026-10-01",
    "reaction_date": "2026-10-08", "hospitalization": True, "reporter_type": "doctor",
    "reaction_description": "The patient's skin started peeling badly one week after starting the drug, and she was admitted to hospital."}]


class Clinician(HttpUser):
    wait_time = between(0.5, 1.5)

    def on_start(self):
        self.cid = "load-" + uuid.uuid4().hex[:8]          # distinct client id: rate limiting is per client

    @task
    def submit_and_wait(self):
        rep = dict(random.choice(VALID))
        t0 = time.monotonic()
        with self.client.post("/reports", json=rep, headers={"X-Client-Id": self.cid}, name="POST /reports", catch_response=True) as r:
            if r.status_code != 202:
                r.failure(f"submit {r.status_code}"); return
            run_id = r.json()["run_id"]
        status, deadline = "queued", t0 + 180
        while time.monotonic() < deadline:
            g = self.client.get(f"/runs/{run_id}", name="GET /runs/{id}")
            status = g.json().get("status")
            if status in ("done", "failed"):
                break
            time.sleep(0.5)
        total_ms = (time.monotonic() - t0) * 1000
        events.request.fire(request_type="E2E", name="E2E run", response_time=total_ms, response_length=0,
                            exception=None if status == "done" else Exception(f"run {status}"), context={})


class Ramp(LoadTestShape):
    """Run one-minute user stages; LOADTEST_USER_STAGES overrides the default ramp."""
    stages = [int(value) for value in os.environ.get("LOADTEST_USER_STAGES", "2,4,8,12,16,24,32").split(",")]

    def tick(self):
        t = self.get_run_time()
        i = int(t // 60)
        return (self.stages[i], 4) if i < len(self.stages) else None
