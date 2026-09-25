# WorkPilot Agent

面向研发故障排查的单 Agent 应用。用户描述工单或线上告警后，系统通过受限计划查询日志、历史工单和知识手册，聚合证据并生成带引用的排查建议。项目包含 FastAPI API、LangGraph 工作流、MCP 查询服务、SQL Guard 和浏览器 Trace Console。

系统用于演示研发故障排查场景下的证据驱动分析，不会自动执行生产运维操作。当前保留增强版 Single Agent；没有采用 Multi-Agent。

## 能力概览

- 故障分析：规则优先路由，必要时使用结构化 LLM 输出；Planner 生成最多 5 步的工具计划。
- 跨服务调查：最多拆成 3 个服务调查项，保存各服务的状态、证据、错误和引用。工具经 Tool Registry 顺序执行；某项工具失败时记录局部失败并继续剩余计划。
- 证据分析：按服务聚合日志、工单和知识检索结果；输出事实、假设、建议及证据步骤。证据不足或冲突时提示不确定性。
- 工具治理：Tool Registry 对工具输入和输出执行 Pydantic 校验，记录 ToolCallLog；高风险计划在执行前进入人工审批流程。
- Text2SQL：LLM 生成结构化 SQL 后，由 SQLGlot AST SQL Guard 再次检查；只有满足只读规则的 SQL 才能执行。
- 知识检索：使用 BM25 搜索 MySQL 中的知识文档。
- MCP：通过官方 Python MCP SDK 的本地 stdio Client/Server 边界提供工单、日志、知识查询能力。
- 可观测性：AgentRun、工具调用和 Trace 持久化到数据库；Console 显示计划、证据、最终回答、审批状态和执行 Trace。
- 受控入口：日志/知识批量导入和 Alert Webhook 使用 INGESTION_API_KEY 验证。

## 架构与数据流

    Browser Console 或 Alert Webhook
      → FastAPI Router / Pydantic Request Schema
      → Agent Service：保存 AgentRun，建立请求级数据库 Session
      → LangGraph：Intent Router → Planner → Risk Checker → Tool Executor
      → Tool Registry：校验工具、执行查询、提交工具审计
      → MCP Client（stdio）→ MCP Server → 查询 Service / SQLAlchemy → MySQL
      → Incident Analyzer：按服务聚合 Evidence、核对引用
      → Answer Generator
      → 持久化 AgentRun / ToolCallLog / TraceEvent；Console 展示结果

异步 Console 请求通过 POST /agent/runs/async 创建运行，前端通过 SSE 接收进度。当前 BackgroundTasks、SSE Broker 和 LangGraph InMemorySaver 都是进程内组件；项目没有 Redis、Celery 或持久化队列。数据库中的运行审计和审批快照不等同于可跨重启恢复的 LangGraph 完整 checkpoint。

## 安全边界

- SQL Guard 仅允许单条 MySQL SELECT；限制表为 tickets、error_logs、knowledge_docs，并校验列和函数白名单。结果最多 100 行，OFFSET 最大 10000；写操作和不支持的 AST 节点会被拒绝。
- 高风险计划先整体评估，再要求人工审批。当前唯一 HIGH 工具是 simulate_high_risk_operation，只返回模拟结果；系统不执行真实重启、发布或其他生产写操作。
- 工具调用必须经过 Tool Registry；输入输出契约、启用状态、工具风险等级和审计在统一边界内处理。
- 导入与告警接口使用固定时间比较的 API Key 校验。普通业务读取和 Agent 接口没有用户身份认证、租户隔离或速率限制；不可直接用于承载敏感企业数据。
- 示例数据是合成演示数据。不要导入真实凭据、个人信息或未经授权的生产日志。

## 技术栈

| 层 | 实现 |
| --- | --- |
| API 与配置 | Python 3.11、FastAPI、Pydantic 2、pydantic-settings |
| 持久化 | MySQL 8+ 兼容数据库、SQLAlchemy 2、PyMySQL |
| Agent | LangGraph、结构化 LLM 调用、单 Agent 有界工作流 |
| 工具与安全 | 自定义 Tool Registry、Pydantic 工具契约、SQLGlot SQL Guard |
| 检索与协议 | BM25、官方 MCP Python SDK、本地 stdio |
| 前端 | 原生 HTML、CSS、JavaScript，由 FastAPI 同源托管 |
| 部署 | 提供 Render 配置模板 render.yaml；部署需要单独配置 MySQL 兼容数据库和密钥 |

## 本地启动

需要 Python 3.11 Conda 环境 workpilot 和 MySQL 8+ 兼容数据库。先在 MySQL 中创建项目数据库：

    CREATE DATABASE workpilot_agent CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

PowerShell：

    Set-Location "D:\WorkPilot Agent\workpilot-agent"
    conda activate workpilot
    Copy-Item .env.example .env
    python -m pip install -r requirements.txt

编辑根目录 .env，填写 MYSQL_HOST、MYSQL_PORT、MYSQL_USER、MYSQL_PASSWORD、MYSQL_DATABASE。设置 LLM_API_KEY、LLM_MODEL；需要自定义 OpenAI-compatible 服务时再填写 LLM_BASE_URL。配置 INGESTION_API_KEY 才能启用导入和告警接口。不要提交 .env。

初始化表、装入演示数据并启动：

    python -m scripts.init_db
    python -m scripts.seed_data
    python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000

演示数据使用 [DEMO] 标记的工单和合成日志/知识记录；seed_data 可重复运行。若没有配置 LLM Key 和模型，系统不创建 LLM 服务，Text2SQL 工具也不会注册，分析会使用受限 fallback。

- Console：<http://127.0.0.1:8000/console/>
- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>

## API 入口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | /health | 检查数据库连通性 |
| POST | /agent/runs | 同步执行 Agent 任务 |
| POST | /agent/runs/async | 创建异步运行并返回 run_id |
| GET | /agent/runs/{run_id}/events | SSE 进度事件 |
| POST | /agent/runs/{run_id}/approval | 批准、拒绝或取消等待中的审批 |
| GET | /agent/runs | 分页查看运行历史 |
| GET | /agent/runs/{run_id} | 查看运行详情 |
| GET | /agent/runs/{run_id}/trace | 查看持久化 Trace |
| POST | /alerts/incidents | 经 API Key 验证的告警入口 |
| POST | /ingestion/logs | 经 API Key 验证的日志导入 |
| POST | /ingestion/knowledge | 经 API Key 验证的知识导入 |
| GET | /tickets、/logs、/knowledge | 查询业务演示数据 |

## 现场演示建议

1. **单服务故障分析**：在 Console 输入“分析 payment-service 最近 60 分钟的 500 错误，结合日志、工单和知识库给出可能原因与排查方案”。展示路由、计划、三类证据和引用。根因表述应区分已证实事实与推测。
2. **跨服务调查**：输入“分析 order-service、inventory-service 和 payment-service 的故障，分别查询日志并比较时间线；证据不足时说明不确定性”。展示最多三个 ServiceInvestigation、逐服务证据和统一结论。计划总预算仍受 5 步限制。
3. **只读 Text2SQL**：提出对演示日志或工单的统计查询，展示 LLM 生成 SQL、SQL Guard 校验结果和查询 Trace。SQL 生成受模型影响，现场应准备一个已确认可用的查询作为备选。
4. **高风险审批边界**：输入“模拟重启 payment-service”，展示执行前审批；批准后只会调用模拟工具并返回模拟结果，不会真的重启服务。

告警 API 也可作为备用演示入口：POST /alerts/incidents，携带 X-Ingestion-Key 和符合 IncidentAlertRequest 的字段。其结果走现有 Agent 工作流。

## Phase 21 / Phase 22 评估

### Phase 21：代表性回归

原有 8 个 Phase 21 Case 在增强版当前工作树通过 8/8。之后 representative_agent 扩展到 11 例；整体报告为 8/11、exit code 1，另外 3 例是 Phase 22 跨服务 Case，失败点是旧 evaluator 的工具序列、intent、status/error 契约，不等价于原 8 例回归失败。详情见 eval/results/phase21-regression-manual.json。

### Phase 22：固定跨服务 A/B

三个固定 Case（正常、证据冲突、单工具失败）分别重复 5 次；baseline 和 enhanced 使用相同 Gold、fixture、模型配置与权限边界。

| Case | Baseline 业务成功 | Enhanced 业务成功 | 平均延迟（baseline → enhanced） | 平均 Token（baseline → enhanced） |
| --- | ---: | ---: | ---: | ---: |
| Normal | 0/5 | 5/5 | 14.80s → 15.90s | 1,945 → 3,058 |
| Conflict | 0/5 | 5/5 | 6.62s → 21.46s | 1,058 → 2,707 |
| Tool failure | 0/5 | 5/5 | 4.83s → 15.11s | 388 → 2,710 |

Enhanced 的 Gold evidence coverage、citation correctness、service completion 各为 15/15；两种方案的安全边界检查均为 15/15。单工具失败 Case 的降级成功率由 baseline 0/5 提升到 enhanced 5/5。

Conflict Router intent diagnostic 仍为 0/5：它仍将该类请求路由为 log_analysis。证据覆盖和业务任务成功不能掩盖这个路由问题。每个 Case 的 P95 只基于 5 次运行，属于小样本参考值，不代表稳定尾延迟。完整摘要见 eval/results/phase22-final-closeout-2026-09-25.json；原始逐次结果保存在本地 eval/results/phase22_ab/runs/，未作为生产性能承诺。

实验支持保留增强版 Single Agent，但没有比较 Multi-Agent，因此不能声称 Multi-Agent 更差或增强版已经证明优于 Multi-Agent。当前没有证据证明 Multi-Agent 能带来额外业务收益，故暂不采用。跨服务调查仍顺序调用工具，并受 5 步总预算限制。

## 已知限制

- 评估是小样本、固定合成 fixture 和特定模型配置；不代表真实生产准确率、可用性或泛化能力。
- Free LLM 服务的模型输出、Token 与延迟会变化；演示时应预留冷启动和请求超时。
- 计划上限为 5 步、最多识别 3 个服务；跨服务步骤顺序执行，当前票据检索仅针对首个服务，不是并行分布式调查。
- SSE 事件和 LangGraph checkpoint 使用进程内实现；实例重启或多副本部署不具备完整的事件重放/工作队列语义。
- 用户认证、租户权限、限流、生产审批集成、真实操作执行和大规模负载测试均不在当前实现范围。
- Conflict intent 路由仍需修复；本项目当前不开发 Multi-Agent。
