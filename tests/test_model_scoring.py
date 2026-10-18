from hashlib import sha256
import json

import pytest

from eval.model_scoring import ROOT, assess, digest, rescore


CASE = next(case for case in json.loads((ROOT / "eval/replay_cases.json").read_text(encoding="utf-8"))
            if case["id"] == "confirmed_dependency")


def result(cause="queue_capacity=0 导致请求被拒绝。", action="核对 queue_capacity 是否维持在32，并验证恢复情况。"):
    claims = [
        {"claim_id": "c1", "claim_type": "fact", "text": cause, "support": "supported", "reason": "record", "evidence_ids": ["e1"]},
        {"claim_id": "a1", "claim_type": "recommendation", "text": action, "support": "supported", "reason": "record", "evidence_ids": ["e1"]},
    ]
    return {"status": "completed", "investigation_status": "completed",
            "final_answer": cause + "\n" + action,
            "evidence": [{"evidence_id": "e1", "service": "replay-stock-service",
                          "tool": "query_error_logs", "output": {"items": [CASE["logs"][1]]}}],
            "verifier": {"claims": claims}, "agent_tasks": [{"findings": claims}]}


def test_chinese_paraphrase_is_an_action_candidate_without_becoming_a_semantic_grade():
    report = assess(CASE, result())
    assert report["cause_slot_candidate_present"]
    assert report["action_slot_candidate_present"]
    assert report["review_flags"] == []
    assert report["business_success"] is None
    assert report["independent_review_status"] == "pending"


@pytest.mark.parametrize("action", [
    "确保 queue_capacity 不低于32", "检查 queue_capacity 是否至少32", "保持 queue_capacity >=32",
    "确认 queue_capacity 稳定在32或更高", "维持 queue_capacity 在32及以上",
    "Check queue_capacity is 32 or higher", "Check queue_capacity is at least 32",
])
def test_bounds_not_in_records_are_flagged_even_when_app_verifier_accepts_them(action):
    report = assess(CASE, result(action=action))
    assert report["review_flags"][0]["issue"] == "quantitative_bound_not_found_in_cited_records"
    assert report["review_flags"][0]["app_verifier_support"] == "supported"
    assert report["business_success"] is None


def test_an_explicitly_recorded_bound_is_not_flagged_as_absent():
    data = result(action="检查 queue_capacity 是否至少32")
    data["evidence"][0]["output"]["minimum"] = "queue_capacity 至少32"
    assert assess(CASE, data)["review_flags"] == []


def test_unproven_statement_or_wrong_value_does_not_count_as_cause_delivery():
    assert not assess(CASE, result(cause="尚未证实 queue_capacity=0 是本次原因"))["cause_slot_candidate_present"]
    assert not assess(CASE, result(cause="queue_capacity=10 导致故障"))["cause_slot_candidate_present"]


def test_fact_mention_is_not_an_action_and_missing_citations_are_not_credited():
    data = result()
    data["verifier"]["claims"][1]["claim_type"] = "fact"
    assert not assess(CASE, data)["action_slot_candidate_present"]
    data = result()
    data["verifier"]["claims"][1]["evidence_ids"] = ["missing"]
    report = assess(CASE, data)
    assert not report["action_slot_candidate_present"]
    assert any(flag["issue"] == "missing_citation" for flag in report["review_flags"])


def test_rejected_claims_stay_in_review_packet_without_counting_as_delivered():
    data = result()
    data["verifier"]["claims"][1]["support"] = "unsupported"
    report = assess(CASE, data)
    assert not report["action_slot_candidate_present"]
    assert report["review_packet"][1]["app_verifier"]["support"] == "unsupported"
    assert report["review_packet"][1]["independent_support"] is None


def test_claim_of_missing_logs_is_cross_checked_against_the_full_ledger():
    data = result(cause="缺乏对 replay-stock-service 在故障时刻实际日志的交叉验证。")
    report = assess(CASE, data)
    gap = next(flag for flag in report["review_flags"]
               if flag["issue"] == "claimed_evidence_gap_with_relevant_records_available")
    assert gap["available_evidence_ids"] == ["e1"]
    assert report["evidence_ledger"] == data["evidence"]
    assert gap["review_status"] == "pending"


def test_rescoring_preserves_raw_bytes_and_old_lexical_results(tmp_path):
    record = {"case_id": CASE["id"], "variant": "multi", "repeat": 1,
              "fixture_hash": digest({key: CASE[key] for key in ("services", "logs", "ticket")}),
              "result": result(), "scores": {"specific_action_terms_present": False}}
    raw = (json.dumps(record, ensure_ascii=False) + "\n").encode()
    (tmp_path / "raw.jsonl").write_bytes(raw)
    path = rescore(tmp_path)
    report = json.loads(path.read_text(encoding="utf-8"))
    assert (tmp_path / "raw.jsonl").read_bytes() == raw
    assert report["source_raw_sha256"] == sha256(raw).hexdigest()
    assert report["runs"][0]["original_lexical_scores"] == record["scores"]
    assert report["runs"][0]["assessment"]["action_slot_candidate_present"]
