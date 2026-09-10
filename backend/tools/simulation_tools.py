"""Safe Phase 11 tool that simulates a HIGH-risk operation."""

from backend.models.enums import RiskLevel
from backend.schemas.tool import (
    SimulateHighRiskOperationInput,
    SimulateHighRiskOperationOutput,
)
from backend.tools.base import BaseTool, ToolContext


class SimulateHighRiskOperationTool(
    BaseTool[SimulateHighRiskOperationInput, SimulateHighRiskOperationOutput]
):
    name = "simulate_high_risk_operation"
    description = "Simulate a high-risk operation without changing any real system."
    input_schema = SimulateHighRiskOperationInput
    output_schema = SimulateHighRiskOperationOutput
    risk_level = RiskLevel.HIGH

    def execute(
        self,
        context: ToolContext,
        tool_input: SimulateHighRiskOperationInput,
    ) -> SimulateHighRiskOperationOutput:
        return SimulateHighRiskOperationOutput(
            simulated=True,
            message=f"已模拟执行高风险请求：{tool_input.request}",
        )
