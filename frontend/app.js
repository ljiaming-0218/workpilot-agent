"use strict";

const state = {
  result: null,
  trace: [],
  runs: [],
  historyTotal: 0,
  activeContent: "plan",
  activeData: "tickets",
  tickets: [],
  logs: [],
  knowledge: [],
  busy: false,
  importing: false,
  user: null,
  teamId: null,
  collaboration: null,
  collaborationBusy: false,
};

const importExamples = {
  logs: {
    items: [
      {
        service_name: "payment-service",
        level: "ERROR",
        error_type: "DB_TIMEOUT",
        message: "Database connection timed out after 3000 ms",
        stack_trace: null,
        request_id: "external-log-001",
        created_at: new Date().toISOString(),
      },
      {
        service_name: "order-service",
        level: "ERROR",
        error_type: "REDIS_TIMEOUT",
        message: "Redis GET exceeded 1000 ms",
        stack_trace: null,
        request_id: "external-log-002",
        created_at: new Date().toISOString(),
      },
      {
        service_name: "auth-service",
        level: "WARNING",
        error_type: "CLOCK_SKEW",
        message: "JWT validation detected clock difference between instances",
        stack_trace: null,
        request_id: "external-log-003",
        created_at: new Date().toISOString(),
      },
    ],
  },
  knowledge: {
    items: [
      {
        title: "payment-service 数据库超时排查",
        content: "检查连接池占用、慢查询、长事务和数据库响应延迟。",
        category: "database",
        source: "external://runbooks/payment-db-timeout",
      },
      {
        title: "order-service Redis 超时排查",
        content: "检查 Redis P99 延迟、热点 Key、连接数和网络抖动。",
        category: "runbook",
        source: "external://runbooks/order-redis-timeout",
      },
      {
        title: "auth-service JWT 时钟偏差排查",
        content: "检查系统时间、NTP 同步、clock skew 和签名密钥版本。",
        category: "security",
        source: "external://runbooks/auth-clock-skew",
      },
    ],
  },
};

const ui = {
  loginScreen: document.querySelector("#login-screen"),
  loginForm: document.querySelector("#login-form"),
  loginUsername: document.querySelector("#login-username"),
  loginPassword: document.querySelector("#login-password"),
  loginError: document.querySelector("#login-error"),
  loginSubmit: document.querySelector("#login-submit"),
  accountControls: document.querySelector("#account-controls"),
  accountName: document.querySelector("#account-name"),
  teamSelect: document.querySelector("#team-select"),
  logout: document.querySelector("#logout"),
  shell: document.querySelector(".app-shell"),
  form: document.querySelector("#agent-form"),
  query: document.querySelector("#agent-query"),
  queryCount: document.querySelector("#query-count"),
  runButton: document.querySelector("#run-agent"),
  runTitle: document.querySelector("#run-title"),
  runStatus: document.querySelector("#run-status"),
  runRisk: document.querySelector("#run-risk"),
  runId: document.querySelector("#run-id"),
  runIntent: document.querySelector("#run-intent"),
  runRouting: document.querySelector("#run-routing"),
  runSteps: document.querySelector("#run-steps"),
  approvalPanel: document.querySelector("#approval-panel"),
  approvalReason: document.querySelector("#approval-reason"),
  planCount: document.querySelector("#plan-count"),
  evidenceCount: document.querySelector("#evidence-count"),
  contentView: document.querySelector("#content-view"),
  traceList: document.querySelector("#trace-list"),
  traceCount: document.querySelector("#trace-count"),
  traceLatency: document.querySelector("#trace-latency"),
  traceFilter: document.querySelector("#trace-filter"),
  dataList: document.querySelector("#data-list"),
  dataListTitle: document.querySelector("#data-list-title"),
  dataListMeta: document.querySelector("#data-list-meta"),
  ticketCount: document.querySelector("#ticket-count"),
  logCount: document.querySelector("#log-count"),
  knowledgeCount: document.querySelector("#knowledge-count"),
  serviceCount: document.querySelector("#service-count"),
  recentRuns: document.querySelector("#recent-runs"),
  runCount: document.querySelector("#run-count"),
  systemState: document.querySelector("#system-state"),
  systemStateLabel: document.querySelector("#system-state-label"),
  toast: document.querySelector("#toast"),
  importDialog: document.querySelector("#import-dialog"),
  importForm: document.querySelector("#import-form"),
  importType: document.querySelector("#import-type"),
  importKey: document.querySelector("#import-key"),
  importFile: document.querySelector("#import-file"),
  importJson: document.querySelector("#import-json"),
  importFeedback: document.querySelector("#import-feedback"),
  submitImport: document.querySelector("#submit-import"),
  logFields: document.querySelector("#log-import-fields"),
  knowledgeFields: document.querySelector("#knowledge-import-fields"),
  logServiceName: document.querySelector("#log-service-name"),
  logLevel: document.querySelector("#log-level"),
  logErrorType: document.querySelector("#log-error-type"),
  logRequestId: document.querySelector("#log-request-id"),
  logMessage: document.querySelector("#log-message"),
  logStackTrace: document.querySelector("#log-stack-trace"),
  knowledgeTitle: document.querySelector("#knowledge-title"),
  knowledgeCategory: document.querySelector("#knowledge-category"),
  knowledgeSource: document.querySelector("#knowledge-source"),
  knowledgeContent: document.querySelector("#knowledge-content"),
};

function createElement(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function setBusy(busy, label = "Run Agent") {
  state.busy = busy;
  ui.runButton.disabled = busy;
  ui.teamSelect.disabled = busy;
  ui.logout.disabled = busy;
  ui.runButton.querySelector("span").textContent = busy ? "Running…" : label;
  document.querySelectorAll("[data-approval]").forEach((button) => {
    button.disabled = busy || !canApprove();
  });
}

function canApprove() {
  const team = (state.user?.teams || []).find((item) => String(item.id) === String(state.teamId));
  return team?.role === "approver" || team?.role === "admin";
}

async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(state.teamId ? {"X-WorkPilot-Team-Id": String(state.teamId)} : {}),
      ...(options.headers || {}),
    },
  });
  let body;
  try {
    body = await response.json();
  } catch {
    throw new Error(`服务返回了无法解析的响应（HTTP ${response.status}）`);
  }
  if (!response.ok || body.success === false) {
    if (response.status === 401 && !path.startsWith("/auth/")) showLogin();
    const message = body.error?.message || body.detail || `请求失败（HTTP ${response.status}）`;
    throw new Error(message);
  }
  return body.data ?? body;
}

function showLogin() {
  state.user = null;
  state.teamId = null;
  ui.loginScreen.hidden = false;
  ui.accountControls.hidden = true;
  ui.loginPassword.value = "";
}

function resetTeamView() {
  state.result = null;
  state.collaboration = null;
  state.runs = [];
  state.trace = [];
  ui.runTitle.textContent = "等待执行任务";
  ui.runStatus.textContent = "IDLE";
  ui.runStatus.className = "status-badge idle";
  ui.runRisk.textContent = "—";
  ui.runId.textContent = ui.runIntent.textContent = ui.runRouting.textContent = ui.runSteps.textContent = "—";
  ui.planCount.textContent = ui.evidenceCount.textContent = "0";
  ui.contentView.replaceChildren(createElement("p", "empty-copy", "提交任务后查看调查结果。"));
  ui.approvalPanel.hidden = true;
  renderTrace([]);
  renderRecentRuns();
}

function acceptUser(user) {
  state.user = user;
  ui.accountName.textContent = user.username;
  ui.teamSelect.replaceChildren();
  for (const team of user.teams || []) {
    const option = document.createElement("option");
    option.value = String(team.id);
    option.textContent = team.name;
    ui.teamSelect.append(option);
  }
  state.teamId = ui.teamSelect.value || null;
  ui.accountControls.hidden = false;
  ui.loginScreen.hidden = true;
  ui.runButton.disabled = !state.teamId;
  resetTeamView();
  if (state.teamId) {
    loadBusinessData();
    loadRunHistory();
  } else {
    ui.dataList.replaceChildren(createElement("p", "empty-copy", "当前账号尚未加入团队。"));
    showToast("当前账号尚未加入团队，请联系管理员。", true);
  }
}

async function restoreSession() {
  try {
    acceptUser(await requestJson("/auth/me"));
  } catch {
    showLogin();
  }
}

ui.loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  ui.loginSubmit.disabled = true;
  ui.loginError.textContent = "";
  try {
    const user = await requestJson("/auth/login", {method: "POST", body: JSON.stringify({
      username: ui.loginUsername.value.trim(), password: ui.loginPassword.value,
    })});
    ui.loginPassword.value = "";
    acceptUser(user);
  } catch (error) {
    ui.loginError.textContent = error.message;
  } finally {
    ui.loginSubmit.disabled = false;
  }
});

ui.logout.addEventListener("click", async () => {
  try {
    await requestJson("/auth/logout", {method: "POST"});
    resetTeamView();
    showLogin();
  } catch (error) {
    showToast(error.message, true);
  }
});

ui.teamSelect.addEventListener("change", () => {
  state.teamId = ui.teamSelect.value || null;
  resetTeamView();
  loadBusinessData();
  loadRunHistory();
});

async function checkHealth() {
  try {
    const health = await requestJson("/health");
    const connected = health.status === "ok" && health.database === "connected";
    ui.systemState.className = `system-state ${connected ? "connected" : "disconnected"}`;
    ui.systemStateLabel.textContent = connected ? "Operational" : "Database unavailable";
  } catch {
    ui.systemState.className = "system-state disconnected";
    ui.systemStateLabel.textContent = "Service unavailable";
  }
}

function setImportExample() {
  ui.importJson.value = JSON.stringify(importExamples[ui.importType.value], null, 2);
  const isLogs = ui.importType.value === "logs";
  ui.logFields.hidden = !isLogs;
  ui.knowledgeFields.hidden = isLogs;
  ui.logFields.querySelectorAll("input, select, textarea").forEach((field) => { field.disabled = !isLogs; });
  ui.knowledgeFields.querySelectorAll("input, select, textarea").forEach((field) => { field.disabled = isLogs; });
  ui.importFeedback.textContent = "";
  ui.importFeedback.className = "import-feedback";
}

function openImportDialog() {
  const uniqueSuffix = Date.now();
  ui.logRequestId.value = `manual-log-${uniqueSuffix}`;
  ui.knowledgeSource.value = `manual://knowledge/${uniqueSuffix}`;
  setImportExample();
  ui.importDialog.showModal();
}

function closeImportDialog() {
  if (!state.importing) ui.importDialog.close();
}

async function loadImportFile() {
  const file = ui.importFile.files?.[0];
  if (!file) return;
  try {
    ui.importJson.value = await file.text();
    ui.importFeedback.textContent = `已读取 ${file.name}`;
    ui.importFeedback.className = "import-feedback";
  } catch {
    ui.importFeedback.textContent = "无法读取所选文件。";
    ui.importFeedback.className = "import-feedback error";
  }
}

async function submitImport(event) {
  event.preventDefault();
  if (state.importing) return;
  let payload;
  try {
    if (event.submitter?.dataset.importMode === "batch") {
      payload = JSON.parse(ui.importJson.value);
      if (Array.isArray(payload)) payload = { items: payload };
      if (!payload || !Array.isArray(payload.items)) {
        throw new Error("JSON 顶层必须是数组，或包含 items 数组的对象。");
      }
    } else if (ui.importType.value === "logs") {
      payload = { items: [{
        service_name: ui.logServiceName.value,
        level: ui.logLevel.value,
        error_type: ui.logErrorType.value || null,
        message: ui.logMessage.value,
        stack_trace: ui.logStackTrace.value || null,
        request_id: ui.logRequestId.value,
        created_at: new Date().toISOString(),
      }] };
    } else {
      payload = { items: [{
        title: ui.knowledgeTitle.value,
        category: ui.knowledgeCategory.value || null,
        source: ui.knowledgeSource.value,
        content: ui.knowledgeContent.value,
      }] };
    }
  } catch (error) {
    ui.importFeedback.textContent = error.message;
    ui.importFeedback.className = "import-feedback error";
    return;
  }

  state.importing = true;
  document.querySelectorAll("[data-import-mode]").forEach((button) => { button.disabled = true; });
  ui.submitImport.querySelector("span").textContent = "Importing…";
  ui.importFeedback.textContent = "正在校验并写入数据库…";
  ui.importFeedback.className = "import-feedback";
  try {
    const result = await requestJson(`/ingestion/${ui.importType.value}`, {
      method: "POST",
      headers: { "X-Ingestion-Key": ui.importKey.value },
      body: JSON.stringify(payload),
    });
    ui.importFeedback.textContent = `完成：新建 ${result.created}，更新 ${result.updated}，跳过 ${result.skipped}`;
    showToast(ui.importFeedback.textContent);
    await loadBusinessData(ui.importType.value === "logs" ? "logs" : "knowledge");
  } catch (error) {
    ui.importFeedback.textContent = error.message;
    ui.importFeedback.className = "import-feedback error";
  } finally {
    state.importing = false;
    document.querySelectorAll("[data-import-mode]").forEach((button) => { button.disabled = false; });
    ui.submitImport.querySelector("span").textContent = "添加这条数据";
  }
}

async function runAgent(event) {
  event.preventDefault();
  const query = ui.query.value.trim();
  if (!query || state.busy) return;

  setBusy(true);
  ui.runTitle.textContent = "正在执行 Agent…";
  ui.runStatus.textContent = "RUNNING";
  ui.runStatus.className = "status-badge idle";
  ui.approvalPanel.hidden = true;
  state.trace = [];
  renderTrace([]);
  let submitted = false;
  try {
    const accepted = await requestJson("/agent/runs/async", {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({ query }),
    });
    submitted = true;
    acceptResult({
      run_id: accepted.run_id,
      request_id: accepted.request_id,
      intent: null,
      status: accepted.status,
      queue_status: accepted.queue_status,
      final_answer: null,
      plan: [],
      evidence: [],
      routing_source: null,
      routing_confidence: null,
      planner_source: null,
      risk_level: null,
      risk_reasons: [],
      steps_executed: 0,
      mode: "multi",
    }, query);
    const result = await streamAgentRun(accepted, query);
    await loadRunHistory();
    showToast(`Agent Run #${result.run_id} 已返回 ${result.status} 状态。`);
  } catch (error) {
    if (!submitted) ui.runTitle.textContent = "任务提交失败";
    ui.runStatus.textContent = submitted ? "进度中断 · 请刷新任务" : "提交失败";
    ui.runStatus.className = "status-badge idle";
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

function streamAgentRun(accepted, query) {
  return new Promise((resolve, reject) => {
    const eventsUrl = new URL(accepted.events_url, window.location.origin);
    if (state.teamId) eventsUrl.searchParams.set("team_id", state.teamId);
    const source = new EventSource(eventsUrl);
    let settled = false;

    if (accepted.queue_status === "queued") {
      ui.runStatus.textContent = "QUEUED";
    }
    source.addEventListener("attempt", () => {
      if (state.result?.run_id !== accepted.run_id) return;
      state.trace = [];
      renderTrace();
    });

    source.addEventListener("progress", (event) => {
      const progress = JSON.parse(event.data);
      if (state.result?.run_id !== accepted.run_id) return;
      state.trace.push(progress);
      renderTrace();
    });

    source.addEventListener("terminal", async (event) => {
      if (settled) return;
      settled = true;
      source.close();
      try {
        const terminal = JSON.parse(event.data);
        let result = terminal.result;
        if (!result && terminal.error) {
          throw new Error(`Agent 后台执行失败：${terminal.error}`);
        }
        if (!result) {
          const detail = await requestJson(`/agent/runs/${accepted.run_id}`);
          result = resultFromRunDetail(detail);
        }
        acceptResult(result, query);
        await loadTrace(accepted.run_id);
        resolve(result);
      } catch (error) {
        reject(error);
      }
    });

    source.onerror = () => {
      if (settled) return;
      settled = true;
      source.close();
      reject(new Error("实时进度连接中断，请从运行历史查看任务终态。"));
    };
  });
}

function acceptResult(result, query) {
  state.result = result;
  state.collaboration = null;
  state.activeContent = "plan";
  const existing = state.runs.findIndex((item) => (item.result?.run_id || item.summary?.id) === result.run_id);
  const runEntry = { query, result, trace: [], summary: { id: result.run_id, status: result.status, intent: result.intent } };
  if (existing >= 0) state.runs.splice(existing, 1);
  state.runs.unshift(runEntry);
  state.runs = state.runs.slice(0, 8);
  renderResult();
  renderRecentRuns();
}

function renderResult() {
  const result = state.result;
  if (!result) return;

  const currentRun = state.runs.find((item) => (item.result?.run_id || item.summary?.id) === result.run_id);
  ui.runTitle.textContent = currentRun?.query || `Agent Run #${result.run_id}`;
  const reviewState = result.verifier?.status !== "completed" && result.verifier
    ? "REVIEW UNAVAILABLE"
    : result.verifier?.claims?.some((claim) => claim.support === "unsupported")
      ? "REVIEW ISSUES" : null;
  ui.runStatus.textContent = [result.queue_status || result.status, result.investigation_status === "partial" ? "PARTIAL" : null, reviewState]
    .filter(Boolean).join(" · ").replaceAll("_", " ").toUpperCase();
  ui.runStatus.className = `status-badge ${statusClass(result.status)}`;
  ui.runRisk.textContent = `${result.risk_level || "—"} RISK`;
  ui.runRisk.className = `risk-badge ${(result.risk_level || "").toLowerCase()}`;
  ui.runId.textContent = result.run_id;
  ui.runIntent.textContent = `${result.intent || "—"} · ${result.mode || "multi"}`;
  ui.runRouting.textContent = `${result.routing_source || "—"} · ${formatConfidence(result.routing_confidence)}`;
  ui.runSteps.textContent = `${result.steps_executed || 0} / ${(result.plan || []).length}`;
  ui.planCount.textContent = (result.plan || []).length;
  ui.evidenceCount.textContent = (result.evidence || []).length;

  const waiting = result.status === "waiting_approval";
  ui.approvalPanel.hidden = !waiting;
  if (waiting) {
    const reasons = result.risk_reasons || [];
    ui.approvalReason.textContent = reasons.length
      ? reasons.join("；")
      : "高风险计划尚未执行任何工具。";
    if (!canApprove()) ui.approvalReason.textContent += " 请联系本团队审批人处理。";
    document.querySelectorAll("[data-approval]").forEach((button) => {
      button.disabled = state.busy || !canApprove();
    });
  }
  document.querySelectorAll(".content-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.content === state.activeContent);
    button.setAttribute("aria-selected", String(button.dataset.content === state.activeContent));
  });
  renderRunContent();
}

function renderRunContent() {
  ui.contentView.replaceChildren();
  const result = state.result;
  if (!result) return;

  if (state.activeContent === "plan") {
    renderPlan(result.plan || [], result.agent_tasks || [], result.tool_budget, result.verifier);
  } else if (state.activeContent === "evidence") {
    renderEvidence(result.evidence || []);
  } else if (state.activeContent === "collaboration") {
    renderCollaboration();
  } else {
    const answer = createElement("div", "answer-content", result.final_answer || "当前没有最终回答。");
    ui.contentView.append(answer);
  }
}

function renderPlan(plan, tasks = [], budget = null, verifier = null) {
  if (tasks.length) {
    const section = createElement("div", "task-summary");
    section.append(createElement("strong", "", "Agent 任务"));
    if (budget) section.append(createElement("p", "", `工具预算：${budget.used} / ${budget.limit} 次尝试`));
    tasks.forEach((task) => {
      const detail = task.agent_id === "workflow_specialist"
        ? `执行 ${task.attempts_used} 步`
        : `证据 ${task.evidence_ids.length} 条 · 尝试 ${task.attempts_used} 次`;
      const row = createElement("p", "", `${task.agent_id} → ${task.service} · ${task.status} · ${task.stop_reason} · ${detail}`);
      if (task.errors.length) row.append(createElement("span", "", ` · 错误 ${task.errors.join(", ")}`));
      section.append(row);
    });
    ui.contentView.append(section);
  }
  if (verifier) {
    const section = createElement("div", "task-summary");
    section.append(createElement("strong", "", `证据核验：${verifier.status}`));
    if (verifier.error) section.append(createElement("p", "", verifier.error));
    (verifier.claims || []).forEach((claim) => {
      section.append(createElement("p", "", `${claim.claim_id} · ${claim.claim_type} · ${claim.support} · ${claim.text} · ${claim.reason} · ${claim.evidence_ids.join(", ")}`));
    });
    ui.contentView.append(section);
  }
  if (!plan.length) {
    ui.contentView.append(emptyBlock(state.result?.status === "running"
      ? "任务排队或执行中，计划在结果发布后显示。"
      : "当前没有已发布的工具计划。"));
    return;
  }
  const list = createElement("ol", "plan-list");
  plan.forEach((step, index) => {
    const item = createElement("li", "plan-step");
    item.append(createElement("span", "step-number", String(index + 1)));
    const body = createElement("div", "step-body");
    body.append(createElement("strong", "", humanize(step.tool)));
    body.append(createElement("div", "json-preview", JSON.stringify(step.inputs || {})));
    item.append(body);
    list.append(item);
  });
  ui.contentView.append(list);
}

function renderEvidence(evidence) {
  if (!evidence.length) {
    ui.contentView.append(emptyBlock("这次运行没有产生可展示的证据。"));
    return;
  }
  const list = createElement("div", "evidence-list");
  evidence.forEach((item, index) => {
    const row = createElement("article", "evidence-item");
    row.append(createElement("strong", "", `${item.evidence_id || `Evidence ${index + 1}`} · ${item.service || ""} · ${humanize(item.tool || item.source || "result")}`));
    const ids = item.source_record_ids || (item.output?.items || []).map((record) => record.id ?? record.document_id).filter((id) => id != null);
    row.append(createElement("p", "evidence-source", `记录来源：${humanize(item.tool || "unknown")} · 原始记录 ID：${ids.join(", ") || "无"}。空查询仅表示未检索到相关记录。`));
    const pre = createElement("pre", "", prettyJson(item));
    row.append(pre);
    list.append(row);
  });
  ui.contentView.append(list);
}

async function submitApproval(action) {
  if (!state.result || state.busy) return;
  setBusy(true);
  try {
    const result = await requestJson(`/agent/runs/${state.result.run_id}/approval`, {
      method: "POST",
      body: JSON.stringify({ action }),
    });
    const entry = state.runs.find((item) => (item.result?.run_id || item.summary?.id) === result.run_id);
    const query = entry?.query || ui.query.value.trim();
    acceptResult(result, query);
    await loadTrace(result.run_id);
    await loadRunHistory();
    showToast(`审批动作 ${action} 已处理。`);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function loadTrace(runId) {
  try {
    const trace = await requestJson(`/agent/runs/${runId}/trace`);
    if (state.result?.run_id !== runId) return;
    state.trace = trace.events || [];
    const entry = state.runs.find((item) => (item.result?.run_id || item.summary?.id) === runId);
    if (entry) entry.trace = state.trace;
    renderTrace();
  } catch (error) {
    state.trace = [];
    renderTrace();
    showToast(`Trace 加载失败：${error.message}`, true);
  }
}

async function loadRunHistory() {
  try {
    const page = await requestJson("/agent/runs?page=1&page_size=8");
    state.historyTotal = page.total;
    state.runs = page.items.map((summary) => {
      const previous = state.runs.find((entry) => (entry.result?.run_id || entry.summary?.id) === summary.id);
      return { query: summary.user_query, summary, result: previous?.result || null, trace: previous?.trace || [] };
    });
    renderRecentRuns();
    const linkedRun = new URLSearchParams(window.location.search).get("run_id");
    if (!state.result && /^[1-9]\d*$/.test(linkedRun || "")) {
      await openHistoricRun({summary: {id: Number(linkedRun)}});
    }
  } catch (error) {
    renderRecentRuns();
    showToast(`运行历史加载失败：${error.message}`, true);
  }
}

async function openHistoricRun(entry) {
  const runId = entry.summary.id;
  try {
    const detail = await requestJson(`/agent/runs/${runId}`);
    const result = resultFromRunDetail(detail);
    entry.result = result;
    entry.query = detail.user_query;
    state.result = result;
    state.collaboration = null;
    state.activeContent = "plan";
    renderResult();
    renderRecentRuns();
    setMobileView("workspace");
    await loadTrace(runId);
  } catch (error) {
    showToast(`运行详情加载失败：${error.message}`, true);
  }
}

function resultFromRunDetail(detail) {
  return {
    run_id: detail.id,
    request_id: detail.request_id,
    intent: detail.intent,
    status: detail.status,
    queue_status: detail.queue_status,
    final_answer: detail.final_answer,
    plan: detail.plan,
    evidence: detail.evidence,
    routing_source: detail.routing_source,
    routing_confidence: detail.routing_confidence,
    planner_source: detail.planner_source,
    risk_level: detail.risk_level,
    risk_reasons: detail.risk_reasons,
    steps_executed: detail.steps_executed,
    mode: detail.mode,
    agent_tasks: detail.agent_tasks,
    investigation_status: detail.investigation_status,
    tool_budget: detail.tool_budget,
    verifier: detail.verifier,
  };
}

const handlingLabels = {open: "待认领", in_progress: "处理中", resolved: "已处理"};
const feedbackLabels = {unreviewed: "待人工核实", supported: "人工认为证据支持", needs_correction: "需要修正", inconclusive: "证据不足"};
const originLabels = {synthetic: "合成案例", local_test: "本地真实测试", external: "外部数据", unspecified: "未标记来源"};

async function loadCollaboration() {
  const runId = state.result?.run_id;
  const teamId = state.teamId;
  if (!runId) return;
  try {
    const data = await requestJson(`/agent/runs/${runId}/collaboration`);
    if (state.teamId !== teamId || state.result?.run_id !== runId) return;
    state.collaboration = data;
    if (state.activeContent === "collaboration") renderRunContent();
  } catch (error) {
    if (state.teamId !== teamId || state.result?.run_id !== runId) return;
    if (state.activeContent !== "collaboration") return;
    ui.contentView.replaceChildren(emptyBlock(`协作记录加载失败：${error.message}`));
  }
}

function reviewSelect(id, values, selected) {
  const select = createElement("select");
  select.id = id;
  for (const [value, text] of Object.entries(values)) {
    const option = createElement("option", "", text);
    option.value = value;
    option.selected = String(selected ?? "") === value;
    select.append(option);
  }
  return select;
}

function renderCollaboration() {
  const data = state.collaboration;
  const section = createElement("section", "collaboration");
  ui.contentView.append(section);
  section.append(createElement("strong", "", "团队调查与交接"),
    createElement("p", "review-hint", `执行状态：${state.result.queue_status || state.result.status}。人工处理状态独立保存，任务完成不代表根因已经确认。`));
  const refresh = createElement("button", "secondary-button", "刷新任务与协作");
  refresh.type = "button";
  refresh.addEventListener("click", async () => {
    const runId = state.result.run_id;
    const teamId = state.teamId;
    refresh.disabled = true;
    try {
      const detail = await requestJson(`/agent/runs/${runId}`);
      if (state.teamId !== teamId || state.result?.run_id !== runId) return;
      state.result = resultFromRunDetail(detail);
      await loadCollaboration();
      await loadTrace(runId);
      await loadRunHistory();
      renderResult();
    } catch (error) { showToast(error.message, true); }
    finally { refresh.disabled = false; }
  });
  section.append(refresh);
  if (!data) {
    section.append(createElement("p", "", "正在加载负责人、反馈和备注…"));
    return;
  }
  const incident = data.incident;
  section.append(createElement("p", "review-origin", incident
    ? `告警来源：${incident.source} / ${incident.event_id} · ${originLabels[incident.source_kind]}（提交方声明） · ${incident.payload.alert_name}`
    : "此任务没有告警来源标记；不计入真实案例。"));

  const issues = [];
  if (data.feedback === "unreviewed") issues.push("调查结论尚待人工核实。");
  for (const task of state.result.agent_tasks || []) {
    if (task.status !== "completed") issues.push(`${task.service}：${task.status}；${(task.errors || []).join("，") || task.stop_reason || "请核对证据缺口"}`);
  }
  if (state.result.verifier?.status !== "completed") issues.push("未取得完整核验结果，请核对原始记录。");
  for (const claim of state.result.verifier?.claims || []) {
    if (claim.support !== "supported") issues.push(`${claim.support}：${claim.text}`);
  }
  const pending = createElement("div", "review-pending");
  pending.append(createElement("strong", "", "待确认事项"));
  for (const issue of issues.length ? issues : ["当前无自动标出的缺口；人工反馈不等于独立根因验证。"]) {
    pending.append(createElement("p", "", issue));
  }
  section.append(pending);

  const form = createElement("form", "review-form");
  const memberChoices = {"": "未分配"};
  for (const member of data.members) memberChoices[member.id] = member.username;
  if (data.owner_user_id && !data.members.some((member) => member.id === data.owner_user_id)) {
    memberChoices[data.owner_user_id] = `原负责人 #${data.owner_user_id}（已离开团队，请重新分配）`;
  }
  const owner = reviewSelect("review-owner", memberChoices, data.owner_user_id);
  const handling = reviewSelect("review-status", handlingLabels, data.handling_status);
  const feedback = reviewSelect("review-feedback", feedbackLabels, data.feedback);
  for (const [title, control] of [["负责人", owner], ["人工处理状态", handling], ["结果反馈", feedback]]) {
    const label = createElement("label", "", title);
    label.append(control);
    form.append(label);
  }
  const claim = createElement("button", "secondary-button", "由我认领");
  claim.type = "button";
  claim.addEventListener("click", () => { owner.value = String(state.user.id); handling.value = "in_progress"; });
  const noteLabel = createElement("label", "", "处理说明 / 新备注");
  const note = createElement("textarea");
  note.id = "review-note";
  note.rows = 3;
  note.maxLength = 4000;
  note.placeholder = "记录已核对的事实、需要补查的事项或交接说明";
  noteLabel.append(note);
  const save = createElement("button", "primary-button", "保存协作记录");
  save.type = "submit";
  save.disabled = state.collaborationBusy;
  form.append(claim, noteLabel, save);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.collaborationBusy) return;
    const runId = data.run_id;
    const teamId = state.teamId;
    state.collaborationBusy = true;
    save.disabled = true;
    try {
      const updated = await requestJson(`/agent/runs/${runId}/collaboration`, {
        method: "PATCH", body: JSON.stringify({expected_version: data.version,
          owner_user_id: owner.value ? Number(owner.value) : null, handling_status: handling.value,
          feedback: feedback.value, note: note.value.trim()}),
      });
      if (state.teamId !== teamId || state.result?.run_id !== runId) return;
      state.collaboration = updated;
      state.collaborationBusy = false;
      renderRunContent();
      showToast("协作记录已保存。");
    } catch (error) { showToast(error.message, true); }
    finally { state.collaborationBusy = false; save.disabled = false; }
  });
  section.append(form);
  const history = createElement("div", "review-history");
  history.append(createElement("strong", "", `交接记录（最近 ${data.notes.length} / ${data.notes_total} 条）`));
  const nameFor = (id) => data.members.find((member) => member.id === id)?.username || `用户 #${id}`;
  for (const item of data.notes) {
    const row = createElement("article");
    row.append(createElement("small", "", `v${item.version} · ${nameFor(item.actor_user_id)} · ${formatBeijing(item.created_at)}`),
      createElement("p", "", `${handlingLabels[item.handling_status]} · ${feedbackLabels[item.feedback]} · 负责人：${item.owner_user_id ? nameFor(item.owner_user_id) : "未分配"}`),
      createElement("p", "review-note", item.note || "（仅更新状态）"));
    history.append(row);
  }
  section.append(history);
  const stats = createElement("details", "review-statistics");
  stats.append(createElement("summary", "", "团队案例统计（按提交方声明来源分组）"));
  stats.addEventListener("toggle", async () => {
    if (!stats.open || stats.dataset.loaded) return;
    try {
      const summary = await requestJson("/investigations/summary");
      stats.dataset.loaded = "true";
      stats.append(createElement("p", "review-hint", "仅统计执行状态和人工反馈；不计算根因准确率。未标记任务单独统计。"));
      for (const group of summary.groups) stats.append(createElement("p", "", `${originLabels[group.source_kind]} · ${group.execution_status} · ${feedbackLabels[group.feedback]}：${group.count}`));
    } catch (error) { showToast(error.message, true); }
  });
  section.append(stats);
}

function renderTrace(events = state.trace) {
  const filter = ui.traceFilter.value;
  const filtered = events.filter((event) => traceMatches(event, filter));
  ui.traceList.replaceChildren();
  ui.traceCount.textContent = events.length;
  ui.traceLatency.textContent = events.reduce((total, event) => total + (event.latency_ms || 0), 0);

  if (!filtered.length) {
    const empty = createElement("div", "trace-empty");
    const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#icon-trace");
    icon.append(use);
    empty.append(icon, createElement("strong", "", events.length ? "没有匹配的事件" : "Trace 尚未生成"));
    empty.append(createElement("p", "", events.length
      ? "切换筛选条件查看其他 Trace。"
      : "运行 Agent 后可查看节点、工具、SQL、检索、MCP 与 LLM 事件。"));
    ui.traceList.append(empty);
    return;
  }

  filtered.forEach((event) => ui.traceList.append(buildTraceEvent(event)));
  ui.traceList.scrollTop = ui.traceList.scrollHeight;
}

function buildTraceEvent(event) {
  const details = createElement("details", `trace-event ${statusClass(event.status)}`);
  if (["failed", "interrupted"].includes(event.status)) details.open = true;
  const summary = document.createElement("summary");
  summary.append(
    createElement("span", "trace-step", String(event.step)),
    createElement("span", "trace-node", humanize(event.node)),
    createElement("span", "trace-type", event.event_type),
    createElement("span", "trace-time", `${event.latency_ms} ms`),
  );
  const detail = createElement("div", "trace-detail");
  const facts = createElement("div", "trace-facts");
  appendFact(facts, "Status", event.status);
  appendFact(facts, "Intent", event.intent || "—");
  appendFact(facts, "Tool", event.tool_name || "—");
  appendFact(facts, "Model", event.model || "—");
  appendFact(facts, "北京时间", formatBeijing(event.created_at));
  detail.append(facts);
  if (event.error) detail.append(createElement("div", "trace-error", event.error));

  const payload = compactObject({
    input: event.input_json,
    output: event.output_json,
    tool_input: event.tool_input,
    tool_output_summary: event.tool_output_summary,
    sql: event.sql,
    sql_guard_result: event.sql_guard_result,
    retrieval_result: event.retrieval_result,
    tokens: event.tokens_json,
    created_at_utc: event.created_at,
  });
  detail.append(createElement("pre", "trace-json", prettyJson(payload)));
  details.append(summary, detail);
  return details;
}

function appendFact(container, label, value) {
  const item = document.createElement("div");
  item.append(createElement("span", "", label), createElement("code", "", String(value)));
  container.append(item);
}

function traceMatches(event, filter) {
  if (filter === "all") return true;
  if (filter === "failed") return event.status === "failed";
  if (filter === "tool") return event.event_type.startsWith("tool_") || event.node === "mcp_client";
  return event.event_type === filter || event.node === filter;
}

async function loadBusinessData(type = state.activeData) {
  state.activeData = type;
  document.querySelectorAll(".data-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.dataType === type);
  });
  ui.dataList.replaceChildren(createElement("p", "loading-copy", "正在读取业务数据…"));
  try {
    if (type === "tickets") {
      const page = await requestJson("/tickets?page=1&page_size=20");
      state.tickets = page.items || [];
      ui.ticketCount.textContent = page.total;
      renderTickets(page);
    } else if (type === "logs") {
      const page = await requestJson("/logs?page=1&page_size=20&sort_by=added");
      state.logs = page.items || [];
      ui.logCount.textContent = page.total;
      renderLogs(page);
    } else if (type === "knowledge") {
      const page = await requestJson("/knowledge?page=1&page_size=20");
      state.knowledge = page.items || [];
      ui.knowledgeCount.textContent = page.total;
      renderKnowledge(page);
    } else if (type === "services") {
      await renderServices();
    }
  } catch (error) {
    ui.dataList.replaceChildren(createElement("p", "empty-copy", `读取失败：${error.message}`));
  }
}

function renderTickets(page) {
  ui.dataListTitle.textContent = "最近工单";
  ui.dataListMeta.textContent = `${page.total} total`;
  ui.dataList.replaceChildren();
  if (!page.items.length) {
    ui.dataList.append(createElement("p", "empty-copy", "数据库中还没有工单。"));
    return;
  }
  page.items.forEach((ticket) => {
    const row = createElement("button", "data-row");
    row.type = "button";
    row.append(createElement("strong", "", `#${ticket.id} ${ticket.title}`));
    const meta = createElement("span", "row-meta");
    meta.append(createElement("span", "", ticket.service_name || ticket.category));
    meta.append(createElement("span", "", `${ticket.priority} · ${ticket.status}`));
    row.append(meta);
    row.append(createElement("span", "row-time", formatBeijing(ticket.created_at)));
    row.addEventListener("click", () => useBusinessQuery(`ticket ${ticket.id}`));
    ui.dataList.append(row);
  });
}

function renderLogs(page) {
  ui.dataListTitle.textContent = "最近添加的日志";
  ui.dataListMeta.textContent = `${page.total} total`;
  ui.dataList.replaceChildren();
  if (!page.items.length) {
    ui.dataList.append(createElement("p", "empty-copy", "数据库中还没有日志。"));
    return;
  }
  page.items.forEach((log) => {
    const row = createElement("button", "data-row log-row");
    row.type = "button";
    row.append(createElement("strong", `level-${String(log.level).toLowerCase()}`, `${log.level} · ${log.service_name}`));
    row.append(createElement("span", "row-message", log.message));
    row.append(createElement("span", "row-time", `${log.request_id || "无请求 ID"} · ${formatBeijing(log.created_at)}`));
    row.addEventListener("click", () => useBusinessQuery(`分析 ${log.service_name} 的 ${log.error_type || log.level} 日志并给出排查方案`));
    ui.dataList.append(row);
  });
}

async function renderServices() {
  if (!state.tickets.length || !state.logs.length) {
    const [ticketPage, logPage] = await Promise.all([
      requestJson("/tickets?page=1&page_size=100"),
      requestJson("/logs?page=1&page_size=100"),
    ]);
    state.tickets = ticketPage.items || [];
    state.logs = logPage.items || [];
    ui.ticketCount.textContent = ticketPage.total;
    ui.logCount.textContent = logPage.total;
  }
  const services = new Map();
  state.tickets.forEach((ticket) => {
    if (ticket.service_name) services.set(ticket.service_name, (services.get(ticket.service_name) || 0) + 1);
  });
  state.logs.forEach((log) => {
    services.set(log.service_name, (services.get(log.service_name) || 0) + 1);
  });
  ui.dataListTitle.textContent = "已发现服务";
  ui.dataListMeta.textContent = `${services.size} services`;
  ui.serviceCount.textContent = services.size;
  ui.dataList.replaceChildren();
  if (!services.size) {
    ui.dataList.append(createElement("p", "empty-copy", "当前业务数据中没有服务名称。"));
    return;
  }
  [...services.entries()].sort((a, b) => b[1] - a[1]).forEach(([service, count]) => {
    const row = createElement("button", "data-row");
    row.type = "button";
    row.append(createElement("strong", "", service));
    row.append(createElement("span", "", `${count} 条关联记录`));
    row.addEventListener("click", () => useBusinessQuery(`分析 service: ${service} 最近的故障，结合日志、工单和知识库给出结论`));
    ui.dataList.append(row);
  });
}

function renderKnowledge(page) {
  ui.dataListTitle.textContent = "知识文档";
  ui.dataListMeta.textContent = `${page.total} total`;
  ui.dataList.replaceChildren();
  if (!page.items.length) {
    ui.dataList.append(createElement("p", "empty-copy", "数据库中还没有知识文档。"));
    return;
  }
  page.items.forEach((doc) => {
    const row = createElement("details", "data-row knowledge-row");
    const summary = document.createElement("summary");
    summary.append(createElement("strong", "", doc.title));
    summary.append(createElement("span", "row-time", `${doc.category || "未分类"} · ${formatBeijing(doc.updated_at)}`));
    row.append(summary);
    const content = createElement("div", "knowledge-detail");
    content.append(createElement("p", "", doc.content));
    content.append(createElement("small", "", `来源：${doc.source || "未设置"}`));
    const searchButton = createElement("button", "secondary-button", "用 Agent 检索");
    searchButton.type = "button";
    searchButton.addEventListener("click", () => useBusinessQuery(`检索知识库中的 ${doc.title}`));
    content.append(searchButton);
    row.append(content);
    ui.dataList.append(row);
  });
}

function useBusinessQuery(query) {
  ui.query.value = query;
  updateQueryCount();
  setMobileView("workspace");
  ui.query.focus();
}

function renderRecentRuns() {
  ui.runCount.textContent = state.historyTotal || state.runs.length;
  ui.recentRuns.replaceChildren();
  if (!state.runs.length) {
    ui.recentRuns.append(createElement("p", "empty-copy", "数据库中还没有 Agent 运行记录。"));
    return;
  }
  state.runs.forEach((entry) => {
    const runId = entry.result?.run_id || entry.summary.id;
    const button = createElement("button", `recent-run${state.result?.run_id === runId ? " active" : ""}`);
    button.type = "button";
    button.append(createElement("strong", "", entry.query));
    button.append(createElement("span", "", `#${runId} · ${entry.summary.status} · ${entry.summary.intent || "—"} · ${formatBeijing(entry.summary.started_at)}`));
    button.addEventListener("click", () => openHistoricRun(entry));
    ui.recentRuns.append(button);
  });
}

function setMobileView(view) {
  ui.shell.dataset.mobileActive = view;
  document.querySelectorAll(".mobile-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.mobileView === view);
  });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function updateQueryCount() {
  ui.queryCount.textContent = ui.query.value.length;
}

function emptyBlock(message) {
  const block = createElement("div", "empty-state");
  block.append(createElement("strong", "", message));
  return block;
}

function prettyJson(value) {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function compactObject(value) {
  return Object.fromEntries(Object.entries(value).filter(([, item]) => item !== null && item !== undefined));
}

function humanize(value) {
  return String(value || "—")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

const beijingDateTime = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

function formatBeijing(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "—"
    : `${beijingDateTime.format(date)} 北京时间`;
}

function formatConfidence(value) {
  return Number.isFinite(value) ? `${Math.round(value * 100)}%` : "—";
}

function statusClass(value) {
  const normalized = String(value || "").toLowerCase();
  return ["completed", "failed", "waiting_approval", "success", "interrupted"].includes(normalized)
    ? normalized
    : "idle";
}

let toastTimer;
function showToast(message, error = false) {
  clearTimeout(toastTimer);
  ui.toast.textContent = message;
  ui.toast.className = `toast visible${error ? " error" : ""}`;
  toastTimer = setTimeout(() => {
    ui.toast.className = "toast";
  }, 4200);
}

ui.form.addEventListener("submit", runAgent);
ui.importForm.addEventListener("submit", submitImport);
ui.importType.addEventListener("change", setImportExample);
ui.importFile.addEventListener("change", loadImportFile);
document.querySelector("#open-import").addEventListener("click", openImportDialog);
document.querySelector("#close-import").addEventListener("click", closeImportDialog);
document.querySelector("#cancel-import").addEventListener("click", closeImportDialog);
ui.query.addEventListener("input", updateQueryCount);
ui.query.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    event.preventDefault();
    ui.form.requestSubmit();
  }
});
ui.traceFilter.addEventListener("change", () => renderTrace());
document.querySelector("#refresh-data").addEventListener("click", () => loadBusinessData());
document.querySelector("#copy-run-id").addEventListener("click", async () => {
  if (!state.result) return;
  try {
    await navigator.clipboard.writeText(String(state.result.run_id));
    showToast("Run ID 已复制。" );
  } catch {
    showToast("浏览器未允许访问剪贴板。", true);
  }
});
document.querySelectorAll(".data-tab").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".data-tab").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    loadBusinessData(button.dataset.dataType);
  });
});
document.querySelectorAll(".content-tab").forEach((button) => {
  button.addEventListener("click", () => {
    state.activeContent = button.dataset.content;
    renderResult();
    if (state.activeContent === "collaboration" && state.result) loadCollaboration();
  });
});
document.querySelectorAll("[data-approval]").forEach((button) => {
  button.addEventListener("click", () => submitApproval(button.dataset.approval));
});
document.querySelectorAll(".mobile-tab").forEach((button) => {
  button.addEventListener("click", () => setMobileView(button.dataset.mobileView));
});

updateQueryCount();
checkHealth();
restoreSession();
