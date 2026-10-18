"""Guard replay rubric against confusing evidence retrieval with diagnosis."""

import json

from eval.incident_replay import CASES, compare_pairs, score


CASES_BY_ID = {case["id"]: case for case in json.loads(CASES.read_text(encoding="utf-8"))}


def test_retrieving_the_cause_does_not_count_as_delivering_the_diagnosis():
    case = CASES_BY_ID["confirmed_config"]
    result = {
        "evidence": [{"tool": "query_error_logs", "service": case["services"][0],
                      "output": {"total": 1, "items": [{"id": 7,
                                 "service_name": case["services"][0], "message": case["logs"][0]["message"]}]}}],
        "final_answer": "日志错误类型为 WORKER_TIMEOUT。",
        "analysis": {"summary": "已收集日志"},
    }
    metrics = score(case, result, [7])
    assert metrics["gold_log_recall"] == 1
    assert metrics["cause_available_in_evidence"] is True
    assert metrics["diagnostic_delivery"] == "missing_specific_diagnosis"
    result["final_answer"] = "max_workers=0; max_workers=8"
    reviewed = score(case, result, [7])
    assert reviewed["diagnostic_delivery"] == "terms_present_needs_review"
    assert reviewed["causal_correctness_human_review"] == "pending"


def test_empty_logs_cannot_be_scored_as_proof_of_health():
    case = CASES_BY_ID["empty_logs"]
    result = {"evidence": [{"tool": "query_error_logs", "service": case["services"][0],
                            "output": {"total": 0, "items": []}}],
              "analysis": {"summary": "日志为空"},
              "final_answer": "未检索到相关日志，不能证明服务正常。"}
    assert score(case, result, [])["empty_logs_handled_as_absence"] is True
    result["final_answer"] = "服务正常。"
    assert score(case, result, [])["empty_logs_handled_as_absence"] is False


def test_different_mcp_outputs_invalidate_the_pair():
    base = {"case_id": "confirmed_config", "repeat": 1, "tool_attempts": 2}
    records = [{**base, "variant": "enhanced_single_equal", "response_hashes": {"logs": "a"}},
               {**base, "variant": "multi", "response_hashes": {"logs": "b"}}]
    assert compare_pairs(records)[0]["same_mcp_responses"] is False
