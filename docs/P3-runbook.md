# P3 持久任务运行说明

使用现有 `workpilot` Conda 环境，从项目根目录执行。MySQL/TiDB 必须支持
跨连接的 `GET_LOCK` / `RELEASE_LOCK`；审批与恢复通过会话锁互斥。

## 启用

先停止旧 API 和 Worker，执行增量迁移，然后重启所有 API 进程：

```powershell
cd "D:\WorkPilot Agent\workpilot-agent"
conda activate workpilot
python -m scripts.migrate_p3
$env:P3_DURABLE_ENABLED='true'
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

另一终端启动独立 Worker（可再开第二个终端验证竞争领取）：

```powershell
cd "D:\WorkPilot Agent\workpilot-agent"
conda activate workpilot
$env:P3_DURABLE_ENABLED='true'
python -m scripts.run_p3_worker
```

也可在 `.env` 中设置 `P3_DURABLE_ENABLED=true`。不要将密钥或 `.env` 提交。
`POST /agent/runs/async` 在此模式下要求 `Idempotency-Key`：重传同一次提交
保留原键；主动发起新调查必须使用新键。

## 验证

```powershell
python -m pytest tests/test_agent.py tests/test_multi_agent.py tests/test_p2_scope.py -q
$env:P3_EXPERIMENTAL_APPROVAL_LOCK_TEST='1'
python -m scripts.smoke_p3_attempt_db
```

数据库 smoke 使用随机命名探针表、固定测试账号和模拟工具，结束后删除自己的
探针表；会启动并终止自己的测试子进程，需建表权限，通常耗时数分钟。
若连接中断，应根据输出的 `Probe table suffix` 核对并清理该次探针表，
不得批量删除未核实归属的表。

## 状态与故障处理

- 任务和服务范围在同一事务保存；Worker 领取后创建独立执行 run。
- `/agent/runs/{id}/attempts` 返回尝试历史，不暴露领取令牌。
- API 详情和 Trace 映射到已发布的执行 run；内部 run 不显示在公开历史中。
- SSE 从数据库读取当前 attempt 的 Trace，重连使用 `Last-Event-ID`。
- Worker 被终止后，只读任务可在租约过期后有限重试。模型调用可能重复计费。
- 审批执行期间，其他恢复进程跳过该审批；进程中断释放会话锁后，已领取且
  结果未知的审批标记失败，不自动重放。
- 数据库失联时不能假设续租或提交成功。恢复后检查队列状态、attempt 和审批
  checkpoint，不把数据库里的 waiting 状态当作内存 checkpoint 存在的证明。

高风险操作仍是模拟操作。本项目不承诺外部副作用 exactly-once。
停用持久提交开关后，已有持久任务仍通过持久视图读取；停 Worker 不会取消任务。

## 学习与讲解

本阶段重点：事务内入队、数据库幂等唯一约束、租约与领取令牌、attempt 隔离、
审批执行和恢复互斥。练习：画出旧 Worker 迟到、新 Worker 发布结果的时序图。

理解检查：为什么只在最终报告写入时检查令牌还不够？为什么新调查必须换幂等键？
为什么数据库断线后不能假设外部操作没有执行？

面试问题与要点：

- 如何解决并发重复提交？数据库唯一约束兜底，同键比较请求指纹，事务失败后读取原任务。
- 如何防止旧 Worker 覆盖结果？独立执行 run 保存证据，发布时原子校验当前领取令牌和租约。
- 为什么不承诺 exactly-once？进程或网络中断可能使副作用结果未知，不能自动重放。
