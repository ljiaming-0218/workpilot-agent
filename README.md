# WorkPilot Agent

面向企业工单、故障分析和研发效能的智能 Agent 平台。本项目分阶段开发；当前推进到 **Phase 9：Incident Analysis Agent**，已实现基础后端、数据模型、业务接口、受控工具注册表、只读自然语言查询链路和基础 LangGraph Agent。

基础 Agent 已实现；MCP 和前端仍是占位。项目协作规范见 [AGENTS.md](AGENTS.md)。

## 当前阶段与验收

- 已实现：FastAPI、Pydantic 配置、MySQL / SQLAlchemy Engine、请求级 Session、Depends、`GET /health`。
- 当前技术栈：Conda `workpilot` / Python 3.11、FastAPI、Pydantic 2、SQLAlchemy 2、PyMySQL、MySQL 8+。
- 验收必须包含真实 MySQL 连接成功，单元测试中的替身不能代替数据库验收。
- Phase 0：真实连接、超时配置练习及核心理解检查已完成，并补充了配置参数传递的回归测试。
- Phase 1：六张表已创建，数据库测试及核心理解检查已完成。
- Phase 2：四个接口、参数校验、筛选分页、统一错误响应和测试已完成；待集中讲解、练习和理解检查。
- 本阶段接口说明、代码讲解和练习见 [Phase 2 工单与日志 API](docs/phase2-apis.md)。
- Phase 2 的关键词筛选练习和真实 MySQL 测试已完成。
- Phase 3：已实现 ToolDefinition、BaseTool、ToolRegistry、三个只读工具及调用审计，代码讲解和理解检查已完成。
- Phase 4：已实现统一 LLMService、结构化 SQL 生成、SQLGlot AST Guard、安全查询执行和 query_database 工具装配；真实 OpenRouter、SQL Guard 和 MySQL 只读链路已验证。
- Phase 5：已实现 AgentState、基础规则路由、单工具执行、最终回答节点、Basic StateGraph 和 `POST /agent/runs`。
- Phase 6：已实现高确定性规则路由、结构化 LLM fallback、受限 Intent、路由来源和置信度输出。
- Phase 7：已实现最多 5 步的结构化计划、顺序多工具执行、错误终止和循环上限。
- Phase 8：已实现中英文混合分词、BM25 排序、Top-K、MySQL 知识文档读取及 `search_knowledge` Agent 工具。
- Phase 9：已形成日志、工单、知识库三类证据收集、结构化 LLM 故障分析和引用证据步骤的完整闭环。

## 整体架构

当前分层：HTTP Router → Agent Service → LangGraph → Tool Registry → Retrieval / Service → ORM / MySQL；后续逐步加入 MCP、Trace 和前端调试台。

Phase 0 提供配置、数据库连接和请求生命周期管理。Phase 1 使用 `python -m scripts.init_db` 显式创建缺失的业务表；应用启动不会自动建库或改表。

```text
GET /health
  → FastAPI health router
  → Depends(get_db)：从 Session 工厂创建独立 Session
  → is_database_connected：SELECT 1
  → Engine / 连接池 → PyMySQL → MySQL
  → HTTP 200 connected / HTTP 503 disconnected
  → finally：关闭 Session、归还连接
```

## 原理与代码导航

| 文件 | 职责、输入输出与设计 |
| --- | --- |
| `backend/config.py` | `Settings` 从环境变量和根目录 `.env` 读取配置并校验端口等字段；环境变量优先。`database_url` 返回 SQLAlchemy URL，正确处理密码中的 `@`、`/` 等字符。密码使用 `SecretStr`，错误展示隐藏输入。缺少必填配置会使启动失败。 |
| `backend/database.py` | `build_engine(settings)` 返回 Engine，管理连接池、失效连接探测及超时；创建 Engine 不等于连接成功。`Base` 是六个 ORM 模型共用基类。`is_database_connected(session)` 执行 `SELECT 1` 返回布尔值，数据库异常 rollback 并仅记录异常类型。 |
| `backend/dependencies.py` | `get_db(request)` 从应用中的工厂生成独立 Session，通过 `yield` 交给路由，数据库异常回滚并重新抛出，`finally` 始终 close。不会自动 commit 业务写操作。 |
| `backend/routers/health_router.py` | `health(response, session)` 调用探测函数，输出 `HealthResponse`；Pydantic 限定状态取值，数据库不可用返回 503。这里是 HTTP 路由，与后续 Agent Intent Router 不同。 |
| `backend/main.py` | `create_app(settings=None)` 创建应用，注册健康/工单/日志路由及统一错误处理；lifespan 在启动时初始化 Engine 和 Session 工厂，退出时 dispose 连接池。配置可注入，便于独立测试。 |
| `tests/test_health.py` | 验证成功/异常 HTTP 契约、Session 独立性和清理、异常脱敏、密码特殊字符处理；提供显式开启的真实 MySQL 集成测试。 |
| `requirements.txt` / `pytest.ini` | 限定依赖主版本并配置测试目录和 MySQL 测试标记；尚未提供完全锁定的传递依赖文件。 |
| `.env.example` / `.gitignore` | 提供无真实密码的配置模板；忽略本地凭据、虚拟环境和缓存。 |

- **ORM**：把 Python 类和关系表对应起来；六个模型位于 `backend/models/`。
- **Engine / Connection Pool**：应用级共享数据库入口和连接池，减少反复连接开销。当前每个进程常驻最多 5 个池连接，额外最多 5 个连接，池等待超时 5 秒。
- **Session**：不是一个全局共享连接。它管理一组数据库操作的事务状态，执行 SQL 时向 Engine 借连接。本项目按请求创建，不能跨并发请求共享。
- **Transaction**：把多个写操作组织成一次提交，失败需要 rollback。即使 `SELECT 1` 也可能开启事务；本次无需 commit，close 会结束未提交事务并归还连接。
- **Dependency Injection**：路由声明需要 Session，FastAPI 负责执行 `get_db` 并把值传入；`yield` 后的清理让资源释放集中管理。
- PyMySQL 是同步驱动，所以健康路由和数据库依赖使用普通 `def`，由 FastAPI 在线程池执行。

## Windows 运行

所有命令在项目根目录执行：

```powershell
Set-Location 'D:\WorkPilot Agent\workpilot-agent'
conda activate workpilot
where.exe python
python --version
python -m pip --version
```

确认首个 Python 路径属于 `workpilot` 且版本为 3.11 后，按需安装依赖（当前本机已安装）。不要创建新的虚拟环境。

```powershell
python -m pip install -r requirements.txt
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
notepad .env
```

填写本机实际的 `MYSQL_USER`、`MYSQL_PASSWORD` 等值，密码不需要提供给聊天或写进代码。若密码含 `#`，请使用引号包围；复杂引号和转义按 dotenv 语法填写。修改配置后重启后端。

若目标数据库尚不存在，在 MySQL 客户端用有建库权限的本地账号执行以下语句。`-p` 会交互询问密码，不要把密码放在命令参数中：

```powershell
mysql -h 127.0.0.1 -P 3306 -u root -p
```

```sql
CREATE DATABASE IF NOT EXISTS workpilot_agent CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

此 SQL 只建立项目空库，不创建业务表。应用使用的账号需要能够连接该库。

显式创建 Phase 1 的六张表（当前本机已完成）：

```powershell
python -m scripts.init_db
```

可重复运行，只创建缺失表，不清空数据、不修改已有表结构。修改模型字段后需另行设计迁移；`create_all()` 不是迁移工具。

在已激活 `workpilot` 的终端启动后端：

```powershell
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

- Swagger：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>

## API 契约

Phase 2 业务接口统一返回 `success/data/error`。列表的 `data` 包含 `items/total/page/page_size`；默认每页 20 条，最多 100 条。详情不存在返回 404，参数错误返回 422，数据库约束冲突返回 409，连接/驱动运行故障返回 503，其他数据库操作错误返回 500。错误响应不包含原始 SQL、密码或请求体输入值。

| 方法与路径 | 用途与筛选 |
| --- | --- |
| `POST /tickets` | 创建工单；必填 title、content，可选 category、priority、service_name；初始 status 固定为 open，成功返回 201 |
| `GET /tickets` | 支持 service_name、category、priority、status、page、page_size |
| `GET /tickets/{ticket_id}` | 正整数 ID 查询详情 |
| `GET /logs` | 支持 service_name、level、error_type、request_id、created_from、created_to、page、page_size |

筛选条件以 AND 组合；字符串比较遵循数据库列的 collation。按 `created_at DESC, id DESC` 排序。响应时间为 UTC（`Z`）；日志时间筛选必须带时区，范围为 `[created_from, created_to)`。目前不提供日志写入接口，空日志库正常返回空列表。完整 PowerShell 请求示例见 [Phase 2 文档](docs/phase2-apis.md)。

遵循 AGENTS.md 的 `/health` 专用响应格式，不包通用 `success/data/error` 外层。

成功：HTTP 200，`{"status":"ok","database":"connected"}`。

数据库连接失败、认证失败、目标库不存在或连接池超时：HTTP 503，`{"status":"degraded","database":"disconnected"}`。详细连接信息不会出现在 HTTP 响应中。配置缺失或非法会阻止应用启动，程序逻辑错误不会伪装成数据库降级。

此接口检查配置所指向数据库的可达性，不验证业务表权限或后续 Agent 功能。`MYSQL_CONNECT_TIMEOUT` 控制连接超时（默认 5 秒，允许 1–30）；连接池等待、驱动读写超时分别为 5 秒，不代表整个请求严格在 5 秒内结束。

## 测试与验收

2026-09-07 本机验证：Conda `workpilot` / Python 3.11.16 / MySQL 8.4.11。开启真实 MySQL 测试后全套 **65 passed**（48 项独立测试、17 项数据库测试）。覆盖原有健康检查、模型和业务 API，以及关键词转义、工具注册/查找/调用、输入输出校验、禁用拒绝、成功/失败/拒绝审计和三个真实查询工具；额外覆盖非字典输入、审计启动失败和最终提交失败。数据库测试使用外层事务 + savepoint，测试结束撤销写入；自增 ID 可能留下间隙。存在一条 Starlette 对 httpx 的弃用提示，未影响通过。

独立测试（不需要数据库，真实 MySQL 用例默认跳过；本机默认应为 40 passed、14 skipped）：

```powershell
python -m pytest -q
```

配置 `.env` 并运行建表脚本后，运行包含真实 MySQL 的全套测试：

```powershell
$env:RUN_MYSQL_TESTS = '1'
try {
    python -m pytest -q
} finally {
    Remove-Item Env:RUN_MYSQL_TESTS -ErrorAction SilentlyContinue
}
```

服务启动后，在另一 PowerShell 窗口检查实际 HTTP：

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/health'
```

通过标准：HTTP 200，同时返回 `status=ok`、`database=connected`。数据库异常分支由单元测试模拟，不需要停止机器上已有的 MySQL 服务。

## 排错

| 现象 | 优先检查 |
| --- | --- |
| 启动时报 Settings 校验错误 | 根目录 `.env` 是否存在，用户/密码是否填写，端口是否为 1–65535 的整数 |
| `/health` 返回 503 | MySQL 服务、主机/端口、账号密码、数据库存在性及账号授权；不要仅凭端口监听就认为连接成功 |
| ModuleNotFoundError | 激活 `workpilot`，用 `where.exe python` 确认解释器属于该环境，并在项目根目录启动 |
| 8000 端口占用 | 指定 `--port 8001`，同时调整请求地址 |

```powershell
Get-Service MySQL84
Test-NetConnection 127.0.0.1 -Port 3306
mysql -h 127.0.0.1 -P 3306 -u your_mysql_user -p -D workpilot_agent -e 'SELECT 1;'
```

## Phase 0 学习记录

已完成：`Settings.mysql_connect_timeout`、驱动参数接入、`.env.example` 更新，保留读写超时。`tests/test_database_config.py` 覆盖非法值和参数传递。

理解检查：

1. 为什么创建 Engine 成功不能证明 MySQL 连接成功？
2. 为什么 Engine 可以共享，而并发请求不能共享一个 Session？
3. `get_db` 为什么使用 `yield` 和 `finally`，只用 `return` 会缺少什么？

面试训练：

1. **FastAPI 如何管理数据库 Session 生命周期？** 要点：Depends、请求级 Session、yield、finally 释放；事务提交应有明确业务边界。
2. **连接池耗尽可能是什么原因，如何定位？** 要点：Session 未关闭、慢查询/长事务、并发量；先查连接占用和耗时，再评估池大小。
3. **如何设计数据库健康检查？** 要点：真实轻量查询、超时、503、信息脱敏；端口可达不代表认证和目标库可用。

## 当前限制与下一阶段

当前已实现六个数据库模型、四个工单/日志接口、Tool Registry、SQL Guard、Text2SQL 编排、同步 Agent API、Rule Router + LLM fallback、最多 5 步的 Planner 执行循环、BM25 Top-K 检索工具及基于证据的故障分析闭环；尚未实现 Risk Checker、MCP、前端或部署验证。故障分析会区分确认事实和模型推断，并校验引用步骤；模型不可用或结构校验失败时返回基于工具结果的 fallback。BM25 当前会在每次查询时从数据库读取文档并重建内存索引，尚未进行业务负载性能测试；系统也尚无认证、幂等键或限流，仅用于本地开发阶段。

Phase 4 已增加 `sqlglot>=30.18,<31` 和 `openai>=3,<4`。SQL Guard 只允许 MySQL 单条 SELECT、三张业务表的白名单列和有限函数，禁止通配列、跨库名、子查询、CTE、UNION、锁、变量及写操作，并把 LIMIT 限制到 100、OFFSET 限制到 10000。已通过真实 OpenRouter 结构化输出完成 `LLMService → Text2SQLService → SQLGuard → MySQL` 手动 smoke check；免费路由可能发生重试，且模糊问题可能返回安全零行兜底 SQL。

本次止于 Phase 9。当前阶段完成只读故障分析闭环，不包含 Risk Checker、Human-in-the-loop 或 MCP。

## 原理参考

- [FastAPI：带 yield 的依赖](https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/)
- [SQLAlchemy：Session Basics](https://docs.sqlalchemy.org/en/20/orm/session_basics.html)
- [Pydantic：Settings Management](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
