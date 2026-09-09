"""Deterministic risk classification before a plan executes."""

from typing import Any

from backend.models.enums import RiskLevel
from backend.schemas.risk import RiskAssessment
from backend.tools.registry import ToolNotFoundError, ToolRegistry


RISK_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
}


class RiskCheckError(RuntimeError):
    code = "RISK_CHECK_ERROR"


class RiskChecker:
    """Combine declared tool risk with workflow-level output risk."""

    def assess(
        self,
        intent: str,
        plan: list[dict[str, Any]],
        registry: ToolRegistry,
    ) -> RiskAssessment:
        level = RiskLevel.LOW
        reasons: list[str] = []

        for step in plan:
            tool_name = step.get("tool")
            if not isinstance(tool_name, str):
                raise RiskCheckError("Plan step has no valid tool name.")
            try:
                definition = registry.get_tool(tool_name)
            except ToolNotFoundError as exc:
                raise RiskCheckError(
                    f"Cannot assess an unregistered tool: {tool_name}"
                ) from exc
            if RISK_ORDER[definition.risk_level] > RISK_ORDER[level]:
                level = definition.risk_level
            if definition.risk_level != RiskLevel.LOW:
                reasons.append(
                    f"工具 {tool_name} 声明为 {definition.risk_level.value} 风险。"
                )

        if intent == "incident_analysis":
            if level == RiskLevel.LOW:
                level = RiskLevel.MEDIUM
            reasons.append("故障分析会生成运维操作建议，执行前需要人工核对。")

        if not reasons:
            reasons.append("计划只包含已注册的只读 LOW 风险工具。")

        return RiskAssessment(
            risk_level=level,
            requires_approval=level == RiskLevel.HIGH,
            reasons=reasons,
        )
