import os, sys, unittest
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PV_USE_LANGGRAPH", "0")
from pv.config import settings
from pv.corpus.build import build
from pv.corpus.retriever import Index
from pv.llm.client import FakeLLM, NoLLM
from pv.llm.resilience import CircuitBreaker, ResilientLLM
from pv.schemas import validate_report

IDX = "data/index"
if not os.path.exists(IDX + "/chunks.jsonl"):
    build("corpus", IDX, log=lambda *_: None)
INDEX = Index(IDX)
TODAY = date(2026, 10, 27)
CASE41 = {"patient_age": 35, "patient_sex": "female", "suspect_drug": "Tegretol (carbamazepine)", "start_date": "2026-10-01",
          "reaction_date": "2026-10-08", "received_date": "2026-10-26", "hospitalization": True, "reporter_type": "doctor",
          "reaction_description": "The patient's skin started peeling badly one week after starting the drug, and she was admitted to hospital."}


def clean(**over):
    c, e = validate_report(dict(CASE41, **over), today=TODAY)
    assert not e, e
    return c


def nollm(): return ResilientLLM(NoLLM(), settings, sleep=lambda s: None)


def fake(script, **kw): return ResilientLLM(FakeLLM(script), settings, sleep=lambda s: None, **kw)


def good_script(messages):
    t = messages[-1]["content"]
    if "control the evidence search" in t: return '{"action":"continue"}'
    if "pharmacovigilance reviewer" in t: return '{"summary":"Serious listed reaction; 15-day expedited route recommended for QPPV confirmation."}'
    return '{"terms":["exfoliative dermatitis","toxic epidermal necrolysis","Stevens-Johnson syndrome"]}'


def run(report=None, llm=None, **over):
    from pv.agents.pipeline import run_case
    return run_case("t1", report or clean(), INDEX, llm or nollm(), settings, use_langgraph=False)


class Env(unittest.TestCase):
    """Set env vars for a test and restore them afterwards."""
    def setenv(self, **kv):
        for k, v in kv.items():
            old = os.environ.get(k)
            self.addCleanup(lambda k=k, old=old: os.environ.pop(k, None) if old is None else os.environ.__setitem__(k, old))
            os.environ[k] = str(v)
