"use strict";

const state = {
  result: null,
  trace: [],
  runs: [],
  activeContent: "plan",
  activeData: "tickets",
  tickets: [],
  logs: [],
  busy: false,
};

const ui = {
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
  serviceCount: document.querySelector("#service-count"),
  recentRuns: document.querySelector("#recent-runs"),
  runCount: document.querySelector("#run-count"),
  systemState: document.querySelector("#system-state"),
  systemStateLabel: document.querySelector("#system-state-label"),
  toast: document.querySelector("#toast"),
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
  ui.runButton.querySelector("span").textContent = busy ? "Running…" : label;
  document.querySelectorAll("[data-approval]").forEach((button) => {
    button.disabled = busy;
  });
}

async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
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
    const message = body.error?.message || body.detail || `请求失败（HTTP ${response.status}）`;
    throw new Error(message);
  }
  return body.data ?? body;
}

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

async function runAgent(event) {
  event.preventDefault();
  const query = ui.query.value.trim();
  if (!query || state.busy) return;

  setBusy(true);
  ui.runTitle.textContent = "正在执行 Agent…";
  ui.runStatus.textContent = "RUNNING";
  ui.runStatus.className = "status-badge idle";
  ui.approvalPanel.hidden = true;
  renderTrace([]);
  try {
    const result = await requestJson("/agent/runs", {
      method: "POST",
      body: JSON.stringify({ query }),
    });
    acceptResult(result, query);
    await loadTrace(result.run_id);
    showToast(`Agent Run #${result.run_id} 已返回 ${result.status} 状态。`);
  } catch (error) {
    ui.runTitle.textContent = "任务执行失败";
    ui.runStatus.textContent = "ERROR";
    ui.runStatus.className = "status-badge failed";
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

function acceptResult(result, query) {
  state.result = result;
  state.activeContent = "plan";
  const existing = state.runs.findIndex((item) => item.result.run_id === result.run_id);
  const runEntry = { query, result, trace: [] };
  if (existing >= 0) state.runs.splice(existing, 1);
  state.runs.unshift(runEntry);
  state.runs = state.runs.slice(0, 8);
  renderResult();
  renderRecentRuns();
}

function renderResult() {
  const result = state.result;
  if (!result) return;

  const currentRun = state.runs.find((item) => item.result.run_id === result.run_id);
  ui.runTitle.textContent = currentRun?.query || `Agent Run #${result.run_id}`;
  ui.runStatus.textContent = String(result.status).replaceAll("_", " ").toUpperCase();
  ui.runStatus.className = `status-badge ${statusClass(result.status)}`;
  ui.runRisk.textContent = `${result.risk_level || "—"} RISK`;
  ui.runRisk.className = `risk-badge ${(result.risk_level || "").toLowerCase()}`;
  ui.runId.textContent = result.run_id;
  ui.runIntent.textContent = result.intent || "—";
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
  }
  document.querySelectorAll(".content-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.content === state.activeContent);
  });
  renderRunContent();
}

function renderRunContent() {
  ui.contentView.replaceChildren();
  const result = state.result;
  if (!result) return;

  if (state.activeContent === "plan") {
    renderPlan(result.plan || []);
  } else if (state.activeContent === "evidence") {
    renderEvidence(result.evidence || []);
  } else {
    const answer = createElement("div", "answer-content", result.final_answer || "当前没有最终回答。");
    ui.contentView.append(answer);
  }
}

function renderPlan(plan) {
  if (!plan.length) {
    ui.contentView.append(emptyBlock("当前任务不需要调用工具。"));
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
    row.append(createElement("strong", "", `Evidence ${index + 1} · ${humanize(item.tool || item.source || "result")}`));
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
    const entry = state.runs.find((item) => item.result.run_id === result.run_id);
    const query = entry?.query || ui.query.value.trim();
    acceptResult(result, query);
    await loadTrace(result.run_id);
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
    state.trace = trace.events || [];
    const entry = state.runs.find((item) => item.result.run_id === runId);
    if (entry) entry.trace = state.trace;
    renderTrace();
  } catch (error) {
    state.trace = [];
    renderTrace();
    showToast(`Trace 加载失败：${error.message}`, true);
  }
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
    created_at: event.created_at,
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
  ui.dataList.replaceChildren(createElement("p", "loading-copy", "正在读取业务数据…"));
  try {
    if (type === "tickets") {
      const page = await requestJson("/tickets?page=1&page_size=20");
      state.tickets = page.items || [];
      ui.ticketCount.textContent = page.total;
      renderTickets(page);
    } else if (type === "logs") {
      const page = await requestJson("/logs?page=1&page_size=20");
      state.logs = page.items || [];
      ui.logCount.textContent = page.total;
      renderLogs(page);
    } else if (type === "services") {
      await renderServices();
    } else {
      renderKnowledgeAccess();
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
    row.addEventListener("click", () => useBusinessQuery(`ticket ${ticket.id}`));
    ui.dataList.append(row);
  });
}

function renderLogs(page) {
  ui.dataListTitle.textContent = "最近日志";
  ui.dataListMeta.textContent = `${page.total} total`;
  ui.dataList.replaceChildren();
  if (!page.items.length) {
    ui.dataList.append(createElement("p", "empty-copy", "数据库中还没有日志。"));
    return;
  }
  page.items.forEach((log) => {
    const row = createElement("button", "data-row");
    row.type = "button";
    row.append(createElement("strong", `level-${String(log.level).toLowerCase()}`, `${log.level} · ${log.service_name}`));
    row.append(createElement("span", "", log.message));
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

function renderKnowledgeAccess() {
  ui.dataListTitle.textContent = "知识库访问";
  ui.dataListMeta.textContent = "via Agent Tool";
  ui.dataList.replaceChildren();
  const row = createElement("button", "data-row");
  row.type = "button";
  row.append(createElement("strong", "", "通过 search_knowledge 检索"));
  row.append(createElement("span", "", "知识文档当前由 Agent Tool 和 MCP Server 受控访问。"));
  row.addEventListener("click", () => useBusinessQuery("检索知识库中的 payment-service 故障排查手册"));
  ui.dataList.append(row);
}

function useBusinessQuery(query) {
  ui.query.value = query;
  updateQueryCount();
  setMobileView("workspace");
  ui.query.focus();
}

function renderRecentRuns() {
  ui.runCount.textContent = state.runs.length;
  ui.recentRuns.replaceChildren();
  if (!state.runs.length) {
    ui.recentRuns.append(createElement("p", "empty-copy", "运行任务后，这里会保留本次页面会话中的记录。"));
    return;
  }
  state.runs.forEach((entry) => {
    const button = createElement("button", `recent-run${state.result?.run_id === entry.result.run_id ? " active" : ""}`);
    button.type = "button";
    button.append(createElement("strong", "", entry.query));
    button.append(createElement("span", "", `#${entry.result.run_id} · ${entry.result.status} · ${entry.result.intent}`));
    button.addEventListener("click", () => {
      state.result = entry.result;
      state.trace = entry.trace;
      state.activeContent = "plan";
      renderResult();
      renderTrace();
      renderRecentRuns();
      setMobileView("workspace");
    });
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
loadBusinessData();
