# P4 真实场景与协作

## Current Phase

当前阶段：第 133 节 P4。本地已实现告警持久入队、可控 HTTP 测试源、团队协作、
证据来源与待确认事项展示。没有提交 Git 或部署。本机验证案例见末尾。

## Why / Learning Goals

P3 解决执行的持久性；P4 解决告警如何进入任务、谁处理结果、如何交接。
需要理解：事务内入队与事件幂等、执行状态和人工状态分离、乐观并发控制、
按服务授权，以及声明来源与独立质量评估的区别。

## Principle / Data Flow

```text
本地 checkout HTTP 请求 → dependency 真实返回 503 → 实际 HTTPError 与 request_id
    ↓
POST /ingestion/logs（密钥 + 登录 + 服务授权）
    ↓
POST /alerts/incidents（相同边界）
    ↓ 同一个数据库事务
公开 AgentRun + 明确服务范围 + DurableJob + IncidentSubmission
    ↓ 202，返回 run_id / events_url
独立 Worker → 新 attempt / 私有 run → Multi-Agent → Tool Registry → MCP → 数据库
    ↓ 有效租约及令牌校验后发布
Console：执行状态 / 原始证据 / 待确认事项
    ↓ 团队成员，重新验证对象和服务权限
负责人 + 人工处理状态 + 结果反馈 + 追加备注和版本历史
```

告警正文不作为自由指令：调查 query 只包含规范化服务、时间窗和固定只读调查目标。
告警名称、错误类型、来源和事件编号保存在告警记录供查看，不用于拓展服务范围或
发出操作指令；第一版不按 `error_type` 单独过滤，保留整个服务窗口的日志。
服务范围由已授权的结构化字段保存。Worker 开始时复核授权，其他安全边界沿用 P2/P3。
告警按团队 + source + event_id 去重，相同完整规范化 payload 返回原任务；
内容有任何变化返回 409。新的事故或主动新调查必须换 event_id。

人工状态：`open`（待认领）、`in_progress`（处理中）、`resolved`（已处理）。
反馈：`unreviewed`、`supported`、`needs_correction`、`inconclusive`。
人工记录关联稳定公开 run_id，Worker 重试不会覆盖。标记已处理需要负责人、
反馈和说明；提交反馈前，执行必须已经结束。处理失败的模型调查也可以由人接手。
这些人工反馈是演示协作数据，不是独立标注的根因真值。

每次 PATCH 要携带 `expected_version`（首次为 0）。数据库条件更新或唯一约束
决定唯一胜者；状态和追加审计记录同事务提交。冲突返回 409，页面保留草稿，
先复制草稿，再点“刷新任务与协作”后重试。只显示最近 50 条交接记录，同时返回总数。

## Files Changed / Code Explanation

| 文件 | 职责、输入与输出 |
| --- | --- |
| backend/models/investigation.py | 告警来源、人工处理记录和只追加的交接记录；与公开 run 关联 |
| backend/schemas/alert.py | 限长告警、来源声明、202 响应；额外字段拒绝 |
| backend/schemas/investigation.py | PATCH 的版本、负责人、处理状态、反馈和备注校验 |
| backend/services/alert_service.py | 校验告警服务、事务内入队及去重；冲突返回 409，数据库错误回滚 |
| backend/services/agent_service.py | prepare_agent_run 新增服务端 target_services 参数；原调用默认行为保留 |
| backend/services/investigation_service.py | 读协作视图、检查负责人、版本条件更新、追加备注；外队负责人 422，过期版本 409 |
| backend/routers/alert_router.py | 告警鉴权与入队；队列关闭或迁移缺失时 503 |
| backend/routers/investigation_router.py | 协作 GET/PATCH 和分组统计；复用公开任务授权，越权对象 404 |
| backend/main.py、backend/models/__init__.py | 注册模型、路由及 P4 表存在性检查 |
| frontend/app.js、index.html、styles.css | 调查协作页签、来源/缺口、认领/交接/反馈、历史及来源统计；修复长标题挤掉状态 |
| scripts/migrate_p4.py | 可重复的纯新增迁移，不重写历史归属 |
| scripts/seed_p4_demo.py | 两个团队、三个专用账号；已有账号密码保留 |
| scripts/p4_http_source.py | 仅监听 127.0.0.1 的可恢复 HTTP 依赖故障；最多保留 100 条实际失败日志 |
| scripts/submit_p4_demo.py | 实测 3 次请求，导入对应 request_id 日志，再发告警 |
| scripts/smoke_p4_http.py | 对已有演示任务做鉴权、去重、交接、历史和 SSE 验收；追加一条带标识的 smoke 备注 |
| scripts/smoke_p4_db.py | 隔离随机 TiDB/MySQL 表上做首提交竞争、协作竞争、来源分组和撤权检查；最后清理自身表 |
| tests/test_p4_contract.py | 元数据指令注入、禁止伪造人工字段和真实 HTTP 故障/恢复回归 |

## Algorithm Explanation

没有新增 Agent、检索或排序算法。沿用已有 Router/Planner、多 Agent、Verifier 和
MCP；确定性代码处理身份、范围、事件去重、版本比较和分组计数。
事件去重用唯一索引兜底；协作采用 compare-and-swap：只有数据库 version 等于
expected_version 时才能更新，再同时追加下一版本的审计记录。

## How to Run

使用既有 Conda 环境；以下均为 PowerShell。在迁移前停止旧 API/Worker，结束后
重启所有 API/Worker，避免旧进程运行旧代码。升级不替旧数据推断或补写团队归属。

```powershell
cd "D:\WorkPilot Agent\workpilot-agent"
conda activate workpilot
python -m scripts.migrate_p1
python -m scripts.migrate_p2
python -m scripts.migrate_p3
python -m scripts.migrate_p4

# 新环境首次使用；依次输入 3 个新账号的密码（至少 12 个字符）
python -m scripts.seed_p4_demo

$env:P3_DURABLE_ENABLED='true'
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

另一个已激活 workpilot 的终端启动 Worker：

```powershell
cd "D:\WorkPilot Agent\workpilot-agent"
conda activate workpilot
$env:P3_DURABLE_ENABLED='true'
python -m scripts.run_p3_worker
```

第三个终端启动本地测试源：

```powershell
cd "D:\WorkPilot Agent\workpilot-agent"
conda activate workpilot
python -m scripts.p4_http_source --port 8765
```

第四个终端（填写 seed 输出的团队 ID；.env 中需要配置 INGESTION_API_KEY）：

```powershell
cd "D:\WorkPilot Agent\workpilot-agent"
conda activate workpilot
python -m scripts.submit_p4_demo --username wp-p4-owner --team-id 1
```

打开命令输出的 Console 链接。使用 wp-p4-owner 或同队的 wp-p4-peer 登录，在
“调查协作”中查看、认领、交接和反馈。wp-p4-other 属于第二团队，不能读取本案例。
自动测试时产生的随机密码仅存于被 Git 忽略的 `.env.p4-demo`，不要提交或展示其内容；
在同一台电脑上可查看该文件获得这些专用账号密码。seed 不会重置已有密码。

手工恢复测试依赖并复测（这是测试源控制接口，Agent 不调用它）：

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8765/control/recover
Invoke-RestMethod http://127.0.0.1:8765/checkout
# 再次制造本地故障：
Invoke-RestMethod -Method Post http://127.0.0.1:8765/control/fail
```

## How to Test

```powershell
python -m compileall -q backend scripts
python -m pip check
node --check frontend/app.js
git diff --check
python -m pytest tests/test_p4_contract.py tests/test_agent.py tests/test_multi_agent.py tests/test_p2_scope.py -q
python -m scripts.smoke_p4_db

# 复核已有任务；该命令会追加 smoke 备注并将人工状态改为处理中
python -m scripts.smoke_p4_http --run-id 1320001 --credentials-file .env.p4-demo
```

HTTP smoke 使用已有且经历中断恢复的已完成案例；新环境请传自己的 run_id。
它不会创建新模型调查，要求上述专用测试账号及本地凭据文件。
并发 DB smoke 需建表权限，会先输出随机 suffix，只清理自身记录的探针表。
若进程被强杀，核对 suffix 后再清理对应表；不要批量删除不明表。

正常：告警 HTTP 202，返回 queue_status 和 SSE 地址；队友读取、更新，版本递增。
异常：只有 ingestion key 无登录 401；无服务权限 403；跨团队对象 404；
不同内容复用事件 409；旧版本更新 409；伪造执行状态或指定外队负责人 422。
租约过期后有新 attempt，人工记录仍位于同一公开 run 下。

## 本机验收记录（2026-11-01）

- 配置库原缺少 P1 团队和会话表；已应用 P1/P2/P3/P4 增量迁移，P4 重复执行通过。
  旧业务数据和未知归属数据没有被重新分配。
- 独立专用团队与服务；3 条真实 HTTP 503 日志经已鉴权导入。公开任务 `1320001`：
  probe Worker 被终止，attempt 1 租约过期，attempt 2 通过正常 Worker/MCP 取证并发布。
  私有执行 run 分别为 1320002、1320003；仅公开 ID 可通过 API 查看。
- 真实模型 3 次调用均出现 APITimeoutError，输出 completed / partial、保留原始日志，
  Verifier 为 NO_DIAGNOSTIC_CLAIMS。不能将本例视为一次成功的模型根因识别。
  随后的无密钥网关连通性探针也为 ConnectTimeout（10 秒）；下一步优先检查当前
  机器到模型网关的网络连接，不能将此问题归因于 RAG 未检索到记录或根因判断错误。
- 本地依赖经手工控制恢复，复测 /checkout 为 HTTP 200。页面保存“已处理 + 证据不足”，
  明确区分测试故障恢复和模型诊断仍待核实。
- HTTP smoke 26 项检查通过；TiDB 隔离 DB smoke 通过且完成探针表清理。
- 针对性回归 32 项通过；compileall、pip check、node --check、git diff --check 通过。
- 使用内置浏览器的 Playwright 控件完成实际登录、任务深链接、团队交接、人工反馈保存、
  保存后按钮可继续操作，以及统计展开。桌面 1280×720、手机 390×844 均检查，
  手机无横向溢出。修复长标题挤掉状态、第四页签换行和手机退出按钮折行。
  页面身份、非空、无错误遮罩、相关控制台错误为空、截图和交互检查均通过。
  Browser 插件与独立 Playwright 包未就绪，实际使用可用的内置浏览器完成验证。
  截图保存在仓库外 `D:\codex_work\2026-11-01\workpilot-p4\p4-desktop.png`、`p4-mobile.png`。
  没有生产事故数据、独立根因准确率评估或线上部署。

`source_kind` 是调用者声明的 synthetic / local_test / external / unspecified。
统计分组只表示任务执行和人工反馈，不验证来源真实性，也不计算根因准确率。
原有实验目录与样本完全保留。模型超时不修改为“正常”或“准确”。

## My Exercise

1. 用 peer 账号将负责人交接给 owner，保留“模型超时仍待核实”的备注。
2. 两个页面打开同一版本，分别保存不同备注，观察第二次返回 409；刷新后正确重试。

## Understanding Check

1. 为什么执行 completed 和人工 resolved 必须分别保存？
2. 为什么提交方的 source_kind 不能作为已经验证的数据真实性证明？
3. 如果协作状态保存成功、审计记录保存失败，为什么需要回滚整个事务？

## Interview Questions

1. 如何处理重复告警？要点：按团队及事件建立唯一约束，比较完整规范化 payload，
   同内容返回原任务，不同内容冲突，首提交竞争时事务回滚后读取胜者。
2. 如何避免协作互相覆盖？要点：客户端带 expected_version，数据库条件更新，
   与追加历史同事务，失败返回 409。
3. 为什么 Worker 恢复不丢人工备注？要点：人工记录关联稳定公开 run；attempt 和
   私有执行 run 隔离，只发布有效结果，Worker 没有人工记录写入逻辑。

## Next Phase

先完成本阶段理解检查；后续仅针对模型网络/供应商超时进行验证并重跑新的本地案例，
再讨论提交范围和部署。不要复用原 event_id 把新调查覆盖到旧案例上。
