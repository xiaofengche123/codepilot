"""声明式工具注册中心。

工具实现只依赖三类元数据：可调用函数、JSON Schema 和风险等级。
Agent 与 MCP 只消费注册结果，不需要知道具体工具模块。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Callable


class RiskLevel(str, Enum):
    SAFE = "safe"
    CONFIRM = "confirm"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    function: Callable
    definition: dict
    risk: RiskLevel = RiskLevel.SAFE


class ToolRegistry:
    def __init__(self):
        self._specs: dict[str, ToolSpec] = {}

    def register(
        self,
        function: Callable,
        definition: dict,
        risk: RiskLevel = RiskLevel.SAFE,
    ) -> ToolSpec:
        """注册工具；名称从 JSON Schema 的 function.name 读取。"""
        name = definition.get("function", {}).get("name", "")
        if not name:
            raise ValueError("tool definition requires function.name")
        if name in self._specs:
            raise ValueError(f"duplicate tool: {name}")
        spec = ToolSpec(name=name, function=function, definition=definition, risk=risk)
        self._specs[name] = spec
        return spec

    def register_group(
        self,
        functions: dict[str, Callable],
        definitions: list[dict],
        dangerous: set[str] | None = None,
    ):
        """兼容现有工具模块，并将其转换为统一 ToolSpec。"""
        dangerous = dangerous or set()
        definitions_by_name = {
            item.get("function", {}).get("name"): item for item in definitions
        }
        if set(functions) != set(definitions_by_name):
            missing_schema = set(functions) - set(definitions_by_name)
            missing_function = set(definitions_by_name) - set(functions)
            raise ValueError(
                f"tool registry mismatch: missing_schema={missing_schema}, "
                f"missing_function={missing_function}"
            )
        for name, function in functions.items():
            risk = RiskLevel.CONFIRM if name in dangerous else RiskLevel.SAFE
            self.register(function, definitions_by_name[name], risk)

    @property
    def functions(self) -> dict[str, Callable]:
        return {name: spec.function for name, spec in self._specs.items()}

    @property
    def definitions(self) -> list[dict]:
        return [spec.definition for spec in self._specs.values()]

    @property
    def dangerous_tools(self) -> set[str]:
        return {
            name for name, spec in self._specs.items()
            if spec.risk in {RiskLevel.CONFIRM, RiskLevel.FORBIDDEN}
        }

    def risk_of(self, name: str) -> RiskLevel:
        spec = self._specs.get(name)
        return spec.risk if spec else RiskLevel.FORBIDDEN
