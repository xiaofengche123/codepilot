"""
码搭 CodePilot · MCP 协议

JSON-RPC 2.0 消息类型 + MCP 方法模型。
纯数据层，无 I/O，可被 server 和 client 共用。
"""

from typing import Optional, Union
from pydantic import BaseModel, Field


# ── JSON-RPC 2.0 核心类型 ──────────────────────────────────────

class JSONRPCRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: Optional[Union[int, str]] = None
    method: str
    params: dict = Field(default_factory=dict)


class JSONRPCResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: Union[int, str]
    result: dict


class JSONRPCError(BaseModel):
    jsonrpc: str = "2.0"
    id: Optional[Union[int, str]] = None
    error: dict  # {code: int, message: str, data?: any}


class JSONRPCNotification(BaseModel):
    jsonrpc: str = "2.0"
    method: str
    params: dict = Field(default_factory=dict)


# ── 标准 JSON-RPC 错误码 ────────────────────────────────────────

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


# ── MCP 协议版本 ────────────────────────────────────────────────

PROTOCOL_VERSION = "2024-11-05"


# ── MCP 工具 schema（tools/list 返回格式） ──────────────────────

class ToolSchema(BaseModel):
    name: str
    description: str
    inputSchema: dict = Field(default_factory=dict)


# ── 序列化/反序列化 ────────────────────────────────────────────

def parse_message(data: str) -> JSONRPCRequest | JSONRPCNotification:
    """从 JSON 字符串解析为 JSON-RPC 消息。无 id 视为通知。"""
    import json
    obj = json.loads(data)
    if "id" in obj and obj["id"] is not None:
        return JSONRPCRequest(**obj)
    return JSONRPCNotification(**obj)


def format_message(msg: BaseModel) -> str:
    """序列化 JSON-RPC 消息为 JSON 字符串。"""
    return msg.model_dump_json(exclude_none=True)
