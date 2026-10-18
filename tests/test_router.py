import json
from pathlib import Path

import pytest

from backend.agent.router import IntentRouter


CASES = json.loads((Path(__file__).resolve().parents[1] / "eval/routing_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_existing_routing_cases(case):
    decision = IntentRouter(None).route(case["query"])
    assert decision.intent == case["expected_intent"]
    assert decision.source == case["expected_source"]


@pytest.mark.parametrize("query,intent", [
    ("诊断 order-service 与 inventory-service 的超时，核对冲突证据", "incident_analysis"),
    ("排查两个服务的异常并核对工单分歧", "incident_analysis"),
    ("搜索分析超时原因的知识库文档", "knowledge_search"),
    ("查询故障分析工单 42 的详情", "ticket_search"),
])
def test_incident_synonyms_and_adjacent_searches(query, intent):
    assert IntentRouter(None).route(query).intent == intent
