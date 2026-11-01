"""Verify P4 through a running API using dedicated demo accounts and an existing run.

This writes one clearly marked smoke note and reuses the original alert event;
it does not create a new investigation or call a model. Credentials stay local.
"""

import argparse
from pathlib import Path

import httpx

from backend.config import Settings
from scripts.submit_p4_demo import loopback_url


def check(api_url: str, run_id: int, credentials_file: Path) -> list[str]:
    passwords = dict(line.split("=", 1) for line in credentials_file.read_text(encoding="utf-8").splitlines()
                     if "=" in line and not line.startswith("#"))
    checks = []
    clients = []

    def expect(response, code: int, name: str):
        assert response.status_code == code, f"{name}: expected {code}, got {response.status_code}"
        checks.append(name)
        return response.json().get("data")

    def login(name):
        client = httpx.Client(base_url=api_url, trust_env=False, timeout=30)
        clients.append(client)
        profile = expect(client.post("/auth/login", json={"username": name, "password": passwords[name]}),
                         200, f"login:{name}")
        client.headers["X-WorkPilot-Team-Id"] = str(profile["teams"][0]["id"])
        return client, profile

    try:
        owner, owner_user = login("wp-p4-owner")
        peer, peer_user = login("wp-p4-peer")
        other, other_user = login("wp-p4-other")
        path = f"/agent/runs/{run_id}"
        before = expect(owner.get(path + "/collaboration"), 200, "authorized collaboration")
        assert before["incident"] is not None
        payload = before["incident"]["payload"]
        key = Settings().ingestion_api_key
        assert key is not None
        headers = {"X-Ingestion-Key": key.get_secret_value()}
        for label, client in (("owner", owner), ("peer", peer)):
            accepted = expect(client.post("/alerts/incidents", json=payload, headers=headers),
                              202, f"duplicate event:{label}")
            assert accepted["run_id"] == run_id
        expect(owner.post("/alerts/incidents", json=dict(payload, severity=(
            "warning" if payload["severity"] == "critical" else "critical")), headers=headers),
            409, "event content conflict")
        expect(other.post("/alerts/incidents", json=payload, headers=headers), 403, "unauthorized alert service")
        expect(owner.post("/alerts/incidents", json=payload, headers={"X-Ingestion-Key": "wrong"}),
               401, "bad ingestion key")
        with httpx.Client(base_url=api_url, trust_env=False, timeout=30) as guest:
            expect(guest.post("/alerts/incidents", json=payload, headers=headers), 401, "key alone cannot choose identity")
        for suffix in ("", "/trace", "/events", "/attempts", "/collaboration"):
            expect(other.get(path + suffix), 404, "cross-team denied:" + (suffix or "detail"))
        expect(other.patch(path + "/collaboration", json={"expected_version": 0}), 404, "cross-team write denied")
        listing = expect(other.get("/agent/runs"), 200, "other team history")
        assert all(item["id"] != run_id for item in listing["items"])
        update = {"expected_version": before["version"], "owner_user_id": peer_user["id"],
                  "handling_status": "in_progress", "feedback": "inconclusive",
                  "note": "[P4 SMOKE] 同团队成员交接；模型结论仍待核实。"}
        expect(peer.patch(path + "/collaboration", json=dict(update, owner_user_id=other_user["id"])),
               422, "foreign-team owner denied")
        expect(peer.patch(path + "/collaboration", json=dict(update, execution_status="completed")),
               422, "execution status cannot be forged")
        after = expect(peer.patch(path + "/collaboration", json=update), 200, "peer handoff and note")
        assert after["version"] == before["version"] + 1
        assert after["notes_total"] == before["notes_total"] + 1
        expect(owner.patch(path + "/collaboration", json=update), 409, "stale review rejected")
        observed = expect(owner.get(path + "/collaboration"), 200, "shared note visible")
        assert observed["owner_user_id"] == peer_user["id"]
        assert observed["notes"][0]["actor_user_id"] == peer_user["id"]
        assert observed["execution_status"] == before["execution_status"]
        detail = expect(owner.get(path), 200, "persisted investigation result")
        assert detail["mode"] == "multi" and detail["evidence"]
        attempts = expect(owner.get(path + "/attempts"), 200, "attempt history")
        assert any(item["status"] == "expired" for item in attempts)
        assert attempts[-1]["status"] == "completed"
        events = owner.get(path + "/events")
        assert events.status_code == 200 and "event: terminal" in events.text
        checks.append("terminal SSE after reconnect")
        summary = expect(owner.get("/investigations/summary"), 200, "provenance grouped counts")
        assert summary["root_cause_accuracy"] is None
        assert any(row["source_kind"] == payload["source_kind"] for row in summary["groups"])
        return checks
    finally:
        for client in clients:
            client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", type=loopback_url, default="http://127.0.0.1:8000")
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--credentials-file", type=Path, default=Path(".env.p4-demo"))
    args = parser.parse_args()
    checks = check(args.api_url, args.run_id, args.credentials_file)
    for name in checks:
        print("PASS", name)
    print(f"P4 HTTP smoke passed: {len(checks)} checks; no new model calls.")
