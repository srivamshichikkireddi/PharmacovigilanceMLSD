import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from pv.eval.baseline import SINGLE_PASS_TOP_K, run_baseline
from pv.eval.metrics import aggregate, faithfulness, groundedness
from pv.llm.client import FakeLLM, LLMQuotaExceeded
from pv.llm.resilience import ResilientLLM
from tests.common import INDEX, settings


class CountingIndex:
    def __init__(self):
        self.calls = []

    def search(self, query, k, source=None):
        self.calls.append((query, k, source))
        return [{"id": "p1", "source": "tegretol_us_pi", "page": 4,
                 "text": "Toxic epidermal necrolysis has been reported."}]


class EvaluationBaseline(unittest.TestCase):
    def test_one_search_one_model_call_and_live_result_shape(self):
        output = {
            "route": "expedited_report", "serious": True, "expectedness": "expected",
            "basis": [{"claim": "Listed reaction", "anchor": "label_listing", "passage_id": "p1",
                       "source": "tegretol_us_pi", "page": 4,
                       "quote": "Toxic epidermal necrolysis has been reported."}],
            "seriousness_reasons": ["hospitalization"], "deadline": {"days": 15},
            "route_basis": "serious expected", "escalation": {"required": False, "reasons": [], "path": None},
        }
        client = FakeLLM(lambda _messages: "```json\n" + json.dumps(output) + "\n```")
        llm = ResilientLLM(client, settings, sleep=lambda _delay: None)
        index = CountingIndex()
        report = {"suspect_drug": "Tegretol", "reaction_description": "skin peeling after a week"}

        result, trace, passage_ids = run_baseline("baseline-test", report, index, llm, settings)

        self.assertEqual(len(index.calls), 1)
        self.assertEqual(index.calls[0][1], SINGLE_PASS_TOP_K)
        self.assertEqual(client.calls, 1)
        self.assertEqual(result["route"], output["route"])
        self.assertEqual(result["basis"][0]["passage_id"], "p1")
        self.assertEqual(result["verification"], None)
        self.assertEqual(result["tool_calls"], 0)
        self.assertEqual(passage_ids, ["p1"])
        self.assertEqual([event["type"] for event in trace], ["baseline_retrieval", "llm_call"])
        self.assertTrue(result["result_schema_valid"])

    def test_grounding_metrics_are_numeric_when_an_answer_has_no_citations(self):
        self.assertEqual(groundedness(INDEX, {"basis": []}), 0.0)
        self.assertEqual(faithfulness({"basis": []}), 0.0)
        summary = aggregate([{
            "kind": "valid", "answered": False, "groundedness": 0.0, "faithfulness": 0.0,
            "context_relevance": 0.0, "context_recall": 0.0, "route_ok": 0,
            "serious_ok": None, "exp_ok": None, "expected_route": "human_escalation",
            "reasons_ok": 0, "expected_reasons": [], "hallucinated": 0, "tool_calls": 0, "cost_usd": 0,
        }])
        self.assertIsInstance(summary["groundedness"], float)
        self.assertIsInstance(summary["faithfulness"], float)

    def test_quota_failure_preserves_a_sanitized_single_attempt_trace(self):
        class RateLimited(Exception):
            status_code = 429

        client = FakeLLM(lambda _messages: (_ for _ in ()).throw(RateLimited("rate limit")))
        llm = ResilientLLM(client, settings, sleep=lambda _delay: None)
        with self.assertRaises(LLMQuotaExceeded) as raised:
            run_baseline(
                "baseline-quota-test",
                {"suspect_drug": "Tegretol", "reaction_description": "skin peeling after a week"},
                CountingIndex(),
                llm,
                settings,
            )
        self.assertEqual(client.calls, 1)
        self.assertEqual(raised.exception.trace[-1]["error"], "provider_rate_limit_or_quota")
        self.assertEqual(raised.exception.trace[-1]["attempts"], 1)

    def test_invalid_cost_configuration_is_rejected_before_provider_call(self):
        client = FakeLLM(lambda _messages: "{}")
        llm = ResilientLLM(client, settings, sleep=lambda _delay: None)
        with patch.dict("os.environ", {"LLM_PRICE_IN_PER_1M": "invalid"}):
            with self.assertRaises(ValueError):
                run_baseline(
                    "baseline-config-test",
                    {"suspect_drug": "Tegretol", "reaction_description": "skin peeling after a week"},
                    CountingIndex(),
                    llm,
                    settings,
                )
        self.assertEqual(client.calls, 0)
