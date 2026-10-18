"""Versioned offline candidate checks and review packets, independent of the app verifier.

Parameter/value extraction tolerates paraphrases. It does not establish causal correctness.
Raw runs and the original Gold/lexical scorer are never rewritten.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re


VERSION = "claim-review-v2.1"
ROOT = Path(__file__).resolve().parents[1]
NUMBER = r"\d+(?:\.\d+)?"
ACTION = re.compile(r"恢复|设置|调整|核对|检查|确认|验证|维持|保持|restore|set\b|check|verify|maintain", re.I)
CAUSE = re.compile(r"原因|根因|导致|引起|拒绝|阻塞|受控|cause|reject|block|controlled", re.I)
UNPROVEN = re.compile(r"无法确认|不能确认|尚未证实|未能确认|not proven|unproven|cannot confirm|not established", re.I)
BOUNDS = [
    ("lower_bound", re.compile(r"(?:至少|不低于|大于等于|>=|at least|minimum(?: of)?)\s*(" + NUMBER + r")", re.I), 1),
    ("lower_bound", re.compile(r"(" + NUMBER + r")\s*(?:或更高|或更多|及以上|以上|or higher|or more|or above|and above)", re.I), 1),
    ("upper_bound", re.compile(r"(?:至多|不高于|不超过|<=|at most|maximum(?: of)?)\s*(" + NUMBER + r")", re.I), 1),
    ("upper_bound", re.compile(r"(" + NUMBER + r")\s*(?:或更低|及以下|以下|or lower|or less|or below)", re.I), 1),
]


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def slot(marker):
    match = re.fullmatch(r"([a-zA-Z_]\w*)=(" + NUMBER + r")", marker or "")
    return match.groups() if match else None


def value_mentions(text, parameter, value):
    """Extract parameter/value co-mentions within a clause, not a semantic verdict."""
    pattern = re.compile(r"(?<![\w])" + re.escape(parameter) + r"(?![\w])[^。；;\n]{0,80}?(?<![\d.])"
                         + re.escape(value) + r"(?![\d.])", re.I)
    return [match.group() for match in pattern.finditer(text)]


def quantitative_flags(text, parameter):
    flags = []
    for clause in re.split(r"[。；;\n]", text):
        if not re.search(r"(?<![\w])" + re.escape(parameter) + r"(?![\w])", clause, re.I):
            continue
        for kind, pattern, number_group in BOUNDS:
            for match in pattern.finditer(clause):
                flags.append({"kind": kind, "value": match.group(number_group), "excerpt": match.group()})
    return flags


def assess(case, result):
    truth = case["truth"]  # Evaluator only. Never included in investigator/verifier inputs.
    evidence = {item["evidence_id"]: item for item in result.get("evidence", [])}
    answer = result.get("final_answer", "")
    reports = (result.get("verifier") or {}).get("claims", [])
    proposed = {claim["claim_id"]: claim for task in result.get("agent_tasks", []) for claim in task.get("findings", [])}
    cause_slot, action_slot = slot(truth.get("cause_marker")), slot(truth.get("action_marker"))
    checks, packets, issues = [], [], []
    for claim in reports:
        references = claim.get("evidence_ids", [])
        valid = bool(references) and all(key in evidence for key in references)
        cited = [evidence[key] for key in references if key in evidence]
        source_text = json.dumps([item["output"] for item in cited], ensure_ascii=False)
        text = claim["text"]
        shown = text in answer and claim["support"] != "unsupported"
        # Presence, action wording and cited source slots are separate observable checks.
        cause_candidate = bool(cause_slot and claim["claim_type"] in {"fact", "hypothesis"}
            and claim["support"] == "supported" and shown and valid and not UNPROVEN.search(text)
            and CAUSE.search(text) and value_mentions(text, *cause_slot)
            and value_mentions(source_text, *cause_slot))
        action_candidate = bool(action_slot and claim["claim_type"] == "recommendation" and shown and valid
            and ACTION.search(text) and value_mentions(text, *action_slot)
            and value_mentions(source_text, *action_slot))
        bounds = quantitative_flags(text, action_slot[0]) if action_slot and shown else []
        source_bounds = quantitative_flags(source_text, action_slot[0]) if action_slot else []
        source_keys = {(item["kind"], item["value"]) for item in source_bounds}
        unsupported_bounds = [bound for bound in bounds if (bound["kind"], bound["value"]) not in source_keys]
        for bound in unsupported_bounds:
            issues.append({"claim_id": claim["claim_id"], "issue": "quantitative_bound_not_found_in_cited_records",
                           **bound, "app_verifier_support": claim["support"], "review_status": "pending"})
        if shown and not valid:
            issues.append({"claim_id": claim["claim_id"], "issue": "missing_citation", "review_status": "pending"})
        if shown:
            for service in case["services"]:
                gap = re.search(r"(?:缺乏|缺少|未获取|没有)[^。；;\n]{0,12}" + re.escape(service)
                                + r"[^。；;\n]{0,30}日志", text)
                available_logs = [key for key, item in evidence.items()
                    if item.get("tool") == "query_error_logs" and item.get("service") == service
                    and item["output"].get("items")]
                if gap and available_logs:
                    issues.append({"claim_id": claim["claim_id"],
                        "issue": "claimed_evidence_gap_with_relevant_records_available",
                        "excerpt": gap.group(), "available_evidence_ids": available_logs,
                        "app_verifier_support": claim["support"], "review_status": "pending"})
        if shown and re.search(r"已排除.{0,8}共同根因|ruled out.{0,12}common (?:root )?cause", text + claim.get("reason", ""), re.I):
            issues.append({"claim_id": claim["claim_id"], "issue": "claimed_exclusion_requires_independent_review", "review_status": "pending"})
        checks.append({"claim_id": claim["claim_id"], "displayed": shown, "citations_exist": valid,
                       "cause_slot_candidate": cause_candidate, "action_slot_candidate": action_candidate})
        packets.append({"claim_id": claim["claim_id"], "claim_type": claim["claim_type"], "text": text,
            "displayed": shown, "proposal_exists": claim["claim_id"] in proposed,
            "app_verifier": {"support": claim["support"], "reason": claim.get("reason")},
            "cited_records": cited, "independent_support": None, "reviewer": None,
            "review_instruction": "Compare the entire claim against cited records; app support is not Gold. Record false acceptance/rejection separately."})
    return {"version": VERSION, "case_id": case["id"],
        "execution_completed": result.get("status") == "completed",
        "investigation_completed": result.get("investigation_status") == "completed",
        "expected_slots": {"cause": cause_slot, "observed_restoration": action_slot},
        "cause_slot_candidate_present": any(item["cause_slot_candidate"] for item in checks) if cause_slot else None,
        "action_slot_candidate_present": any(item["action_slot_candidate"] for item in checks) if action_slot else None,
        "has_displayed_recommendation": any(c["claim_type"] == "recommendation" and c["text"] in answer and c["support"] != "unsupported" for c in reports),
        "claim_checks": checks, "review_flags": issues, "review_packet": packets,
        "evidence_ledger": list(evidence.values()),
        "global_review_instruction": "Also check claims of missing evidence against the full ledger; cited records alone cannot establish absence elsewhere.",
        "independent_review_status": "pending", "business_success": None,
        "limitation": "Rule-based slot candidates, not full semantic grades. Independent source review is required, including statements not matched by these patterns."}


def rescore(directory, output_name="scoring_v2.json"):
    cases = {case["id"]: case for case in json.loads((ROOT / "eval/replay_cases.json").read_text(encoding="utf-8"))}
    raw_path = directory / "raw.jsonl"
    source_bytes = raw_path.read_bytes()
    runs = []
    for line in source_bytes.decode("utf-8").splitlines():
        record = json.loads(line)
        case = cases[record["case_id"]]
        if record["fixture_hash"] != digest({key: case[key] for key in ("services", "logs", "ticket")}):
            raise ValueError("Fixture changed; use the original frozen case before rescoring")
        runs.append({"variant": record["variant"], "repeat": record["repeat"],
                     "original_lexical_scores": record.get("scores"), "assessment": assess(case, record["result"])})
    output = {"version": VERSION, "source_raw_sha256": sha256(source_bytes).hexdigest(),
              "scorer_sha256": sha256(Path(__file__).read_bytes()).hexdigest(), "runs": runs}
    path = directory / output_name
    if path.exists():
        raise FileExistsError("Versioned scoring output already exists; preserve it and use a new output version")
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output-name", default="scoring_v2.json")
    args = parser.parse_args()
    print(rescore(args.results, args.output_name))


if __name__ == "__main__":
    main()
