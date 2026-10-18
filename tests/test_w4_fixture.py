"""W4 fixture comparison gates without MySQL or an online model."""

import json

from backend.services.multi_agent_service import MultiAgentOptions
from eval.w4_fixture import CASES_PATH, run_one


CASES = {item["id"]: item for item in json.loads(CASES_PATH.read_text(encoding="utf-8"))}


def test_frozen_scenarios_are_distinct_and_cover_required_faults():
    assert len(CASES) >= 18
    assert {"normal_two", "transient_once", "followup_gain", "wrong_service_record",
            "non_low_tool", "routing_ambiguity", "overreach_claim"} <= CASES.keys()


def test_equal_budget_single_and_multi_use_same_fixture_contract():
    case = CASES["normal_two"]
    single = run_one(case, "enhanced_single_equal", 2001)
    multi = run_one(case, "multi", 2002)
    for key in ("tool_limit", "tool_attempts", "model_calls", "tokens", "log_service_coverage"):
        assert single["metrics"][key] == multi["metrics"][key]
    assert single["result"]["evidence"][0]["agent_id"] == "enhanced-single"
    assert {item["agent_id"] for item in multi["result"]["agent_tasks"]} == {
        "investigator-1", "investigator-2",
    }


def test_ablations_change_only_the_selected_mechanism():
    followup = CASES["followup_gain"]
    full = run_one(followup, "multi", 2003)
    disabled = run_one(followup, "multi_no_followup", 2004,
                       MultiAgentOptions(enable_followup=False))
    assert full["metrics"]["followup_count"] == 1
    assert disabled["metrics"]["followup_count"] == 0
    assert full["metrics"]["tool_attempts"] == disabled["metrics"]["tool_attempts"] + 1
    retry_case = CASES["transient_once"]
    full_retry = run_one(retry_case, "multi", 2005)
    no_retry = run_one(retry_case, "multi_no_retry", 2006,
                       MultiAgentOptions(enable_retry=False))
    assert full_retry["metrics"]["retry_count"] == 1
    assert no_retry["metrics"]["retry_count"] == 0
    assert no_retry["metrics"]["log_service_coverage"] < full_retry["metrics"]["log_service_coverage"]


def test_scope_and_risk_violations_do_not_enter_evidence():
    wrong_scope = run_one(CASES["wrong_service_record"], "multi", 2007)
    assert wrong_scope["metrics"]["wrong_scope_evidence_ids"] == []
    assert wrong_scope["metrics"]["log_service_coverage"] == 0.5
    blocked = run_one(CASES["non_low_tool"], "multi", 2008)
    assert blocked["result"]["status"] == "blocked"
    assert blocked["fixture_calls"] == []
    assert blocked["metrics"]["tool_attempts"] == 0


def test_injected_false_rejection_is_counted_for_both_variants():
    case = CASES["verifier_false_reject"]
    single = run_one(case, "enhanced_single_equal", 2009)
    multi = run_one(case, "multi", 2010)
    assert single["metrics"]["injected_false_rejections"] == 1
    assert multi["metrics"]["injected_false_rejections"] == 1
    assert single["metrics"]["log_service_coverage"] == multi["metrics"]["log_service_coverage"] == 1
