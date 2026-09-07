# AGENTS.md

# WorkPilot Agent — Codex Project Instructions

> 本文件是 WorkPilot Agent 项目的最高优先级项目协作规范。
>
> Codex 在执行任何代码修改、目录调整、重构、测试、依赖安装、Prompt 修改、数据库修改、文档编写或架构设计之前，必须首先阅读并遵守本文件。
>
> 本项目不仅要求“把系统做出来”，还要求开发者真正理解代码、Agent 架构、算法逻辑、安全设计和工程实现，并最终能够独立完成项目讲解、代码修改和实习面试答辩。

---

# 1. 项目基本信息

## 1.1 项目名称

**WorkPilot Agent**

中文定位：

**面向企业工单、故障分析与研发效能场景的智能 Agent 平台**

---

## 1.2 项目目标

构建一个面向企业内部研发场景的 AI Agent，能够自动完成：

- 工单理解
- 故障分类
- 日志查询
- 历史工单检索
- 企业知识库检索
- Text2SQL
- Tool Calling
- 多步骤任务规划
- Evidence Aggregation
- 故障原因分析
- 解决方案生成
- 风险判断
- Human-in-the-loop
- MCP 工具调用
- Agent Trace
- Agent Evaluation

典型输入：

```text
最近 payment-service 大量出现 500 错误，
帮我分析可能原因，并给出排查方案。
```

最终系统执行链：

```text
User Query
    ↓
FastAPI
    ↓
LangGraph
    ↓
Intent Router
    ↓
Planner
    ↓
Tool Selection
    ↓
Tool Registry
    ↓
┌─────────────────────────────┐
│ query_error_logs            │
│ search_tickets              │
│ search_knowledge            │
│ query_database              │
└─────────────────────────────┘
    ↓
Evidence Aggregation
    ↓
LLM Analysis
    ↓
Risk Checker
    ↓
Human Approval（必要时）
    ↓
Final Answer
    ↓
Agent Trace
```

---

# 2. 项目双重目标

本项目有两个同等重要的目标。

## 2.1 工程目标

最终系统必须：

- 能运行
- 能测试
- 能调试
- 能演示
- 能追踪
- 能评估
- 能部署
- 能解释

不能只做一个调用大模型 API 的 Demo。

---

## 2.2 学习目标

Codex 不允许单纯替我把代码全部写完。

开发过程中必须让我逐步掌握：

### Backend

- Python
- FastAPI
- REST API
- Pydantic
- MySQL
- SQLAlchemy
- ORM
- Engine
- Session
- Transaction
- Connection Pool
- Dependency Injection

### Agent

- Function Calling
- Tool Calling
- Tool Registry
- Agent State
- Intent Router
- Planner
- LangGraph
- StateGraph
- Node
- Edge
- Conditional Edge
- State Transition
- ReAct 基本思想
- Loop Control
- Human-in-the-loop

### MCP

- MCP Client
- MCP Server
- MCP Tool
- MCP Resource
- MCP Prompt
- MCP Transport

### Algorithms

- SQL Parser
- Tokenizer
- AST
- Tree Traversal
- DFS
- BM25
- TF
- IDF
- Document Length Normalization
- Ranking
- Top-K
- Routing Classification
- Confidence
- Accuracy
- Precision
- Recall
- HitRate

### Engineering

- Logging
- Error Handling
- Agent Trace
- Observability
- Security
- Evaluation
- Testing
- Dependency Management

---

# 3. 固定技术栈

除非明确讨论并得到我的同意，否则不要擅自替换以下技术。

## Backend

```text
Python 3.11
FastAPI
Pydantic
```

---

## Database

```text
MySQL 8+
SQLAlchemy 2.x
PyMySQL
```

---

## Agent Framework

```text
LangGraph
```

---

## Tool System

```text
Custom Tool Registry
Pydantic Tool Schema
```

---

## MCP

```text
Official MCP Python SDK
```

如果尚未接入官方 SDK，不允许把自定义 Tool Registry 写成：

```text
完整 MCP 实现
```

---

## LLM

所有模型调用必须统一经过：

```text
LLMService
```

禁止在 Router、Tool、Service、Agent Node 中到处直接实例化模型客户端。

LLM Provider 应尽量保持可替换。

---

## Text2SQL

```text
LLM
+
SQLGlot
+
SQL Guard
```

---

## Retrieval

第一版优先：

```text
BM25
```

后续根据实验结果再考虑：

```text
Embedding
Dense Retrieval
Hybrid Retrieval
RRF
Reranker
```

不要一开始直接堆复杂向量检索。

---

## Frontend

最终目标：

**Agent Trace Debug Console**

不是普通 ChatGPT Clone。

第一版可以使用：

```text
HTML
CSS
JavaScript
```

如果已有 React/Vue，则先分析现有项目再决定是否沿用。

---

# 4. 当前开发环境

本项目开发环境已经确定。

Codex 不允许擅自创建新的虚拟环境。

---

## 4.1 项目路径

项目根目录：

```text
D:\WorkPilot Agent\workpilot-agent
```

所有项目命令默认从该目录执行。

进入项目：

```cmd
cd /d "D:\WorkPilot Agent\workpilot-agent"
```

---

## 4.2 Conda 环境

本项目统一使用：

```text
Environment Manager: Conda
Environment Name: workpilot
Python Version: 3.11
```

激活：

```cmd
conda activate workpilot
```

正确终端状态：

```text
(workpilot) D:\WorkPilot Agent\workpilot-agent>
```

---

# 5. 虚拟环境规则

当前项目已经拥有：

```text
workpilot
```

Conda 环境。

因此 Codex：

## 禁止

```text
创建 .venv
创建 venv
创建其他 Conda 环境
使用系统 Python 代替 workpilot
擅自改变 Python 大版本
```

禁止执行：

```cmd
python -m venv .venv
```

禁止执行：

```cmd
conda create -n ...
```

除非我明确要求重建环境。

---

# 6. `.venv` 冲突规则

历史目录中可能存在 `.venv`。

该 `.venv` 不属于当前 WorkPilot Agent 开发环境。

如果终端出现：

```text
(workpilot) (.venv)
```

说明同时激活两个环境。

这种情况下：

**禁止继续安装依赖。**

恢复流程：

```cmd
conda deactivate
```

如果仍显示：

```text
(.venv)
```

执行：

```cmd
deactivate
```

然后：

```cmd
cd /d "D:\WorkPilot Agent\workpilot-agent"
conda activate workpilot
```

最终必须是：

```text
(workpilot) D:\WorkPilot Agent\workpilot-agent>
```

---

# 7. 环境检查

第一次安装依赖前，必须检查：

```cmd
where python
```

```cmd
python --version
```

```cmd
python -m pip --version
```

预期：

```text
Python 3.11.x
```

并且：

```cmd
where python
```

返回的第一条 Python 路径必须属于：

```text
workpilot
```

环境。

如果不是：

**停止依赖安装。**

---

# 8. 包管理规则

本项目使用：

```text
Conda → 管理 Python 环境
pip   → 管理 Python 项目依赖
```

安装依赖时优先：

```cmd
python -m pip install ...
```

而不是：

```cmd
pip install ...
```

原因：

```text
python -m pip
```

能够明确使用当前 `workpilot` Python Interpreter 对应的 pip。

---

# 9. 依赖安装策略

本项目采用：

```text
按 Phase 安装依赖
```

不要在项目第一天一次安装所有 Agent / RAG / MCP 包。

原因：

1. 降低依赖冲突；
2. 明确每个依赖为什么存在；
3. 符合边开发边学习原则；
4. 方便理解不同技术之间的关系。

---

# 10. Phase 0 Dependencies

Phase 0 只需要基础后端依赖：

```text
fastapi
uvicorn
sqlalchemy
pymysql
pydantic
pydantic-settings
python-dotenv
pytest
httpx
```

安装：

```cmd
python -m pip install fastapi uvicorn sqlalchemy pymysql pydantic pydantic-settings python-dotenv pytest httpx
```

暂时禁止提前安装：

```text
langgraph
mcp
sqlglot
rank-bm25
chromadb
qdrant-client
faiss
```

---

# 11. Phase Dependency Map

## Phase 0

```text
FastAPI
Uvicorn
SQLAlchemy
PyMySQL
Pydantic
Pydantic Settings
python-dotenv
pytest
httpx
```

---

## SQL Guard Phase

进入 Text2SQL / SQL Guard 后安装：

```cmd
python -m pip install sqlglot
```

---

## LangGraph Phase

进入 LangGraph 后安装：

```cmd
python -m pip install langgraph
```

如果需要额外 LangChain 组件：

必须先解释：

- 为什么需要；
- 用在哪；
- 是否有更轻的替代。

不要直接：

```cmd
pip install langchain langchain-community langchain-openai ...
```

一股脑全部安装。

---

## BM25 Phase

优先自己理解 BM25。

如果需要用第三方实现作为结果对照，可安装：

```cmd
python -m pip install rank-bm25
```

但不能因为安装了库就跳过算法讲解。

---

## MCP Phase

进入 MCP 阶段后再安装官方 MCP Python SDK。

安装前必须：

1. 确认官方包；
2. 确认当前官方 SDK；
3. 确认官方文档；
4. 不要根据过时知识猜包名。

---

# 12. requirements.txt 规则

不要一开始写入所有未来依赖。

第一阶段只记录真实使用的直接依赖。

例如：

```text
fastapi
uvicorn
sqlalchemy
pymysql
pydantic
pydantic-settings
python-dotenv
pytest
httpx
```

不要直接：

```cmd
pip freeze > requirements.txt
```

然后把几十个间接依赖当成项目设计依赖。

优先维护：

```text
Direct Dependencies
```

正式部署阶段再考虑版本锁定。

---

# 13. 新增依赖必须解释

每次安装新依赖，Codex 必须说明：

```text
Dependency:
Why:
Used By:
Why Not Standard Library:
Alternative:
Phase:
```

例如：

```text
Dependency:
sqlglot

Why:
用于把 LLM 生成 SQL 解析为 AST。

Used By:
SQL Guard。

Why:
相比纯正则，可以从语法结构层面判断 SQL 是否包含写操作、非法表等。
```

---

# 14. 项目工程原则

整个项目遵守：

```text
能运行
↓
能理解
↓
能测试
↓
能解释
↓
能评估
↓
再优化
```

不要为了：

```text
“显得高级”
```

加入没有必要的技术。

优先：

- 清晰
- 可维护
- 可测试
- 可观察
- 可解释
- 可演示

---

# 15. Codex 教学协议

这是最高优先级规则之一。

Codex 不允许一次性把整个项目代写完成。

禁止：

```text
一次实现 LangGraph + MCP + BM25 + Planner + 前端
一次写几十个核心业务文件
一次性完成全部 Phase
```

必须：

```text
理解
↓
设计
↓
实现
↓
解释
↓
测试
↓
我的练习
↓
理解检查
↓
面试训练
↓
下一阶段
```

---

# 16. 每次写代码前必须先讲原理

不要：

```text
先输出 300 行代码
然后只写一句“这就是实现”
```

必须：

```text
问题是什么
↓
为什么需要这个模块
↓
它解决什么问题
↓
数据怎么流动
↓
再实现
```

---

# 17. 每次代码修改固定输出格式

Codex 每一次完成代码修改后，回答必须包含：

## Current Phase

```text
当前 Phase：
已完成：
本次目标：
未完成：
```

---

## Why

说明：

- 为什么现在做；
- 当前模块解决什么；
- 与前后模块有什么关系。

---

## Learning Goals

列出本次需要理解的 2-5 个知识点。

---

## Principle

先讲技术原理。

---

## Data Flow

必须画数据流，例如：

```text
HTTP Request
↓
FastAPI Router
↓
Pydantic
↓
Service
↓
SQLAlchemy Session
↓
MySQL
↓
Response
```

---

## Files Changed

列出实际修改文件。

---

## Code Explanation

每个核心文件解释：

- 文件职责
- 核心类
- 核心函数
- 输入
- 输出
- 关键设计
- 异常处理

---

## Algorithm Explanation

只要涉及：

- Router
- Planner
- SQL Guard
- AST
- BM25
- Ranking
- Retrieval
- LangGraph
- Tool Selection

必须单独讲算法。

---

## How to Run

必须告诉我：

- 当前目录
- 激活环境
- 执行命令

---

## How to Test

必须给：

- 正常测试
- 异常测试
- 预期结果
- 测试命令

---

## My Exercise

每个阶段至少留：

```text
1-2 个我自己完成的小任务
```

不要马上给答案。

---

## Understanding Check

每次至少问我 3 个问题。

---

## Interview Questions

每次给 3-5 个与当前 Phase 相关的：

- AI 应用开发
- Agent 开发
- AI 后端
- 大模型应用

面试问题。

并提供：

```text
回答要点
```

---

## Next Phase

只说明下一步。

不要提前实现。

---

# 18. 代码解释原则

不要机械逐行翻译代码。

禁止：

```text
第一行导入 FastAPI
第二行定义 app
第三行定义函数
```

应该解释：

```text
这里通过 FastAPI Depends 将数据库 Session 注入请求。

一个请求获得一个 Session，请求结束后关闭，可以避免多个请求共享同一个事务上下文。
```

---

# 19. 项目目录

最终推荐架构：

```text
workpilot-agent/
│
├── backend/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── dependencies.py
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── ticket.py
│   │   ├── error_log.py
│   │   ├── knowledge.py
│   │   ├── agent_run.py
│   │   └── tool_call.py
│   │
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── common.py
│   │   ├── ticket.py
│   │   ├── log.py
│   │   ├── agent.py
│   │   └── tool.py
│   │
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── health_router.py
│   │   ├── ticket_router.py
│   │   ├── log_router.py
│   │   ├── agent_router.py
│   │   └── trace_router.py
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── llm_service.py
│   │   ├── ticket_service.py
│   │   ├── log_service.py
│   │   ├── knowledge_service.py
│   │   ├── text2sql_service.py
│   │   └── retrieval_service.py
│   │
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── registry.py
│   │   ├── ticket_tools.py
│   │   ├── log_tools.py
│   │   ├── knowledge_tools.py
│   │   └── sql_tools.py
│   │
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── state.py
│   │   ├── graph.py
│   │   ├── router.py
│   │   ├── planner.py
│   │   ├── nodes.py
│   │   ├── risk.py
│   │   └── trace.py
│   │
│   ├── security/
│   │   ├── __init__.py
│   │   ├── sql_guard.py
│   │   └── tool_permission.py
│   │
│   ├── retrieval/
│   │   ├── __init__.py
│   │   ├── tokenizer.py
│   │   ├── bm25.py
│   │   └── retriever.py
│   │
│   ├── mcp/
│   │   ├── __init__.py
│   │   ├── server.py
│   │   ├── client.py
│   │   └── tools.py
│   │
│   ├── prompts/
│   │   ├── router.txt
│   │   ├── planner.txt
│   │   ├── text2sql.txt
│   │   ├── incident_analysis.txt
│   │   └── final_answer.txt
│   │
│   └── utils/
│       ├── __init__.py
│       ├── logger.py
│       └── ids.py
│
├── frontend/
│   ├── index.html
│   ├── app.js
│   └── styles.css
│
├── eval/
│   ├── routing_cases.json
│   ├── tool_cases.json
│   ├── sql_cases.json
│   ├── retrieval_cases.json
│   └── incident_cases.json
│
├── tests/
│   ├── test_health.py
│   ├── test_registry.py
│   ├── test_sql_guard.py
│   ├── test_router.py
│   ├── test_bm25.py
│   └── test_agent.py
│
├── scripts/
│   ├── init_db.py
│   └── seed_data.py
│
├── AGENTS.md
├── README.md
├── requirements.txt
├── .env.example
└── .gitignore
```

注意：

**不要在 Phase 0 一次把所有文件都实现。**

进入某个 Phase 时再真正创建和实现对应模块。

---

# 20. 分层规则

## Router

只处理：

```text
Request
Validation
Service Call
Response
```

不要：

- 写复杂 SQL
- 直接调用 LLM
- 编排 Agent
- 实现复杂业务

---

## Service

负责：

- 业务逻辑
- 数据处理
- 数据库访问
- LLM 调用封装
- Retrieval 调用

---

## Model

负责：

```text
SQLAlchemy ORM
```

---

## Schema

负责：

```text
Pydantic Request / Response / Tool Schema
```

---

## Tool

负责：

Agent 可调用能力。

所有 Agent Tool 必须进入 Tool Registry。

---

## Agent

负责：

- State
- Router
- Planner
- Graph
- Node
- Execution
- Risk
- Trace

---

## Security

负责：

- SQL Guard
- Tool Permission
- Risk Validation

---

# 21. MySQL 数据库

数据库建议：

```text
workpilot_agent
```

---

# 22. users

字段：

```text
id
username
role
created_at
updated_at
```

role：

```text
developer
operator
admin
```

第一版不做复杂登录系统。

---

# 23. tickets

```text
id
title
content
category
priority
status
service_name
resolution
created_at
updated_at
```

category：

```text
incident
consultation
data_query
operation
requirement
other
```

priority：

```text
low
medium
high
critical
```

status：

```text
open
processing
resolved
closed
```

建议索引：

```text
service_name
category
status
created_at
```

---

# 24. error_logs

```text
id
service_name
level
error_type
message
stack_trace
request_id
created_at
```

level：

```text
INFO
WARNING
ERROR
CRITICAL
```

建议索引：

```text
service_name
level
error_type
created_at
```

---

# 25. knowledge_docs

```text
id
title
content
category
source
created_at
updated_at
```

第一版保存纯文本。

后续再考虑：

```text
chunk
embedding
metadata
```

---

# 26. agent_runs

```text
id
request_id
user_query
intent
status
final_answer
started_at
finished_at
latency_ms
```

status：

```text
running
completed
failed
waiting_approval
```

---

# 27. tool_call_logs

```text
id
agent_run_id
tool_name
input_json
output_json
risk_level
status
latency_ms
error_message
created_at
```

---

# 28. API 返回规范

尽量统一：

```json
{
  "success": true,
  "data": {},
  "error": null
}
```

错误：

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "UNSAFE_SQL",
    "message": "SQL contains forbidden operation."
  }
}
```

---

# 29. 基础 API

逐步实现：

```text
GET /health

POST /tickets
GET /tickets
GET /tickets/{ticket_id}

GET /logs

POST /agent/run

GET /agent/runs/{run_id}

GET /agent/runs/{run_id}/trace
```

---

# 30. /health

正常：

```json
{
  "status": "ok",
  "database": "connected"
}
```

数据库异常：

```json
{
  "status": "degraded",
  "database": "disconnected"
}
```

---

# 31. Tool Registry

Tool Registry 是项目核心。

Agent 不允许任意直接调用业务函数。

所有 Agent Tools 必须注册。

---

# 32. Tool Contract

每个 Tool 必须包含：

```text
name
description
input_schema
output_schema
risk_level
handler
enabled
```

risk_level：

```text
LOW
MEDIUM
HIGH
```

---

# 33. 第一批 Tool

## search_tickets

输入：

```json
{
  "keyword": "DB_TIMEOUT",
  "service_name": "payment-service",
  "limit": 5
}
```

---

## get_ticket_detail

```json
{
  "ticket_id": 1
}
```

---

## query_error_logs

```json
{
  "service_name": "payment-service",
  "error_type": "DB_TIMEOUT",
  "minutes": 60,
  "limit": 100
}
```

---

## search_knowledge

```json
{
  "query": "DB_TIMEOUT connection pool",
  "top_k": 5
}
```

---

## query_database

```json
{
  "question": "最近24小时哪个服务报错最多？"
}
```

执行：

```text
Question
↓
Text2SQL
↓
SQL Guard
↓
MySQL
↓
Result
```

---

# 34. Tool Registry 接口

至少：

```text
register_tool()
get_tool()
list_tools()
call_tool()
```

必须支持：

- 未注册 Tool 拒绝
- Disabled Tool 拒绝
- Input Schema Validation
- Output Schema Validation
- Risk Level
- Tool Call Logging
- Latency
- Exception Handling

---

# 35. Text2SQL

执行：

```text
Natural Language
↓
Schema Context
↓
LLM
↓
Generated SQL
↓
SQL Guard
↓
MySQL
↓
Structured Result
↓
Natural Language Explanation
```

LLM 永远不能直接执行 SQL。

---

# 36. SQL Guard

必须使用：

```text
SQLGlot
```

构建 AST 安全校验。

禁止仅依赖：

```python
if "delete" in sql.lower():
```

---

# 37. SQL Guard Pipeline

```text
Generated SQL
↓
Parse
↓
AST
↓
Statement Validation
↓
Table Validation
↓
Column Validation
↓
Function Validation
↓
LIMIT Validation
↓
Safe SQL
```

---

# 38. SQL Guard 第一版规则

只允许：

```text
SELECT
```

禁止：

```text
INSERT
UPDATE
DELETE
DROP
ALTER
CREATE
TRUNCATE
REPLACE
GRANT
REVOKE
```

禁止：

```text
Multiple Statements
```

---

## Table Whitelist

允许：

```text
tickets
error_logs
knowledge_docs
```

---

## LIMIT

没有 LIMIT：

```text
自动添加 LIMIT 100
```

如果：

```text
LIMIT > 100
```

限制为：

```text
LIMIT 100
```

---

# 39. SQL Guard 算法学习要求

Codex 必须讲清：

```text
SQL String
↓
Tokenizer
↓
Parser
↓
AST
↓
Tree
↓
DFS Traversal
↓
Rule Validation
```

必须解释：

- Token
- Parser
- AST
- Tree Node
- DFS
- Whitelist
- Blacklist
- Static Analysis

我最终必须回答：

> 为什么 AST 校验比简单字符串匹配安全？

---

# 40. LangGraph AgentState

第一版 Agent State 至少：

```python
class AgentState(TypedDict, total=False):
    query: str
    intent: str
    plan: list
    current_step: int
    selected_tool: str
    tool_inputs: dict
    tool_results: list
    evidence: list
    risk_level: str
    requires_approval: bool
    final_answer: str
    error: str | None
```

如果新增字段：

必须解释为什么。

---

# 41. 第一版 LangGraph

```text
START
↓
intent_router
↓
tool_selector
↓
tool_executor
↓
answer_generator
↓
END
```

后续：

```text
START
↓
Intent Router
↓
Planner
↓
Tool Executor
↓
Evidence Aggregator
↓
Risk Checker
↓
      requires approval?
       ↙            ↘
     YES             NO
      ↓               ↓
Human Approval    Final Answer
      ↓
Final Answer
      ↓
END
```

---

# 42. LangGraph 学习目标

必须理解：

```text
State
Node
Edge
Conditional Edge
State Transition
```

并能解释：

```text
S_t
↓
Node Function
↓
S_(t+1)
```

重点问题：

> 为什么 Agent Workflow 使用 Graph，而不是无限 while-loop？

---

# 43. Intent Router

Intent：

```text
ticket_search
log_analysis
knowledge_search
data_query
incident_analysis
general
```

---

# 44. 第一版 Router

采用：

```text
Rule Router
+
LLM Fallback
```

不是所有请求直接交 LLM 分类。

---

# 45. Router Evaluation

创建：

```text
eval/routing_cases.json
```

至少：

```text
20 cases
```

示例：

```json
{
  "query": "查询 payment-service 最近一小时错误",
  "gold_intent": "log_analysis"
}
```

计算：

```text
Accuracy
```

后续可增加：

```text
Confusion Matrix
```

---

# 46. Planner

Planner 负责：

```text
Complex Query
↓
Task Decomposition
↓
Ordered Tool Plan
```

例如：

```text
payment-service 一直 500，分析原因
```

可能计划：

```text
1. query_error_logs
2. search_tickets
3. search_knowledge
4. synthesize evidence
```

---

# 47. Planner 安全限制

必须：

```text
max_steps <= 5
```

禁止无限规划。

超过：

```text
max_steps
```

必须终止或进入 fallback。

---

# 48. Planner 学习目标

必须解释：

- Task Decomposition
- Planning
- Execution
- ReAct
- Agent Loop
- Loop Guard
- Termination Condition

---

# 49. BM25

第一阶段知识库检索使用：

```text
BM25
```

不能只是调用库然后不解释。

---

# 50. BM25 学习要求

必须讲：

- TF
- IDF
- Document Frequency
- Document Length
- Average Document Length
- k1
- b
- Ranking
- Top-K

流程：

```text
Query
↓
Tokenization
↓
Term Statistics
↓
BM25 Score
↓
Sort
↓
Top-K Documents
```

必须能够回答：

- BM25 和 TF-IDF 有什么区别？
- 为什么需要长度归一化？
- k1 控制什么？
- b 控制什么？
- 为什么 BM25 对专业术语检索有优势？

---

# 51. RAG 后续升级

BM25 稳定后再讨论：

```text
Dense Retrieval
```

然后可以尝试：

```text
BM25
+
Dense Retrieval
↓
RRF
↓
Rerank
```

不得提前堆叠。

---

# 52. Evidence

最终回答不能完全依赖模型脑补。

Evidence 示例：

```json
{
  "source_type": "error_log",
  "source_id": 123,
  "content": "DB_TIMEOUT",
  "score": null
}
```

或：

```json
{
  "source_type": "knowledge",
  "source_id": 8,
  "content": "连接池耗尽处理方法",
  "score": 8.21
}
```

---

# 53. Final Answer

最终回答应尽量分：

## Evidence

系统查询得到的事实。

## Inference

模型基于 Evidence 的推断。

## Recommendation

建议。

严禁把：

```text
LLM 推测
```

描述成：

```text
数据库事实
```

---

# 54. Risk Checker

Risk Level：

```text
LOW
MEDIUM
HIGH
```

---

## LOW

例如：

```text
读取日志
查询工单
检索知识
```

可以自动执行。

---

## MEDIUM

例如：

```text
生成参数修改建议
生成运维操作建议
```

可以输出，但需提示。

---

## HIGH

例如：

```text
DELETE
UPDATE
生产配置修改
服务重启
Shell Execution
数据删除
```

必须：

```text
Human Approval
```

---

# 55. Human-in-the-loop

流程：

```text
Risk Checker
↓
HIGH
↓
LangGraph Interrupt
↓
WAITING_APPROVAL
↓
Approve / Reject
↓
Resume / Cancel
```

第一版只模拟。

禁止直接操作真实生产系统。

---

# 56. MCP

必须在：

```text
Tool Registry
+
Basic Agent
```

稳定之后再引入。

---

# 57. MCP 第一批 Tools

可以暴露：

```text
search_tickets
query_error_logs
search_knowledge
```

---

# 58. MCP Architecture

```text
LangGraph Agent
↓
MCP Client
↓
MCP Server
↓
MCP Tool
↓
Service
↓
MySQL / Retriever
```

---

# 59. MCP 学习要求

必须比较：

```text
Python Function
vs
Function Calling
vs
Tool Registry
vs
MCP
```

并让我真正理解：

> MCP 解决的是 Agent 与外部工具、资源和服务之间标准化连接的问题。

---

# 60. Agent Trace

每个 Agent Run 必须记录 Trace。

例如：

```text
Run #1024

00:00.000
intent_router
→ incident_analysis

00:00.080
planner
→ 3 steps

00:00.210
query_error_logs
→ 57 rows

00:00.390
search_tickets
→ 3 tickets

00:00.640
search_knowledge
→ 5 documents

00:01.350
final_answer
→ completed
```

---

# 61. Trace Event

建议：

```json
{
  "step": 1,
  "node": "intent_router",
  "event_type": "node_completed",
  "input": {},
  "output": {},
  "latency_ms": 20,
  "status": "success",
  "error": null
}
```

---

# 62. Trace 至少记录

```text
run_id
step
node
intent
tool_name
tool_input
tool_output_summary
sql
sql_guard_result
retrieval_result
latency
status
error
```

如果 API 支持：

```text
model
tokens
cost
```

也可加入。

---

# 63. Frontend

不要做普通聊天机器人前端。

推荐：

```text
┌────────────────┬──────────────────────┬─────────────────────┐
│ Business Data  │ Agent Workspace      │ Agent Trace         │
│                │                      │                     │
│ Tickets        │ User Query           │ Router              │
│ Logs           │ Agent Answer         │ Planner             │
│ Knowledge      │ Evidence             │ Tool Calls          │
│ Services       │                      │ SQL                 │
│                │                      │ SQL Guard           │
│                │                      │ Retrieval           │
│                │                      │ MCP                 │
└────────────────┴──────────────────────┴─────────────────────┘
```

---

# 64. Trace Panel

至少展示：

- Intent
- Plan
- Node
- Tool
- Tool Input
- Tool Output
- Generated SQL
- SQL Guard
- Retrieval
- MCP
- Latency
- Error

目标：

> 让面试官可以直接看到 Agent 是如何完成任务的。

---

# 65. LLMService

所有 LLM 调用统一：

```text
backend/services/llm_service.py
```

业务代码禁止到处：

```python
client.chat.completions.create(...)
```

---

# 66. LLMService 负责

至少：

```text
provider
model
base_url
api_key
timeout
retry
structured output
error handling
latency
```

---

# 67. LLM 使用原则

不要什么都交给 LLM。

确定性程序优先处理：

```text
SQL Guard
Tool Registry
Database Query
Permission
Risk Rules
Evaluation
```

LLM 更适合：

```text
Intent Fallback
Planning
Text2SQL Generation
Evidence Synthesis
Natural Language Answer
```

每次增加 LLM 调用前必须思考：

> 这个地方真的需要 LLM 吗？

---

# 68. Prompt 管理

Prompt 统一：

```text
backend/prompts/
```

禁止散落在 Python 文件。

例如：

```text
router.txt
planner.txt
text2sql.txt
incident_analysis.txt
final_answer.txt
```

---

# 69. Prompt 修改要求

必须解释：

```text
Input
Output
Constraint
Failure Mode
Evaluation
```

---

# 70. Error Handling

禁止所有错误都变成：

```text
500 Internal Server Error
```

至少区分：

```text
DATABASE_ERROR
VALIDATION_ERROR
LLM_ERROR
TOOL_NOT_FOUND
TOOL_DISABLED
TOOL_EXECUTION_ERROR
INVALID_SQL
UNSAFE_SQL
ROUTER_ERROR
PLANNER_ERROR
RETRIEVAL_ERROR
MCP_ERROR
AGENT_EXECUTION_ERROR
```

---

# 71. Logging

至少记录：

```text
request_id
run_id
query
intent
tool
tool latency
sql
sql guard result
retrieval result
llm latency
status
error
```

禁止记录：

```text
API Key
MySQL Password
Secret
完整敏感配置
```

---

# 72. Coding Style

函数、变量：

```text
snake_case
```

类：

```text
PascalCase
```

常量：

```text
UPPER_CASE
```

---

# 73. Type Hints

核心代码尽量完整 Type Hint。

例如：

```python
def get_ticket(ticket_id: int) -> Ticket | None:
    ...
```

---

# 74. Function Rule

函数遵循：

```text
Single Responsibility
```

不要一个 300 行函数同时负责：

```text
数据库
LLM
Tool
Agent
Trace
```

---

# 75. Comment Rule

注释主要解释：

```text
Why
```

而不是机械描述：

```text
What
```

---

# 76. Docstring

核心：

- Service
- Tool
- LangGraph Node
- Security Module

建议有 Docstring。

描述：

```text
Purpose
Input
Output
Important Constraints
```

---

# 77. Database Transaction

数据库写操作必须考虑：

```text
commit
rollback
```

数据库异常应 rollback。

---

# 78. `.env`

敏感配置：

```text
.env
```

例如：

```env
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your_password
MYSQL_DATABASE=workpilot_agent

LLM_API_KEY=your_key
LLM_BASE_URL=
LLM_MODEL=
```

必须提供：

```text
.env.example
```

---

# 79. `.gitignore`

至少：

```text
.env
__pycache__/
*.pyc
.idea/
.vscode/
logs/
.pytest_cache/
```

如果存在旧：

```text
.venv/
```

也加入：

```text
.venv/
```

不要忽略：

```text
.env.example
```

---

# 80. Testing

核心模块必须尽量可单独测试。

---

## Unit Test

重点：

```text
Tool Registry
SQL Guard
Router
BM25
Risk Checker
```

---

## Integration Test

重点：

```text
FastAPI → MySQL
Agent → Tool Registry
Text2SQL → SQL Guard → MySQL
LangGraph → Tools
MCP → Service
```

---

# 81. Evaluation

项目必须拥有：

```text
eval/
```

---

# 82. Router Eval

至少：

```text
20 cases
```

指标：

```text
Accuracy
```

---

# 83. Tool Selection Eval

至少：

```text
20 cases
```

指标：

```text
Tool Selection Accuracy
```

---

# 84. Text2SQL Eval

至少：

```text
20 questions
```

评估：

```text
SQL executable
Correct table
Correct filtering
Correct result
```

---

# 85. SQL Guard Eval

至少：

```text
10 Safe SQL
10 Unsafe SQL
```

指标：

```text
Safe Pass Rate
Unsafe Block Rate
```

---

# 86. Retrieval Eval

至少：

```text
20 Queries
```

指标：

```text
HitRate@1
HitRate@3
HitRate@5
```

---

# 87. Agent Eval

至少：

```text
10 Incident Tasks
```

指标：

```text
Task Success Rate
Tool Success Rate
Average Steps
Average Latency
```

---

# 88. LLM Judge

可以用于：

```text
答案质量
解释质量
方案合理性
```

但不能把所有评估都交给 LLM。

能程序化的指标必须程序化。

---

# 89. README

最终 README 至少包含：

```text
Project Introduction
Architecture
Tech Stack
Environment
Database Design
Agent Workflow
Tool Registry
LangGraph
Text2SQL
SQL Guard
BM25
MCP
Human-in-the-loop
Agent Trace
Evaluation
Run Guide
API Guide
Limitations
Future Work
```

---

# 90. 禁止夸大

没有完成：

```text
MCP
```

就不能写：

```text
Implemented MCP integration
```

如果只是模拟：

```text
Human Approval
```

就不能写：

```text
Production Approval System
```

如果没有真实生产验证：

不能写：

```text
Production Ready
```

---

# 91. Phase 0 — Backend Foundation

只实现：

```text
Project Structure
FastAPI
Config
MySQL
SQLAlchemy
Session
Dependency Injection
/health
```

必须学习：

```text
FastAPI Request Lifecycle
ORM
Engine
Session
Transaction
Connection Pool
Dependency Injection
```

验收：

```http
GET /health
```

数据库连接正常。

---

# 92. Phase 1 — Database Models

实现：

```text
users
tickets
error_logs
knowledge_docs
agent_runs
tool_call_logs
```

学习：

```text
Primary Key
Foreign Key
Index
Normalization
Enum
Relationship
Transaction
```

---

# 93. Phase 2 — Ticket & Log APIs

实现：

```text
POST /tickets
GET /tickets
GET /tickets/{id}
GET /logs
```

学习：

```text
REST API
Schema Validation
Service Layer
Filtering
Pagination
```

---

# 94. Phase 3 — Tool Registry

实现：

```text
BaseTool
ToolDefinition
ToolRegistry
register_tool
get_tool
list_tools
call_tool
```

Tool：

```text
search_tickets
get_ticket_detail
query_error_logs
```

学习：

```text
Registry Pattern
Strategy Pattern
Tool Calling
Schema Validation
```

---

# 95. Phase 4 — Text2SQL + SQL Guard

安装：

```cmd
python -m pip install sqlglot
```

实现：

```text
Text2SQL
SQLGlot
AST Validation
Read-only Query
```

学习：

```text
Parser
AST
Tree
DFS
Static Analysis
SQL Security
```

---

# 96. Phase 5 — LangGraph Basic Agent

安装：

```cmd
python -m pip install langgraph
```

实现：

```text
AgentState
Intent Router
Tool Executor
Final Answer Node
Basic StateGraph
```

学习：

```text
State Machine
Graph
State Transition
Conditional Edge
```

---

# 97. Phase 6 — Router

升级：

```text
Rule Router
+
LLM Fallback
```

完成：

```text
Router Evaluation
```

---

# 98. Phase 7 — Planner

实现：

```text
Task Decomposition
Plan Generation
Multi-tool Execution
max_steps
```

学习：

```text
Planning
ReAct
Loop Control
Termination
```

---

# 99. Phase 8 — BM25

实现：

```text
Tokenizer
BM25
Retriever
Top-K
```

学习：

```text
TF
IDF
Ranking
Length Normalization
```

---

# 100. Phase 9 — Incident Analysis Agent

形成完整业务闭环：

```text
User
↓
Router
↓
Planner
↓
Logs
↓
Tickets
↓
Knowledge
↓
Evidence
↓
LLM Analysis
↓
Answer
```

---

# 101. Phase 10 — Risk Checker

实现：

```text
LOW
MEDIUM
HIGH
```

---

# 102. Phase 11 — Human-in-the-loop

实现：

```text
Interrupt
Approval
Resume
Cancel
```

第一版只模拟高风险执行。

---

# 103. Phase 12 — MCP Python SDK

前提：

```text
Tool Registry 稳定
Agent 基础稳定
```

然后实现：

```text
MCP Server
MCP Client
MCP Tools
```

---

# 104. Phase 13 — Agent Trace

实现：

```text
Node Trace
Tool Trace
SQL Trace
Retrieval Trace
LLM Trace
Latency
Errors
```

---

# 105. Phase 14 — Frontend Debug Console

实现：

```text
Business Data
Agent Workspace
Agent Trace
```

---

# 106. Phase 15 — Evaluation

完成：

```text
Router Eval
Tool Eval
SQL Eval
SQL Guard Eval
Retrieval Eval
Agent Eval
```

---

# 107. Phase 16 — Documentation

完成：

```text
README
Architecture Diagram
Demo Cases
Interview Notes
Resume Description
Limitations
```

---

# 108. Phase Gate

如果当前 Phase：

```text
代码不工作
测试失败
核心原理没有解释
```

则：

**禁止进入下一 Phase。**

每个 Phase 必须同时满足：

```text
Code Works
Tests Pass
I Understand It
Documentation Updated
```

---

# 109. 第一阶段禁止加入

没有明确需求前不要主动加入：

```text
Multi-Agent
Redis
Kafka
Celery
Kubernetes
Elasticsearch
Microservices
Fine-tuning
Complex Authentication
Automatic Shell Execution
Real Production Restart
Automatic Database Write Agent
```

---

# 110. Multi-Agent Rule

只有：

```text
Single Agent 完成
+
存在明确业务需要
```

之后才能讨论 Multi-Agent。

不要为了简历关键词强行多 Agent。

---

# 111. Codex 每次修改前流程

必须：

```text
1. Read AGENTS.md
2. Inspect Repository
3. Inspect Relevant Files
4. Determine Current Phase
5. Determine Minimal Change
6. Explain Plan
7. Modify Code
8. Run Tests
9. Explain Results
```

---

# 112. 禁止盲目重构

如果发现架构问题：

必须先说明：

```text
Problem
Why
Impact
Options
Recommendation
```

不要直接重构整个项目。

---

# 113. 不允许偷偷替换技术

项目规定：

```text
MySQL
```

不能因为方便换成：

```text
SQLite
```

项目规定：

```text
LangGraph
```

不能私自换其他 Agent Framework。

项目规定：

```text
Conda workpilot
```

不能自己创建 `.venv`。

---

# 114. Windows 命令规范

默认开发环境：

```text
Windows CMD / PowerShell
```

进入项目：

```cmd
cd /d "D:\WorkPilot Agent\workpilot-agent"
```

激活：

```cmd
conda activate workpilot
```

启动 FastAPI：

```cmd
uvicorn backend.main:app --reload
```

测试：

```cmd
pytest -v
```

---

# 115. 每次运行前检查

必要时：

```cmd
where python
python --version
```

确保使用：

```text
workpilot
Python 3.11
```

---

# 116. 我必须真正掌握的算法

## SQL AST

```text
SQL
↓
Tokenizer
↓
Parser
↓
AST
↓
DFS
↓
Validation
```

---

## BM25

```text
Query
+
Documents
↓
Token Statistics
↓
TF / IDF
↓
Length Normalization
↓
Score
↓
Ranking
↓
Top-K
```

---

## Router

```text
Query
↓
Rule
↓
Confidence
↓
LLM Fallback
↓
Intent
```

---

## LangGraph

```text
State
↓
Node
↓
State Update
↓
Conditional Routing
↓
Next Node
```

---

## Planner

```text
Complex Task
↓
Task Decomposition
↓
Ordered Steps
↓
Tool Execution
↓
Observation
↓
Next Step / Stop
```

---

# 117. 最终面试能力

项目完成后，我必须能解释：

```text
为什么选择 FastAPI？
```

```text
为什么使用 MySQL？
```

```text
ORM 做了什么？
```

```text
Session 生命周期是什么？
```

```text
为什么一个请求通常使用独立 Session？
```

```text
为什么需要 Tool Registry？
```

```text
Tool Registry 和 Function Calling 有什么区别？
```

```text
Tool Registry 和 MCP 有什么区别？
```

```text
MCP Client 和 MCP Server 如何通信？
```

```text
为什么使用 LangGraph？
```

```text
Agent State 如何流转？
```

```text
为什么不用 while-loop？
```

```text
怎么防止 Agent 无限执行？
```

```text
Planner 如何限制步骤？
```

```text
为什么 SQL Guard 使用 AST？
```

```text
BM25 如何计算相关性？
```

```text
BM25 与 Dense Retrieval 如何选择？
```

```text
Agent 如何进行 Evaluation？
```

```text
为什么需要 Agent Trace？
```

```text
什么时候需要 Human-in-the-loop？
```

---

# 118. 最终 Demo

必须能演示：

```text
用户：

最近 payment-service 为什么一直出现 500？
```

执行 Trace：

```text
1. Intent Router
   → incident_analysis

2. Planner
   → query_error_logs
   → search_tickets
   → search_knowledge

3. query_error_logs
   → 57 DB_TIMEOUT

4. search_tickets
   → 3 similar tickets

5. search_knowledge
   → 5 relevant documents

6. Evidence Aggregation

7. LLM Analysis

8. Risk Checker
   → LOW

9. Final Answer
```

---

# 119. 最终回答示例

```text
## 观察到的事实

过去 1 小时 payment-service 出现 57 次 DB_TIMEOUT。

## 历史情况

检索到 3 个类似历史工单，其中 2 个与数据库连接池耗尽有关。

## 可能原因

结合当前日志和历史工单，数据库连接池耗尽是较高概率原因。

## 推荐排查

1. 检查 active connections
2. 检查 connection pool size
3. 检查数据库 response latency
4. 检查是否存在连接泄漏

## 风险提示

当前建议仅涉及查询和诊断，不包含高风险写操作。
```

---

# 120. 简历导向

项目最终根据真实完成情况可以形成类似：

```text
WorkPilot Agent｜企业工单与研发效能智能体平台

基于 FastAPI、MySQL、SQLAlchemy 与 LangGraph 构建企业研发效能
Agent，设计 Intent Router、Planner、Tool Registry 与状态机式 Agent
Workflow，实现历史工单查询、错误日志分析、知识检索及 Text2SQL。

基于 SQLGlot AST 构建只读 SQL Guard，对模型生成 SQL 进行语法树级
安全校验；实现 BM25 企业知识检索，并通过 MCP Python SDK 将内部数据
查询能力标准化暴露为 MCP Tools。

构建 Agent Trace 记录 Node、Tool Call、SQL、Retrieval 和 Latency，
并通过 Router Accuracy、Tool Selection Accuracy、Retrieval HitRate
和 Task Success Rate 对系统进行评估。
```

只有真正实现的内容才能写进简历。

---

# 121. Codex 第一次任务

如果当前项目刚开始：

**只执行 Phase 0。**

第一次只允许：

```text
1. 阅读 AGENTS.md
2. 检查当前项目目录
3. 检查当前 workpilot Conda 环境
4. 检查 Python 3.11
5. 解释 WorkPilot 整体架构
6. 解释 Phase 0 在整体系统中的位置
7. 检查或创建 Phase 0 所需目录
8. 安装 Phase 0 必要依赖
9. 配置 FastAPI
10. 配置 MySQL
11. 配置 SQLAlchemy
12. 实现 Session
13. 实现 Dependency Injection
14. 实现 GET /health
15. 测试数据库连接
16. 解释全部新增代码
17. 给我练习任务
18. 给我理解检查
19. 给我面试问题
20. 告诉我 Phase 1 做什么
```

---

# 122. 第一次禁止实现

第一次严禁实现：

```text
Tool Registry
Text2SQL
SQL Guard
LangGraph
Planner
BM25
MCP
Human-in-the-loop
完整 Frontend
```

---

# 123. 第一次回复必须包含

```text
## Current Phase

## Environment Check

## Architecture Overview

## Learning Goals

## Principle

## Data Flow

## Files Changed

## Code Explanation

## How to Run

## How to Test

## My Exercise

## Understanding Check

## Interview Questions

## Next Phase
```

---

# 124. 项目最高原则

Codex 的职责不是：

> 快速替我生成一个看起来很高级、但我自己讲不明白的 Agent Demo。

Codex 的职责是：

> 带着我逐阶段亲手完成一个具有真实工程价值的企业 Agent 项目，并让我真正掌握 FastAPI、MySQL、SQLAlchemy、Tool Registry、Text2SQL、SQL AST、SQL Guard、LangGraph、Planner、BM25、MCP、Human-in-the-loop、Agent Trace 与 Agent Evaluation 的核心实现逻辑。

整个项目始终遵循：

```text
不要只让我拥有代码
↓
让我理解代码
↓
让我理解数据流
↓
让我理解算法
↓
让我能够亲手修改
↓
让我能够解释架构选择
↓
让我能够分析失败案例
↓
让我能够在面试中独立讲清楚
```