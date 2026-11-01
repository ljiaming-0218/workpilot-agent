"""Observe real local HTTP failures, import their logs, then submit one alert."""

import argparse
from getpass import getpass
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from backend.config import Settings


def loopback_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise argparse.ArgumentTypeError("Use a loopback http://127.0.0.1:PORT origin")
    return value.rstrip("/")


def submit(client: httpx.Client, source_url: str, service: str, key: str,
           event_id: str | None = None) -> dict:
    """Client is already logged in and carries its trusted team selection."""
    with httpx.Client(base_url=source_url, trust_env=False, timeout=5) as source:
        seen_ids = []
        for _ in range(3):
            response = source.get("/checkout")
            if response.status_code != 503:
                raise RuntimeError("Expected a controlled 503; POST /control/fail to enable it")
            seen_ids.append(response.json()["request_id"])
        items = source.get("/observations").raise_for_status().json()["items"]
    observed = [dict(item, service_name=service) for item in items if item["request_id"] in seen_ids]
    if len(observed) != 3:
        raise RuntimeError("Local HTTP observation log is incomplete")
    imported = client.post("/ingestion/logs", headers={"X-Ingestion-Key": key}, json={"items": observed})
    imported.raise_for_status()
    payload = {"source": "local-http-test", "source_kind": "local_test",
               "event_id": event_id or "http-" + uuid4().hex, "service_name": service,
               "alert_name": "Local checkout dependency returned HTTP 503",
               "severity": "critical", "error_type": "DEPENDENCY_HTTP_ERROR", "window_minutes": 60}
    response = client.post("/alerts/incidents", headers={"X-Ingestion-Key": key}, json=payload)
    response.raise_for_status()
    return {"alert": response.json()["data"], "payload": payload,
            "import": imported.json()["data"], "observed_request_ids": seen_ids}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", type=loopback_url, default="http://127.0.0.1:8000")
    parser.add_argument("--source-url", type=loopback_url, default="http://127.0.0.1:8765")
    parser.add_argument("--team-id", type=int, required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--service", default="p4-http-service")
    parser.add_argument("--event-id")
    args = parser.parse_args()
    settings = Settings()
    if settings.ingestion_api_key is None:
        parser.error("Configure INGESTION_API_KEY in .env first")
    with httpx.Client(base_url=args.api_url, trust_env=False, timeout=30,
                      headers={"X-WorkPilot-Team-Id": str(args.team_id)}) as client:
        client.post("/auth/login", json={"username": args.username,
                    "password": getpass("Password: ")}).raise_for_status()
        result = submit(client, args.source_url, args.service,
                        settings.ingestion_api_key.get_secret_value(), args.event_id)
        accepted = result["alert"]
        print(f"Imported observed logs: {result['import']['created']}; run_id={accepted['run_id']}; queue={accepted['queue_status']}")
        print(f"Console: {args.api_url}/console/?run_id={accepted['run_id']}")
