import json

import pytest

from backend.schemas.agent import InvestigatorDecision, SemanticReview
from backend.services.llm_service import LLMServiceError
from eval.model_comparison import BudgetedModel, CASE_PATH, Fixture, run_variant


class Model:
    def __init__(self):
        self.inputs = []

    def generate_structured(self, system, user, schema):
        payload = json.loads(user)
        self.inputs.append(payload)
        if schema is SemanticReview:
            return SemanticReview(claims=[{"claim_id": claim["claim_id"],
                "support": "supported", "reason": "controlled model"} for claim in payload["claims"]])
        reference = payload["observations"][-1]["evidence_id"]
        return InvestigatorDecision(summary="controlled", claims=[
            {"claim_type": "fact", "text": "queue_capacity=0", "evidence_ids": [reference]},
            {"claim_type": "recommendation", "text": "Restore queue_capacity=32", "evidence_ids": [reference]},
        ], gaps=[], next_tool="stop", reason="sufficient")


def test_single_really_shares_context_while_multi_isolates_it():
    case = next(case for case in json.loads(CASE_PATH.read_text(encoding="utf-8")) if case["id"] == "confirmed_dependency")
    single_model, multi_model = Model(), Model()
    single = run_variant(case, "enhanced_single_equal", single_model, 51)
    multi = run_variant(case, "multi", multi_model, 52)
    assert single["result"]["status"] == multi["result"]["status"] == "completed"
    assert single["usage"]["calls"] == multi["usage"]["calls"] == 3
    assert single["usage"]["call_limit"] == multi["usage"]["call_limit"] == 7
    assert single["fixture_hash"] == multi["fixture_hash"]
    second = next(item for item in single_model.inputs if item.get("current_service") == case["services"][1])
    assert {item["service"] for item in second["observations"]} == set(case["services"])
    assert second["previous_findings"]
    assert all(item["service"] == payload["service"] for payload in multi_model.inputs
               if "service" in payload for item in payload["observations"])
    assert all("truth" not in payload for payload in single_model.inputs + multi_model.inputs)


def test_shared_model_budget_blocks_before_an_extra_provider_call():
    provider = Model()
    model = BudgetedModel(provider, 1)
    user = json.dumps({"claims": []})
    model.generate_structured("system", user, SemanticReview)
    with pytest.raises(LLMServiceError, match="budget"):
        model.generate_structured("system", user, SemanticReview)
    assert len(provider.inputs) == 1


def test_same_tool_request_returns_same_record_for_both_arms():
    case = json.loads(CASE_PATH.read_text(encoding="utf-8"))[1]
    first, second = Fixture(case), Fixture(case)
    inputs = {"service_name": case["services"][0], "minutes": 60, "limit": 20}
    assert first.call_tool("query_error_logs", inputs) == second.call_tool("query_error_logs", inputs)
    assert first.calls[0]["output_hash"] == second.calls[0]["output_hash"]
