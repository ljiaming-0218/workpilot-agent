"""Insert deterministic, linked demo evidence without deleting user data."""

from datetime import datetime, timedelta, timezone

from pydantic import ValidationError
from sqlalchemy import inspect, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

import backend.models  # Register ORM metadata before checking the schema.
from backend.config import Settings
from backend.database import build_engine
from backend.models import ErrorLog, KnowledgeDoc, Ticket, User
from backend.models.enums import (
    LogLevel,
    TicketCategory,
    TicketPriority,
    TicketStatus,
    UserRole,
)


REQUIRED_TABLES = {"users", "tickets", "error_logs", "knowledge_docs"}


USER_DATA = [
    {"username": "demo_developer", "role": UserRole.DEVELOPER},
    {"username": "demo_operator", "role": UserRole.OPERATOR},
    {"username": "demo_admin", "role": UserRole.ADMIN},
]


TICKET_DATA = [
    {
        "title": "[DEMO] payment-service 高峰期数据库连接池耗尽",
        "content": "支付接口在流量高峰持续返回 500，日志出现 QueuePool timeout 和 Too many connections。",
        "category": TicketCategory.INCIDENT,
        "priority": TicketPriority.CRITICAL,
        "status": TicketStatus.RESOLVED,
        "service_name": "payment-service",
        "resolution": "定位到慢查询占用连接时间过长；优化查询并调整连接池容量后恢复。",
        "created_minutes_ago": 43_200,
        "updated_minutes_ago": 42_960,
    },
    {
        "title": "[DEMO] payment-service 结算接口集中出现 500",
        "content": "最近一小时支付创建接口失败率升高，需要核对数据库连接池、慢查询和上游依赖。",
        "category": TicketCategory.INCIDENT,
        "priority": TicketPriority.CRITICAL,
        "status": TicketStatus.PROCESSING,
        "service_name": "payment-service",
        "resolution": None,
        "created_minutes_ago": 28,
        "updated_minutes_ago": 8,
    },
    {
        "title": "[DEMO] payment-service 慢查询导致连接等待",
        "content": "账单汇总查询执行时间超过 20 秒，连接池等待请求明显增加。",
        "category": TicketCategory.INCIDENT,
        "priority": TicketPriority.HIGH,
        "status": TicketStatus.RESOLVED,
        "service_name": "payment-service",
        "resolution": "增加联合索引并拆分汇总查询，连接平均占用时间下降。",
        "created_minutes_ago": 103_680,
        "updated_minutes_ago": 102_960,
    },
    {
        "title": "[DEMO] payment-service 支付网关偶发 502",
        "content": "第三方支付网关短时返回 502，本地数据库指标正常。",
        "category": TicketCategory.INCIDENT,
        "priority": TicketPriority.MEDIUM,
        "status": TicketStatus.CLOSED,
        "service_name": "payment-service",
        "resolution": "上游网关恢复后关闭，未调整数据库连接池。",
        "created_minutes_ago": 172_800,
        "updated_minutes_ago": 172_200,
    },
    {
        "title": "[DEMO] order-service Redis 读取超时",
        "content": "订单详情接口延迟升高，缓存读取多次超过 1 秒。",
        "category": TicketCategory.INCIDENT,
        "priority": TicketPriority.HIGH,
        "status": TicketStatus.RESOLVED,
        "service_name": "order-service",
        "resolution": "修复 Redis 热点 Key，并增加读取超时监控。",
        "created_minutes_ago": 20_160,
        "updated_minutes_ago": 19_800,
    },
    {
        "title": "[DEMO] order-service 库存服务响应缓慢",
        "content": "下单请求等待 inventory-service，订单服务自身无数据库错误。",
        "category": TicketCategory.INCIDENT,
        "priority": TicketPriority.MEDIUM,
        "status": TicketStatus.CLOSED,
        "service_name": "order-service",
        "resolution": "库存服务扩容后延迟恢复。",
        "created_minutes_ago": 63_360,
        "updated_minutes_ago": 62_900,
    },
    {
        "title": "[DEMO] order-service 缓存命中率下降",
        "content": "Redis 缓存命中率下降导致数据库查询量上升。",
        "category": TicketCategory.INCIDENT,
        "priority": TicketPriority.MEDIUM,
        "status": TicketStatus.OPEN,
        "service_name": "order-service",
        "resolution": None,
        "created_minutes_ago": 55,
        "updated_minutes_ago": 20,
    },
    {
        "title": "[DEMO] auth-service JWT 校验集中返回 401",
        "content": "Token 尚未到业务过期时间但被部分实例判定为 expired。",
        "category": TicketCategory.INCIDENT,
        "priority": TicketPriority.HIGH,
        "status": TicketStatus.RESOLVED,
        "service_name": "auth-service",
        "resolution": "修复节点 NTP 时间偏差并重启时间同步服务。",
        "created_minutes_ago": 31_680,
        "updated_minutes_ago": 31_200,
    },
    {
        "title": "[DEMO] auth-service 签名密钥轮换检查",
        "content": "密钥轮换期间检查新旧 Key ID 的兼容窗口。",
        "category": TicketCategory.OPERATION,
        "priority": TicketPriority.MEDIUM,
        "status": TicketStatus.CLOSED,
        "service_name": "auth-service",
        "resolution": "确认所有实例加载同一版本的公钥集合。",
        "created_minutes_ago": 86_400,
        "updated_minutes_ago": 85_900,
    },
    {
        "title": "[DEMO] auth-service 登录接口偶发 401",
        "content": "少量客户端继续使用已经撤销的 Refresh Token。",
        "category": TicketCategory.INCIDENT,
        "priority": TicketPriority.LOW,
        "status": TicketStatus.RESOLVED,
        "service_name": "auth-service",
        "resolution": "客户端重新登录后恢复。",
        "created_minutes_ago": 7_200,
        "updated_minutes_ago": 7_000,
    },
    {
        "title": "[DEMO] 通用 HTTP 500 告警分级规则调整",
        "content": "根据持续时间和影响请求数调整服务端 500 告警等级。",
        "category": TicketCategory.REQUIREMENT,
        "priority": TicketPriority.LOW,
        "status": TicketStatus.CLOSED,
        "service_name": "monitoring-service",
        "resolution": "已上线新的告警聚合规则。",
        "created_minutes_ago": 14_400,
        "updated_minutes_ago": 13_900,
    },
    {
        "title": "[DEMO] 数据库容量周巡检",
        "content": "检查连接数、慢查询数量和磁盘增长趋势。",
        "category": TicketCategory.OPERATION,
        "priority": TicketPriority.LOW,
        "status": TicketStatus.OPEN,
        "service_name": "database-platform",
        "resolution": None,
        "created_minutes_ago": 1_440,
        "updated_minutes_ago": 1_200,
    },
]


KNOWLEDGE_DATA = [
    {
        "source": "seed://workpilot/payment-db-pool-runbook",
        "title": "payment-service 500 与数据库连接池耗尽排查",
        "category": "runbook",
        "content": (
            "当 payment-service 大量返回 HTTP 500，且日志包含 QueuePool timeout、"
            "connection checkout timeout 或 Too many connections 时，优先检查连接池活跃数、"
            "等待数和慢查询。先确认数据库可用性，再定位长事务与未释放连接；修复查询后评估"
            "pool_size 和 max_overflow。禁止只扩大连接池而忽略慢查询。"
        ),
    },
    {
        "source": "seed://workpilot/mysql-pool-capacity",
        "title": "MySQL 连接池容量与慢查询检查清单",
        "category": "database",
        "content": (
            "检查应用连接池上限、MySQL max_connections、连接等待时间、长事务和慢查询。"
            "连接池等待增长而数据库仍可连接，通常表示连接被长时间占用。调整容量前必须确认"
            "应用实例数乘以单实例连接上限不会超过数据库容量。"
        ),
    },
    {
        "source": "seed://workpilot/payment-gateway-502",
        "title": "第三方支付网关 502 排查",
        "category": "runbook",
        "content": "网关 502 应核对上游状态、DNS、TLS 和超时指标；若数据库连接池正常，不应归因于数据库。",
    },
    {
        "source": "seed://workpilot/payment-idempotency",
        "title": "支付请求幂等与重复扣款防护",
        "category": "design",
        "content": "支付写请求使用业务幂等键，重试前查询原请求状态，避免网络超时造成重复扣款。",
    },
    {
        "source": "seed://workpilot/order-redis-timeout",
        "title": "order-service Redis 超时排查",
        "category": "runbook",
        "content": "检查 Redis P99 延迟、热点 Key、连接数、网络抖动和缓存穿透，并与数据库负载对照。",
    },
    {
        "source": "seed://workpilot/order-inventory-latency",
        "title": "订单链路下游延迟定位",
        "category": "runbook",
        "content": "按 Trace 区分 order-service、inventory-service 和 payment-service 的耗时，避免只看入口总延迟。",
    },
    {
        "source": "seed://workpilot/auth-jwt-clock-skew",
        "title": "auth-service JWT 过期与时钟偏差排查",
        "category": "runbook",
        "content": "JWT 在部分节点提前过期时检查系统时间、NTP 同步和允许的 clock skew，再检查签名密钥版本。",
    },
    {
        "source": "seed://workpilot/auth-key-rotation",
        "title": "JWT 签名密钥安全轮换",
        "category": "security",
        "content": "轮换期间同时发布新旧公钥，按 Key ID 校验，并在旧 Token 生命周期结束后撤销旧密钥。",
    },
    {
        "source": "seed://workpilot/http-500-triage",
        "title": "服务端 HTTP 500 通用排查顺序",
        "category": "runbook",
        "content": "先确认影响范围和时间窗口，再关联 request_id、错误类型、依赖指标、历史工单和变更记录。",
    },
    {
        "source": "seed://workpilot/mysql-slow-query-index",
        "title": "MySQL 慢查询与索引失效诊断",
        "category": "database",
        "content": (
            "先用慢查询日志确认 SQL 指纹、调用次数和 P95 耗时，再用 EXPLAIN 检查访问类型、"
            "扫描行数与实际使用索引。函数包裹索引列、隐式类型转换和联合索引最左前缀缺失，"
            "都可能导致索引失效。优化后应对比执行计划与业务延迟，禁止直接在生产库执行写操作。"
        ),
    },
    {
        "source": "seed://workpilot/sqlalchemy-session-leak",
        "title": "SQLAlchemy Session 与连接泄漏排查",
        "category": "runbook",
        "content": (
            "每个 HTTP 请求应创建独立 Session，并在 finally 中关闭。异常事务先 rollback，"
            "再继续审计或返回错误。若连接池 active 持续增长且请求结束后不下降，检查未关闭的"
            "Session、流式结果集和长事务。不要在并发请求间共享 Session。"
        ),
    },
    {
        "source": "seed://workpilot/deployment-change-correlation",
        "title": "故障时间窗与发布变更关联方法",
        "category": "incident",
        "content": (
            "把错误率开始上升的时间与应用发布、配置变更、依赖升级和数据库变更对齐。"
            "时间接近只能形成候选原因，还需通过版本分组、实例对照或回滚后的指标变化补充证据。"
        ),
    },
    {
        "source": "seed://workpilot/redis-cache-breakdown",
        "title": "Redis 热点 Key 与缓存击穿处理",
        "category": "runbook",
        "content": (
            "缓存命中率下降且数据库请求突增时，检查热点 Key 是否集中失效。可采用过期时间抖动、"
            "请求合并、互斥重建和限流降低回源压力，并持续观察 Redis P99 与数据库连接数。"
        ),
    },
    {
        "source": "seed://workpilot/incident-evidence-quality",
        "title": "Agent 故障分析证据质量规则",
        "category": "agent",
        "content": (
            "日志说明当前发生了什么，历史工单提供相似案例，知识文档提供排查方法。"
            "Agent 应区分事实、推断和建议；没有命中记录时必须明确证据不足，不能把空结果解释为系统正常。"
        ),
    },
]


LOG_GROUPS = [
    {
        "service_name": "payment-service",
        "error_type": "500",
        "prefix": "demo-pay",
        "offsets": [3, 5, 7, 9, 11, 14, 17, 21, 26, 32, 41, 53],
        "messages": [
            "POST /api/payments returned 500; QueuePool limit size 10 overflow 5 reached, connection timed out.",
            "Database connection checkout timed out after 30 seconds; active=15 waiting=8.",
            "Payment transaction rolled back after pymysql OperationalError: Too many connections.",
        ],
        "stack_trace": "sqlalchemy.exc.TimeoutError: QueuePool connection checkout timed out",
    },
    {
        "service_name": "order-service",
        "error_type": "RedisTimeout",
        "prefix": "demo-order",
        "offsets": [6, 12, 19, 29, 44, 67, 88, 116],
        "messages": [
            "GET /api/orders timed out while reading Redis key order:summary.",
            "Redis command GET exceeded 1000 ms; falling back to database.",
        ],
        "stack_trace": "redis.exceptions.TimeoutError: Timeout reading from socket",
    },
    {
        "service_name": "auth-service",
        "error_type": "401",
        "prefix": "demo-auth",
        "offsets": [8, 16, 24, 37, 49, 73, 105],
        "messages": [
            "JWT rejected with 401 because token exp is earlier than local node time.",
            "Authentication failed; detected 94 second clock difference between instances.",
        ],
        "stack_trace": "TokenExpiredError: JWT validation failed on exp claim",
    },
    {
        "service_name": "payment-service",
        "error_type": "SlowQuery",
        "prefix": "demo-pay-slow",
        "offsets": [4, 8, 13, 18, 24, 31, 39, 48, 58],
        "messages": [
            "SELECT payment_orders exceeded 2500 ms and held a pooled connection.",
            "Payment reconciliation query scanned 183420 rows; expected index was not used.",
            "Database P95 latency exceeded threshold while connection waiters increased.",
        ],
        "stack_trace": "sqlalchemy.exc.OperationalError: query execution exceeded timeout",
    },
    {
        "service_name": "database-platform",
        "error_type": "ConnectionPressure",
        "prefix": "demo-db-pressure",
        "offsets": [10, 22, 35, 51, 79, 121],
        "messages": [
            "Active connections reached 92 percent of the configured database limit.",
            "Long-running transaction remained open for more than 180 seconds.",
            "Connection acquisition P95 exceeded 1500 ms across application instances.",
        ],
        "stack_trace": "DatabaseCapacityWarning: connection pressure threshold exceeded",
    },
    {
        "service_name": "inventory-service",
        "error_type": "UpstreamTimeout",
        "prefix": "demo-inventory",
        "offsets": [15, 33, 62, 94, 138],
        "messages": [
            "Inventory reservation timed out while calling warehouse gateway.",
            "Warehouse upstream returned 504 after 2000 ms.",
        ],
        "stack_trace": "httpx.ReadTimeout: warehouse gateway did not respond",
    },
]


def seed_data(session: Session, now: datetime) -> dict[str, dict[str, int]]:
    """Upsert only records carrying stable demo identifiers."""
    stats = {
        name: {"created": 0, "updated": 0}
        for name in ("users", "tickets", "error_logs", "knowledge_docs")
    }
    _seed_users(session, stats)
    _seed_tickets(session, now, stats)
    _seed_logs(session, now, stats)
    _seed_knowledge(session, now, stats)
    return stats


def _seed_users(session: Session, stats: dict[str, dict[str, int]]) -> None:
    for values in USER_DATA:
        row = session.scalar(select(User).where(User.username == values["username"]))
        created = row is None
        if row is None:
            row = User(**values)
            session.add(row)
        else:
            row.role = values["role"]
        _record(stats, "users", created)


def _seed_tickets(
    session: Session,
    now: datetime,
    stats: dict[str, dict[str, int]],
) -> None:
    for values in TICKET_DATA:
        row = session.scalars(
            select(Ticket).where(
                Ticket.title == values["title"],
                Ticket.service_name == values["service_name"],
            )
        ).first()
        created = row is None
        if row is None:
            row = Ticket()
            session.add(row)
        for field in (
            "title", "content", "category", "priority", "status",
            "service_name", "resolution",
        ):
            setattr(row, field, values[field])
        row.created_at = now - timedelta(minutes=values["created_minutes_ago"])
        row.updated_at = now - timedelta(minutes=values["updated_minutes_ago"])
        _record(stats, "tickets", created)


def _seed_logs(
    session: Session,
    now: datetime,
    stats: dict[str, dict[str, int]],
) -> None:
    for group in LOG_GROUPS:
        for index, offset in enumerate(group["offsets"], start=1):
            request_id = f"{group['prefix']}-{index:03d}"
            row = session.scalars(
                select(ErrorLog).where(ErrorLog.request_id == request_id)
            ).first()
            created = row is None
            if row is None:
                row = ErrorLog()
                session.add(row)
            row.service_name = group["service_name"]
            row.level = LogLevel.CRITICAL if index % 5 == 0 else LogLevel.ERROR
            row.error_type = group["error_type"]
            row.message = group["messages"][(index - 1) % len(group["messages"])]
            row.stack_trace = group["stack_trace"]
            row.request_id = request_id
            row.created_at = now - timedelta(minutes=offset)
            _record(stats, "error_logs", created)


def _seed_knowledge(
    session: Session,
    now: datetime,
    stats: dict[str, dict[str, int]],
) -> None:
    for values in KNOWLEDGE_DATA:
        row = session.scalars(
            select(KnowledgeDoc).where(KnowledgeDoc.source == values["source"])
        ).first()
        created = row is None
        if row is None:
            row = KnowledgeDoc()
            session.add(row)
        for field in ("source", "title", "category", "content"):
            setattr(row, field, values[field])
        row.created_at = now - timedelta(days=90)
        row.updated_at = now - timedelta(days=2)
        _record(stats, "knowledge_docs", created)


def _record(
    stats: dict[str, dict[str, int]],
    table: str,
    created: bool,
) -> None:
    stats[table]["created" if created else "updated"] += 1


def main() -> int:
    engine = None
    session = None
    try:
        settings = Settings()
        engine = build_engine(settings)
        existing_tables = set(inspect(engine).get_table_names())
        missing_tables = sorted(REQUIRED_TABLES - existing_tables)
        if missing_tables:
            print(
                "SCHEMA_ERROR: missing tables "
                + ", ".join(missing_tables)
                + "; run python -m scripts.init_db first."
            )
            return 1

        factory = sessionmaker(bind=engine, autoflush=False)
        session = factory()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        stats = seed_data(session, now)
        session.commit()

        summary = "; ".join(
            f"{table}: created={counts['created']}, updated={counts['updated']}"
            for table, counts in stats.items()
        )
        print("Demo data committed. " + summary)
        print("Try: 分析 payment-service 最近 60 分钟大量出现 500 错误的可能原因")
        return 0
    except ValidationError:
        if session is not None:
            session.rollback()
        print("CONFIG_ERROR: check the project .env configuration.")
        return 1
    except SQLAlchemyError as exc:
        if session is not None:
            session.rollback()
        print(
            f"DATABASE_ERROR ({type(exc).__name__}): "
            "check connectivity, target database, schema, and write permission."
        )
        return 1
    except Exception:
        if session is not None:
            session.rollback()
        raise
    finally:
        if session is not None:
            session.close()
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
