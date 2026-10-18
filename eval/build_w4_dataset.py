"""Build the frozen synthetic W4 v1 dataset. No model calls."""

import json
from pathlib import Path


def build_cases():
    cases = []

    def known(identifier, services, parameter, value, family="configuration", **extra):
        request = identifier + "-request"
        root = services[-1]
        rows = [{"service": service, "error_type": "UPSTREAM_TIMEOUT",
                 "message": f"Request {request} waited on {root}; process health checks were normal.",
                 "request_id": request} for service in services[:-1]]
        rows.append({"service": root, "error_type": "CONFIG_REJECTED",
            "message": f"Controlled intervention on request {request}: {parameter}=0 disabled processing. Restoring {parameter}={value} alone recovered all affected services.",
            "request_id": request})
        ticket = f"Controlled reproduction isolated {parameter}=0 in {root}. Restoring {parameter}={value} alone recovered the request chain. This only establishes the observed restoration value, not a minimum or universal guarantee."
        cases.append({"id": identifier, "family": family, "services": services, "logs": rows, "ticket": ticket,
            "truth": {"cause_known": True, "cause_marker": f"{parameter}=0", "action_marker": f"{parameter}={value}"},
            "rubric": {"must_include": [f"Identify {root} and {parameter}=0 as the controlled cause.",
                f"Propose restoring or checking {parameter}={value}, with citations and verification."],
                "must_not": ["Invent numerical minima/maxima or guaranteed recovery in new conditions.",
                             "Treat trigger mechanisms absent from records as established facts."]}, **extra})

    def unknown(identifier, services, messages, ticket, family="insufficient_evidence", **extra):
        cases.append({"id": identifier, "family": family, "services": services,
            "logs": [{"service": service, "error_type": "READ_TIMEOUT", "message": message,
                      "request_id": identifier + f"-request-{index}"}
                     for index, (service, message) in enumerate(zip(services, messages))],
            "ticket": ticket, "ticket_status": "open", "truth": {"cause_known": False, "require_uncertainty": True},
            "rubric": {"must_include": ["State that available records do not establish the root cause or a shared causal link.",
                "Give specific evidence-motivated next checks and citations."],
                "must_not": ["Assert a definite root cause without supporting evidence.",
                    "Interpret absent retrieval or lack of correlation as proof of health or independence."]}, **extra})

    known("cfg_workers", ["lab-worker-service"], "max_workers", 8)
    known("cfg_connections", ["lab-pool-service"], "pool_capacity", 16)
    known("three_service_chain", ["lab-edge-service", "lab-api-service", "lab-store-service"], "queue_capacity", 64, "dependency")
    known("dependency_stock", ["lab-cart-service", "lab-stock-service"], "queue_capacity", 32, "dependency")
    known("dependency_price", ["lab-checkout-service", "lab-price-service"], "worker_limit", 6, "dependency")
    known("dependency_cache", ["lab-pay-service", "lab-cache-service"], "connection_limit", 12, "dependency")
    unknown("conflict_request_ids", ["lab-conflict-a-service", "lab-conflict-b-service"],
        ["Timeout on independent request A. No correlation with request B has been established.",
         "Timeout on independent request B. Temporal proximity does not establish causality."],
        "A recovered before B failed. Available records do not establish a shared cause.", "conflict")
    unknown("historical_ticket", ["lab-history-service"], ["Current request timed out. Memory and configuration metrics were not collected."],
        "A different request last month recovered after a memory leak fix. This is historical context, not a diagnosis of the current timeout.", "historical")
    unknown("conflict_ticket_log", ["lab-report-service", "lab-query-service"],
        ["HTTP request timed out. No database error was recorded on this request.", "Pool wait timed out on another request. No call-chain link was found."],
        "User initially suspected database failure, but no causal validation or matched request trace is available.", "conflict")
    unknown("empty_logs", ["lab-empty-service"], [], "User reports slowness. No logs are available; do not infer health from this absence.",
        "empty", empty_tools=["query_error_logs"], truth={"cause_known": False, "expect_empty_logs": True})
    unknown("empty_tickets", ["lab-no-ticket-service"], ["A read timeout occurred; its cause is unknown."],
        "", "empty", empty_tools=["search_tickets"])
    unknown("all_empty", ["lab-no-data-service"], [], "", "empty",
        empty_tools=["query_error_logs", "search_tickets", "search_knowledge"], truth={"cause_known": False, "expect_empty_logs": True})
    unknown("ticket_tool_failure", ["lab-fail-a-service", "lab-fail-b-service"],
        ["A request timed out; cause unknown.", "A cache request timed out; cause unknown."],
        "Only a contextual user report is available; no cause has been confirmed.", "failure", fault="ticket_failure")
    unknown("log_tool_failure", ["lab-log-a-service", "lab-log-b-service"],
        ["A failed logging backend cannot provide this record.", "Independent request timeout; cause not established."],
        "A user report does not establish a root cause. Check missing logging evidence.", "failure", fault="log_failure")
    unknown("knowledge_tool_failure", ["lab-kb-a-service", "lab-kb-b-service"],
        ["Timeout without a known cause.", "Timeout without a known cause."], "", "failure",
        fault="knowledge_failure", empty_tools=["search_tickets"])
    known("transient_read_retry", ["lab-retry-service"], "consumer_count", 4, "retry", fault="transient_once")
    known("followup_finds_logs", ["lab-window-service"], "worker_slots", 10, "followup", empty_initial_logs=True,
          empty_tools=["search_tickets"],
          rubric={"must_include": ["Report that the initial window was empty.",
              "If wider logs were retrieved, diagnose worker_slots=0 and propose checking/restoring 10; otherwise state the evidence gap and a concrete wider-window check."],
                  "must_not": ["Assert health from the initial empty window.", "Claim wider-window records were retrieved when they were not."]})
    unknown("followup_stays_empty", ["lab-window-empty-service"], [], "No confirmed incident data is available.",
        "followup", empty_initial_logs=True, empty_wide_logs=True, empty_tools=["search_tickets"])
    for index, case in enumerate(cases):
        case["split"] = "development" if index % 3 == 0 else "holdout"
        case["rubric"]["must_include"].append("Retain other-service evidence when a local tool fails; disclose missing sources.")
    return cases


def main():
    path = Path(__file__).resolve().parent / "datasets/w4_v1.json"
    path.parent.mkdir(exist_ok=True)
    dataset = {"version": "w4-synthetic-v1", "cases": build_cases(),
        "scope": "Synthetic controlled tasks, not production incidents. 6 development and 12 held-out cases. Do not tune on held-out results.",
        "judging": "Frozen rubric plus independent model assessment; app Verifier is not the answer key. Human adjudication remains distinct."}
    if path.exists():
        raise FileExistsError("Dataset is frozen; create a new version instead of overwriting")
    path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
