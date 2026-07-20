"""
码搭 CodePilot · MCP Server

通过 stdio 以 MCP 协议暴露 CodePilot 工具，供 Claude Desktop 等外部客户端调用。
启动方式: python -m mcp.server
"""

import sys
import json
import logging
from typing import Optional

from mcp.protocol import (
    JSONRPCRequest,
    JSONRPCResponse,
    JSONRPCError,
    JSONRPCNotification,
    parse_message,
    format_message,
    PARSE_ERROR,
    METHOD_NOT_FOUND,
    INVALID_PARAMS,
    INTERNAL_ERROR,
    PROTOCOL_VERSION,
)
from tools import TOOL_DEFINITIONS, DANGEROUS_TOOLS, execute_tool
from config import config

# stderr 用于日志，stdout 用于 JSON-RPC 消息
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger(__name__)

SERVER_NAME = "codepilot"
SERVER_VERSION = "1.0.0"


class MCPServer:
    def __init__(self):
        self.initialized = False
        self.server_info = {"name": SERVER_NAME, "version": SERVER_VERSION}
        self.capabilities = {"tools": {}}

    def run(self):
        """主循环：逐行读取 stdin 的 JSON-RPC 请求，处理后写入 stdout。"""
        logger.info("MCP Server 启动，等待连接...")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = parse_message(line)
            except Exception as e:
                logger.error(f"解析消息失败: {e}")
                self._send_error(None, PARSE_ERROR, f"Parse error: {e}")
                continue

            response = self._dispatch(msg)
            if response:
                self._send(response)

    def _dispatch(self, msg: JSONRPCRequest | JSONRPCNotification) -> Optional[JSONRPCResponse | JSONRPCError]:
        """路由到对应的处理方法。"""
        method = msg.method

        if method == "initialize":
            return self._handle_initialize(msg)
        elif method == "initialized":
            return None  # 通知，无需回复
        elif method == "ping":
            return self._handle_ping(msg)
        elif method == "tools/list":
            return self._handle_tools_list(msg)
        elif method == "tools/call":
            return self._handle_tools_call(msg)
        else:
            msg_id = getattr(msg, "id", None)
            return self._make_error(msg_id, METHOD_NOT_FOUND, f"Unknown method: {method}")

    # ── MCP 方法处理器 ──────────────────────────────────────

    def _handle_initialize(self, msg: JSONRPCRequest) -> JSONRPCResponse:
        self.initialized = True
        logger.info(f"客户端初始化: {msg.params.get('clientInfo', {})}")
        return JSONRPCResponse(
            id=msg.id,
            result={
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": self.capabilities,
                "serverInfo": self.server_info,
            },
        )

    def _handle_ping(self, msg: JSONRPCRequest) -> JSONRPCResponse:
        return JSONRPCResponse(id=msg.id, result={})

    def _handle_tools_list(self, msg: JSONRPCRequest) -> JSONRPCResponse:
        tools = []
        for td in TOOL_DEFINITIONS:
            tools.append({
                "name": td["function"]["name"],
                "description": td["function"]["description"],
                "inputSchema": td["function"]["parameters"],
            })
        return JSONRPCResponse(id=msg.id, result={"tools": tools})

    def _handle_tools_call(self, msg: JSONRPCRequest) -> JSONRPCResponse | JSONRPCError:
        params = msg.params or {}
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if not tool_name:
            return self._make_error(msg.id, INVALID_PARAMS, "Missing tool name")

        logger.info(f"调用工具: {tool_name}({arguments})")

        # 危险工具（run_shell / git_add / git_commit）需要交互确认，
        # MCP 是无交互通道，默认拒绝；配置 mcp.allow_dangerous: true 可放开。
        if tool_name in DANGEROUS_TOOLS and not config.get("mcp.allow_dangerous", False):
            return JSONRPCResponse(id=msg.id, result={
                "content": [{"type": "text", "text": (
                    f"[拒绝] 危险工具 {tool_name} 在 MCP 模式下默认禁用"
                    "（无法交互确认）。在 config/settings.yaml 中设置"
                    " mcp.allow_dangerous: true 可放开。"
                )}],
            })

        try:
            result_text = execute_tool(tool_name, arguments)
        except Exception as e:
            logger.exception(f"工具执行异常: {tool_name}")
            return self._make_error(msg.id, INTERNAL_ERROR, f"Tool execution failed: {e}")

        return JSONRPCResponse(id=msg.id, result={
            "content": [{"type": "text", "text": result_text}],
        })

    # ── 消息收发 ────────────────────────────────────────────

    def _send(self, msg: JSONRPCResponse | JSONRPCError):
        sys.stdout.write(format_message(msg) + "\n")
        sys.stdout.flush()

    def _send_error(self, msg_id, code: int, message: str):
        self._send(JSONRPCError(id=msg_id, error={"code": code, "message": message}))

    def _make_error(self, msg_id, code: int, message: str) -> JSONRPCError:
        return JSONRPCError(id=msg_id, error={"code": code, "message": message})


def main():
    server = MCPServer()
    server.run()


if __name__ == "__main__":
    main()
