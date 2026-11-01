"""Exercise attempt isolation on disposable, randomly named TiDB/MySQL tables."""

from datetime import timedelta
import re
import subprocess
import sys
import os
from threading import Thread, Event
from queue import Queue, Empty
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import event, func, select, text, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateTable
from fastapi.testclient import TestClient

import backend.models  # noqa: F401
from backend.config import Settings
from backend.database import build_engine
from backend.models.agent_run import AgentRun, AgentRunService
from backend.models.approval_checkpoint import AgentApprovalCheckpoint
from backend.models.durable_job import DurableJob, DurableJobAttempt
from backend.models.enums import AgentRunStatus
from backend.models.enums import TraceStatus
from backend.models.tool_call import ToolCallLog
from backend.models.trace_event import AgentTraceEvent
from backend.agent.planner import Planner
from backend.agent.risk import RiskChecker
from backend.agent.router import IntentRouter
from backend.agent import build_agent_graph
from backend.agent.analyzer import IncidentAnalyzer
from langgraph.checkpoint.memory import InMemorySaver
from backend.services.durable_attempt import publish_attempt, start_attempt
from backend.services.durable_queue import add_job, claim_next, digests, finish_claim
from backend.services.durable_worker import run_one
from backend.tools import create_default_registry
from backend.main import create_app
from backend.dependencies import get_db
from backend.services.access_service import current_team
from backend.services.auth_service import hash_password, token_digest
from backend.services.approval_recovery_service import reconcile_approval_checkpoints
from backend.services.approval_lock import approval_lock
import backend.services.durable_agent_adapter as adapter


def table_names(suffix: str) -> dict[str, str]:
    if not re.fullmatch(r"[0-9a-f]{32}", suffix):
        raise ValueError("Invalid probe suffix")
    return {name: f"p3a_{short}_{suffix}" for name, short in (
        ("agent_runs", "run"), ("agent_run_services", "scope"),
        ("durable_jobs", "job"), ("durable_job_attempts", "attempt"),
        ("agent_approval_checkpoints", "checkpoint"),
        ("tool_call_logs", "tool"), ("agent_trace_events", "trace"),
        ("auth_sessions", "auth"),
        ("users", "user"), ("teams", "team"),
        ("team_members", "member"), ("service_grants", "grant"))}


def install_rewrite(engine, names, suffix):
    def rewrite(connection, cursor, statement, parameters, context, executemany):
        for original, replacement in names.items():
            statement = re.sub(r"\b" + original + r"\b", replacement, statement)
        for name in ("fk_trace_events_agent_run", "fk_tool_call_logs_agent_run",
                     "fk_approval_checkpoints_agent_run"):
            statement = re.sub(r"\b" + name + r"\b", f"{name}_{suffix[:8]}", statement)
        return statement, parameters
    event.listen(engine, "before_cursor_execute", rewrite, retval=True)
    return rewrite


def child_process(suffix: str) -> None:
    engine = build_engine(Settings())
    install_rewrite(engine, table_names(suffix), suffix)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    def block_after_claim(execution):
        print(f"CLAIMED {execution.run_id}", flush=True)
        while True:
            sleep(1)
    try:
        run_one(factory, "interrupt-worker", block_after_claim, lease_seconds=8, heartbeat_seconds=1)
    finally:
        engine.dispose()


def reconcile_process(suffix: str) -> None:
    engine = build_engine(Settings())
    install_rewrite(engine, table_names(suffix), suffix)
    factory = sessionmaker(bind=engine, autoflush=False)
    try:
        with factory() as session:
            reconcile_approval_checkpoints(session)
    finally:
        engine.dispose()


def hold_approval(request_id: str) -> None:
    engine = build_engine(Settings())
    try:
        with approval_lock(engine, request_id) as acquired:
            if not acquired:
                raise RuntimeError("Cannot acquire approval lock")
            print("LOCKED", flush=True)
            while True:
                sleep(1)
    finally:
        engine.dispose()


def main() -> None:
    engine = build_engine(Settings())
    suffix = uuid4().hex
    print(f"Probe table suffix: {suffix}", flush=True)
    names = table_names(suffix)
    created: list[str] = []
    rewrite = None

    def create(statement: str, name: str) -> None:
        with engine.begin() as connection:
            connection.execute(text(statement))
        created.append(names[name])

    try:
        create(f"CREATE TABLE `{names['team_members']}` (id INT PRIMARY KEY, team_id INT NOT NULL, user_id INT NOT NULL, role VARCHAR(24) NOT NULL)", "team_members")
        create(f"CREATE TABLE `{names['service_grants']}` (id INT PRIMARY KEY, team_id INT NOT NULL, service_name VARCHAR(128) NOT NULL)", "service_grants")
        create(f"CREATE TABLE `{names['auth_sessions']}` (id INT AUTO_INCREMENT PRIMARY KEY, user_id INT NOT NULL, token_hash VARCHAR(64) NOT NULL, expires_at DATETIME(6) NOT NULL, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6))", "auth_sessions")
        create(f"CREATE TABLE `{names['users']}` (id INT PRIMARY KEY, username VARCHAR(64), password_hash VARCHAR(255), role VARCHAR(20), created_at DATETIME(6), updated_at DATETIME(6))", "users")
        create(f"CREATE TABLE `{names['teams']}` (id INT PRIMARY KEY, name VARCHAR(64), created_at DATETIME(6), updated_at DATETIME(6))", "teams")
        rewrite = install_rewrite(engine, names, suffix)
        for table in (AgentRun.__table__, AgentRunService.__table__, DurableJob.__table__,
                      DurableJobAttempt.__table__, AgentApprovalCheckpoint.__table__,
                      ToolCallLog.__table__, AgentTraceEvent.__table__):
            with engine.begin() as connection:
                connection.execute(CreateTable(table))
            created.append(names[table.name])
        factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        with factory.begin() as session:
            session.execute(text("INSERT INTO team_members VALUES (1, 10, 20, 'member')"))
            session.execute(text("INSERT INTO service_grants VALUES (1, 10, 'payment-service')"))
            root = AgentRun(request_id="root", user_query="payment-service", creator_user_id=20,
                            team_id=10, service_scope_recorded=True, status=AgentRunStatus.RUNNING)
            session.add(root)
            session.flush()
            root_id = root.id
            session.add(AgentRunService(agent_run_id=root_id, service_name="payment-service"))
            key, body = digests("attempt-test", root.user_query)
            add_job(session, run_id=root_id, team_id=10, user_id=20, key_digest=key,
                    request_digest=body, retry_safe=True)
        old = claim_next(factory, "old-worker")
        assert old is not None
        old_execution = start_attempt(factory, old)
        assert old_execution is not None and old_execution.run_id != root_id
        try:
            start_attempt(factory, old)
        except RuntimeError:
            pass
        else:
            raise AssertionError("same attempt allocated twice")
        try:
            finish_claim(factory, old, succeeded=True)
        except RuntimeError:
            pass
        else:
            raise AssertionError("allocated attempt bypassed publication")
        with factory.begin() as session:
            now = session.scalar(select(func.utc_timestamp(6)))
            session.execute(update(DurableJob).where(DurableJob.id == old.job_id)
                            .values(lease_until=now - timedelta(seconds=1)))
        new = claim_next(factory, "new-worker")
        assert new is not None and new.attempt == 2
        new_execution = start_attempt(factory, new)
        assert new_execution is not None and new_execution.run_id != old_execution.run_id
        # The stale worker finishes late, after the replacement has started.
        with factory.begin() as session:
            stale_run = session.get(AgentRun, old_execution.run_id)
            stale_run.status = AgentRunStatus.COMPLETED
            stale_run.final_answer = "STALE"
            current_run = session.get(AgentRun, new_execution.run_id)
            current_run.status = AgentRunStatus.COMPLETED
            current_run.final_answer = "CURRENT"
        assert publish_attempt(factory, old) is False
        assert publish_attempt(factory, new) is True
        with factory() as session:
            assert session.get(AgentRun, root_id).final_answer == "CURRENT"
            assert session.get(DurableJob, new.job_id).published_run_id == new_execution.run_id
            attempts = session.scalars(select(DurableJobAttempt).where(
                DurableJobAttempt.job_id == new.job_id).order_by(DurableJobAttempt.number)).all()
            assert [a.status for a in attempts] == ["expired", "completed"]
            assert [a.execution_run_id for a in attempts] == [old_execution.run_id, new_execution.run_id]
            scopes = session.scalars(select(AgentRunService.service_name).where(
                AgentRunService.agent_run_id == new_execution.run_id)).all()
            assert scopes == ["payment-service"]

        # Access is checked again when an attempt starts, not just on submission.
        with factory.begin() as session:
            denied_root = AgentRun(request_id="denied", user_query="payment-service", creator_user_id=20,
                                   team_id=10, service_scope_recorded=True, status=AgentRunStatus.RUNNING)
            session.add(denied_root)
            session.flush()
            session.add(AgentRunService(agent_run_id=denied_root.id, service_name="payment-service"))
            key, body = digests("denied-test", denied_root.user_query)
            add_job(session, run_id=denied_root.id, team_id=10, user_id=20, key_digest=key,
                    request_digest=body)
            session.execute(text("DELETE FROM service_grants WHERE id=1"))
        denied = claim_next(factory, "denied-worker")
        assert denied
        try:
            start_attempt(factory, denied)
        except ValueError:
            pass
        else:
            raise AssertionError("revoked service grant was accepted")
        with factory() as session:
            attempt = session.scalar(select(DurableJobAttempt).where(DurableJobAttempt.job_id == denied.job_id))
            assert attempt.execution_run_id is None
        assert finish_claim(factory, denied, succeeded=False, error_code="SCOPE_CHANGED")

        # Publishing a pending approval requires its own valid durable checkpoint.
        with factory.begin() as session:
            session.execute(text("INSERT INTO service_grants VALUES (1, 10, 'payment-service')"))
            waiting_root = AgentRun(request_id="waiting", user_query="payment-service", creator_user_id=20,
                                    team_id=10, service_scope_recorded=True, status=AgentRunStatus.RUNNING)
            session.add(waiting_root)
            session.flush()
            waiting_id = waiting_root.id
            session.add(AgentRunService(agent_run_id=waiting_id, service_name="payment-service"))
            key, body = digests("waiting-test", waiting_root.user_query)
            add_job(session, run_id=waiting_id, team_id=10, user_id=20, key_digest=key,
                    request_digest=body)
        waiting = claim_next(factory, "waiting-worker")
        assert waiting
        waiting_execution = start_attempt(factory, waiting)
        assert waiting_execution
        with factory.begin() as session:
            child = session.get(AgentRun, waiting_execution.run_id)
            child.status = AgentRunStatus.WAITING_APPROVAL
            child.final_answer = "waiting"
        try:
            publish_attempt(factory, waiting)
        except ValueError:
            pass
        else:
            raise AssertionError("missing approval checkpoint was accepted")
        with factory.begin() as session:
            now = session.scalar(select(func.utc_timestamp(6)))
            session.add(AgentApprovalCheckpoint(agent_run_id=waiting_execution.run_id,
                        request_id=waiting_execution.request_id, state_json={},
                        expires_at=now + timedelta(hours=1)))
        assert publish_attempt(factory, waiting)
        with factory() as session:
            assert session.get(AgentRun, waiting_id).status == AgentRunStatus.WAITING_APPROVAL
            assert session.get(DurableJob, waiting.job_id).published_run_id == waiting_execution.run_id

        with factory.begin() as session:
            worker_root = AgentRun(request_id="worker", user_query="payment-service", creator_user_id=20,
                                   team_id=10, service_scope_recorded=True, status=AgentRunStatus.RUNNING)
            session.add(worker_root)
            session.flush()
            worker_root_id = worker_root.id
            session.add(AgentRunService(agent_run_id=worker_root_id, service_name="payment-service"))
            key, body = digests("worker-test", worker_root.user_query)
            worker_job = add_job(session, run_id=worker_root_id, team_id=10, user_id=20, key_digest=key,
                                 request_digest=body, retry_safe=True)
            worker_job_id = worker_job.id
        state = SimpleNamespace(session_factory=factory, intent_router=None, planner=None,
                                tool_registry=None, risk_checker=None, diagnostic_agents=None)
        real_prepare, real_multi = adapter.prepare_multi_scope, adapter.execute_multi_agent_run
        adapter.prepare_multi_scope = lambda query, router, planner: ("routing", "plan")
        def fake_multi(session, registry, risk_checker, run_id, request_id, query, routing, planned, **kwargs):
            with factory() as read_session:
                first_lease = read_session.get(DurableJob, worker_job_id).lease_until
            sleep(2)
            with factory() as read_session:
                later_lease = read_session.get(DurableJob, worker_job_id).lease_until
            assert later_lease > first_lease, "worker did not renew its lease"
            child = session.get(AgentRun, run_id)
            child.status = AgentRunStatus.COMPLETED
            child.final_answer = "worker result"
            session.commit()
        adapter.execute_multi_agent_run = fake_multi
        assert run_one(factory, "smoke-worker", lambda execution: adapter.execute_attempt(state, execution),
                       lease_seconds=20, heartbeat_seconds=0.5)
        with factory() as session:
            assert session.get(AgentRun, worker_root_id).final_answer == "worker result"
        adapter.prepare_multi_scope, adapter.execute_multi_agent_run = real_prepare, real_multi

        # Execute the real Multi-Agent orchestration and Tool Registry with an
        # in-memory MCP double returning empty records; no model or production data.
        class EmptyMcp:
            def call_tool(self, name, arguments):
                assert name in {"query_error_logs", "search_tickets", "search_knowledge"}
                return {"items": [], "total": 0}
        state.intent_router = IntentRouter(None)
        state.planner = Planner(None)
        state.tool_registry = create_default_registry(mcp_client=EmptyMcp())
        state.risk_checker = RiskChecker()
        with factory.begin() as session:
            real_root = AgentRun(request_id="real-flow", user_query="分析 payment-service 的故障",
                                 creator_user_id=20, team_id=10, service_scope_recorded=True,
                                 status=AgentRunStatus.RUNNING)
            session.add(real_root)
            session.flush()
            real_root_id = real_root.id
            session.add(AgentRunService(agent_run_id=real_root_id, service_name="payment-service"))
            key, body = digests("real-flow", real_root.user_query)
            real_job = add_job(session, run_id=real_root_id, team_id=10, user_id=20,
                               key_digest=key, request_digest=body, retry_safe=True)
            real_job_id = real_job.id
        assert run_one(factory, "real-flow-worker", lambda execution: adapter.execute_attempt(state, execution),
                       lease_seconds=60, heartbeat_seconds=5)
        with factory() as session:
            real_job = session.get(DurableJob, real_job_id)
            assert real_job.status == "completed", real_job.status
            assert session.get(AgentRun, real_root_id).status == AgentRunStatus.COMPLETED
            assert session.scalar(select(func.count()).select_from(ToolCallLog).where(
                ToolCallLog.agent_run_id == real_job.published_run_id)) >= 3
            assert session.scalar(select(func.count()).select_from(AgentTraceEvent).where(
                AgentTraceEvent.agent_run_id == real_job.published_run_id)) > 0

        # Terminate a real worker process after it has allocated an attempt,
        # then let a second worker reclaim the expired read-only job.
        with factory.begin() as session:
            interrupted_root = AgentRun(request_id="interrupted", user_query="payment-service",
                                        creator_user_id=20, team_id=10, service_scope_recorded=True,
                                        status=AgentRunStatus.RUNNING)
            session.add(interrupted_root)
            session.flush()
            interrupted_id = interrupted_root.id
            session.add(AgentRunService(agent_run_id=interrupted_id, service_name="payment-service"))
            key, body = digests("interrupt-test", interrupted_root.user_query)
            interrupted_job = add_job(session, run_id=interrupted_id, team_id=10, user_id=20,
                                      key_digest=key, request_digest=body, retry_safe=True)
            interrupted_job_id = interrupted_job.id
        proc = subprocess.Popen([sys.executable, "-u", "-m", "scripts.smoke_p3_attempt_db",
                                 "--child", suffix], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        lines = Queue()
        reader = Thread(target=lambda: lines.put(proc.stdout.readline()), daemon=True)
        reader.start()
        try:
            try:
                line = lines.get(timeout=30)
            except Empty as exc:
                raise AssertionError("worker did not claim before timeout") from exc
            if not line.startswith("CLAIMED "):
                raise AssertionError(f"worker did not start: {line!r}")
            first_run_id = int(line.split()[1])
        finally:
            proc.kill()
            proc.communicate(timeout=10)
        deadline = monotonic() + 30
        recovered = None
        while monotonic() < deadline and recovered is None:
            recovered = claim_next(factory, "restart-worker", lease_seconds=60)
            if recovered is None:
                sleep(1)
        assert recovered and recovered.job_id == interrupted_job_id and recovered.attempt == 2
        replacement = start_attempt(factory, recovered)
        assert replacement and replacement.run_id != first_run_id
        with factory.begin() as session:
            child = session.get(AgentRun, replacement.run_id)
            child.status = AgentRunStatus.COMPLETED
            child.final_answer = "recovered"
        assert publish_attempt(factory, recovered)
        with factory() as session:
            assert session.get(AgentRun, interrupted_id).final_answer == "recovered"

        # Exercise the real HTTP entry points over the same isolated tables.
        app = create_app()
        app.state.settings = SimpleNamespace(p3_durable_enabled=True)
        app.state.p3_schema_present = True
        app.state.session_factory = factory
        app.state.intent_router = IntentRouter(None)
        app.state.planner = Planner(None)
        member = SimpleNamespace(team_id=10, user_id=20, role="approver")
        def db_override():
            with factory() as session:
                yield session
        app.dependency_overrides[get_db] = db_override
        app.dependency_overrides[current_team] = lambda: member
        client = TestClient(app)
        cookie = "p3-probe-session"
        client.cookies.set("workpilot_session", cookie)
        with factory.begin() as session:
            now = session.scalar(select(func.utc_timestamp(6)))
            session.execute(text("INSERT INTO auth_sessions (id, user_id, token_hash, expires_at) VALUES (1, 20, :hash, :expiry)"),
                            {"hash": token_digest(cookie), "expiry": now + timedelta(hours=1)})
        headers = {"Idempotency-Key": "api-probe-one"}
        body = {"query": "分析 payment-service 的故障"}
        response = client.post("/agent/runs/async", headers=headers, json=body)
        assert response.status_code == 202, response.text
        api_run_id = response.json()["data"]["run_id"]
        replay = client.post("/agent/runs/async", headers=headers, json=body)
        assert replay.status_code == 202 and replay.json()["data"]["run_id"] == api_run_id
        assert client.post("/agent/runs/async", headers=headers,
                           json={"query": "分析 payment-service 的其他故障"}).status_code == 409
        assert client.post("/agent/runs/async", json=body).status_code == 422
        def api_execute(execution):
            with factory.begin() as session:
                child = session.get(AgentRun, execution.run_id)
                child.status = AgentRunStatus.COMPLETED
                child.final_answer = "API result"
                session.add(AgentTraceEvent(agent_run_id=child.id, step=1, node="supervisor",
                                             event_type="node_completed", latency_ms=1, status=TraceStatus.SUCCESS))
        assert run_one(factory, "api-worker", api_execute, lease_seconds=60, heartbeat_seconds=5)
        detail = client.get(f"/agent/runs/{api_run_id}")
        assert detail.status_code == 200 and detail.json()["data"]["final_answer"] == "API result", detail.text
        trace = client.get(f"/agent/runs/{api_run_id}/trace")
        assert trace.status_code == 200 and len(trace.json()["data"]["events"]) == 1, trace.text
        event_stream = client.get(f"/agent/runs/{api_run_id}/events")
        assert event_stream.status_code == 200 and "event: progress" in event_stream.text and "event: terminal" in event_stream.text
        event_id = re.search(r"^id: (\d+)$", event_stream.text, re.MULTILINE).group(1)
        with factory.begin() as session:
            now = session.scalar(select(func.utc_timestamp(6)))
            session.execute(text("INSERT INTO users (id, username, password_hash, role, created_at, updated_at) VALUES (20, 'probe', :password, 'developer', :now, :now)"),
                            {"password": hash_password("probe-password"), "now": now})
            session.execute(text("INSERT INTO teams (id, name, created_at, updated_at) VALUES (10, 'probe-team', :now, :now)"), {"now": now})
        real_auth_app = create_app()
        real_auth_app.state.settings = SimpleNamespace(p3_durable_enabled=True)
        real_auth_app.state.p3_schema_present = True
        real_auth_app.state.session_factory = factory
        real_auth_app.dependency_overrides[get_db] = db_override
        real_client = TestClient(real_auth_app)
        login = real_client.post("/auth/login", json={"username": "probe", "password": "probe-password"})
        assert login.status_code == 200, login.text
        reconnect = real_client.get(f"/agent/runs/{api_run_id}/events", headers={
            "X-WorkPilot-Team-Id": "10", "Last-Event-ID": event_id})
        assert reconnect.status_code == 200 and "event: terminal" in reconnect.text and "event: progress" not in reconnect.text
        assert real_client.post("/auth/logout").status_code == 200
        assert real_client.get(f"/agent/runs/{api_run_id}/events", headers={"X-WorkPilot-Team-Id": "10"}).status_code == 401
        browser_script = os.environ.get("P3_BROWSER_SMOKE_SCRIPT")
        if browser_script:
            import uvicorn
            real_auth_app.state.intent_router = IntentRouter(None)
            real_auth_app.state.planner = Planner(None)
            server = uvicorn.Server(uvicorn.Config(real_auth_app, host="127.0.0.1", port=18766,
                                                   lifespan="off", log_level="error"))
            stop_worker = Event()
            def browser_worker():
                while not stop_worker.wait(0.5):
                    run_one(factory, "browser-worker", api_execute, lease_seconds=60, heartbeat_seconds=5)
            server_thread = Thread(target=server.run, daemon=True)
            worker_thread = Thread(target=browser_worker, daemon=True)
            server_thread.start()
            worker_thread.start()
            try:
                deadline = monotonic() + 15
                while not server.started and monotonic() < deadline:
                    sleep(0.1)
                assert server.started
                checked = subprocess.run(["node", browser_script], capture_output=True, text=True, timeout=90)
                assert checked.returncode == 0, checked.stderr
                print(checked.stdout.strip(), flush=True)
            finally:
                stop_worker.set()
                worker_thread.join(timeout=20)
                server.should_exit = True
                server_thread.join(timeout=10)
        history = client.get("/agent/runs?page=1&page_size=100")
        assert history.status_code == 200, history.text
        with factory() as session:
            job = session.scalar(select(DurableJob).where(DurableJob.agent_run_id == api_run_id))
            child_id = job.published_run_id
        assert child_id not in [item["id"] for item in history.json()["data"]["items"]]
        assert client.get(f"/agent/runs/{child_id}").status_code == 404
        attempts = client.get(f"/agent/runs/{api_run_id}/attempts")
        assert attempts.status_code == 200 and len(attempts.json()["data"]) == 1
        assert "claim_token" not in attempts.text
        member.team_id = 99
        assert client.get(f"/agent/runs/{api_run_id}").status_code == 404
        member.team_id = 10
        app.state.agent_graph = build_agent_graph(InMemorySaver())
        app.state.tool_registry = state.tool_registry
        app.state.risk_checker = state.risk_checker
        app.state.incident_analyzer = IncidentAnalyzer(None)
        approval = client.post("/agent/runs/async", headers={"Idempotency-Key": "approval-probe"},
                               json={"query": "模拟重启 payment-service"})
        assert approval.status_code == 202, approval.text
        approval_id = approval.json()["data"]["run_id"]
        state.agent_graph = app.state.agent_graph
        state.incident_analyzer = app.state.incident_analyzer
        assert run_one(factory, "approval-worker", lambda execution: adapter.execute_attempt(state, execution),
                       lease_seconds=60, heartbeat_seconds=5)
        waiting_detail = client.get(f"/agent/runs/{approval_id}")
        assert waiting_detail.json()["data"]["status"] == "waiting_approval", waiting_detail.text
        restarted = subprocess.run([sys.executable, "-m", "scripts.smoke_p3_attempt_db", "--reconcile", suffix],
                                   capture_output=True, text=True, timeout=45)
        assert restarted.returncode == 0, restarted.stderr
        approved = client.post(f"/agent/runs/{approval_id}/approval", json={"action": "approve"})
        assert approved.status_code == 200 and approved.json()["data"]["status"] == "completed", approved.text
        assert client.post(f"/agent/runs/{approval_id}/approval", json={"action": "approve"}).status_code == 409
        unknown = client.post("/agent/runs/async", headers={"Idempotency-Key": "approval-unknown"},
                              json={"query": "模拟重启 payment-service"})
        assert unknown.status_code == 202, unknown.text
        unknown_id = unknown.json()["data"]["run_id"]
        assert run_one(factory, "unknown-worker", lambda execution: adapter.execute_attempt(state, execution),
                       lease_seconds=60, heartbeat_seconds=5)
        with factory.begin() as session:
            unknown_job = session.scalar(select(DurableJob).where(DurableJob.agent_run_id == unknown_id))
            checkpoint = session.get(AgentApprovalCheckpoint, unknown_job.published_run_id)
            checkpoint.decision = "approve"
            locked_request_id = checkpoint.request_id
            locked_run_id = unknown_job.published_run_id
            session.get(AgentRun, unknown_job.published_run_id).status = AgentRunStatus.RUNNING
        if os.environ.get("P3_EXPERIMENTAL_APPROVAL_LOCK_TEST") == "1":
            holder = subprocess.Popen([sys.executable, "-u", "-m", "scripts.smoke_p3_attempt_db",
                                       "--hold-approval", locked_request_id], stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True)
            ready = Queue()
            Thread(target=lambda: ready.put(holder.stdout.readline()), daemon=True).start()
            try:
                assert ready.get(timeout=30).strip() == "LOCKED"
                active_recovery = subprocess.run([sys.executable, "-m", "scripts.smoke_p3_attempt_db", "--reconcile", suffix],
                                                 capture_output=True, text=True, timeout=45)
                assert active_recovery.returncode == 0, active_recovery.stderr
                with factory() as session:
                    assert session.get(AgentRun, locked_run_id).status == AgentRunStatus.RUNNING
                    assert session.get(AgentApprovalCheckpoint, locked_run_id) is not None
            finally:
                holder.kill()
                holder.communicate(timeout=10)
        restarted = subprocess.run([sys.executable, "-m", "scripts.smoke_p3_attempt_db", "--reconcile", suffix],
                                   capture_output=True, text=True, timeout=45)
        assert restarted.returncode == 0, restarted.stderr
        unknown_detail = client.get(f"/agent/runs/{unknown_id}")
        assert unknown_detail.json()["data"]["status"] == "failed", unknown_detail.text
        assert unknown_detail.json()["data"]["final_answer"] == "APPROVAL_EXECUTION_OUTCOME_UNKNOWN"
        assert client.post(f"/agent/runs/{unknown_id}/approval", json={"action": "approve"}).status_code == 409
        barrier = Barrier(2)
        def concurrent_submit(_):
            barrier.wait(timeout=15)
            return client.post("/agent/runs/async", headers={"Idempotency-Key": "concurrent-probe"}, json=body)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(concurrent_submit, range(2)))
        assert all(r.status_code == 202 for r in responses), [(r.status_code, r.text) for r in responses]
        assert len({r.json()["data"]["run_id"] for r in responses}) == 1
        print("P3 smoke passed: isolation, process recovery, concurrent API, history, SSE and cross-process approval")
    finally:
        if rewrite is not None and event.contains(engine, "before_cursor_execute", rewrite):
            event.remove(engine, "before_cursor_execute", rewrite)
        for table in reversed(created):
            with engine.begin() as connection:
                connection.execute(text(f"DROP TABLE `{table}`"))
        engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--child":
        child_process(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == "--reconcile":
        reconcile_process(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == "--hold-approval":
        hold_approval(sys.argv[2])
    else:
        main()
