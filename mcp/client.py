"""
码搭 CodePilot · MCP Client

连接外部 MCP Server，发现并代理其工具。
配置文件: mcp_servers.json
"""

import sys
import json
import subprocess
import threading
from queue import Queue, Empty

from mcp.protocol import (
    JSONRPCRequest,
    JSONRPCResponse,
    JSONRPCError,
    JSONRPCNotification,
    format_message,
    parse_message,
    METHOD_NOT_FOUND,
    INTERNAL_ERROR,
    PROTOCOL_VERSION,
)


class MCPClientConnection:
    """管理单个 MCP server 的 stdio 连接。"""

    def __init__(self, name: str, config: dict):
        self.name = name
        self.config = config
        self.tools: dict[str, dict] = {}
        self._process: subprocess.Popen | None = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._pending: dict[int, Queue] = {}
        self._reader_thread: threading.Thread | None = None

    def connect(self) -> bool:
        """启动子进程，完成初始化握手，发现工具。"""
        command = self.config.get("command", "")
        args = self.config.get("args", [])
        env = self.config.get("env", {})

        try:
            import os
            full_env = os.environ.copy()
            full_env.update(env)

            self._process = subprocess.Popen(
                [command] + args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=full_env,
                cwd=self.config.get("cwd") or None,
            )
        except Exception as e:
            print(f"  [MCP] 无法启动 {self.name}: {e}", file=sys.stderr)
            return False

        # 启动读线程
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

        # 初始化
        init_result = self._send_request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "codepilot", "version": "1.0.0"},
        })
        if init_result is None:
            return False

        # 发送 initialized 通知
        self._send_notification("initialized", {})

        # 发现工具
        tools_result = self._send_request("tools/list", {})
        if tools_result is None:
            return False

        for tool in tools_result.get("tools", []):
            original_name = tool["name"]
            prefixed_name = f"mcp_{self.name}_{original_name}"
            self.tools[prefixed_name] = {
                "server": self.name,
                "original_name": original_name,
                "schema": tool.get("inputSchema", {}),
                "description": tool.get("description", ""),
            }

        return True

    def _reader_loop(self):
        """后台线程持续读取子进程 stdout，把响应分发到 pending 队列。"""
        try:
            for line in self._process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = parse_message(line)
                except Exception:
                    continue
                if isinstance(msg, (JSONRPCResponse, JSONRPCError)):
                    rid = msg.id
                    if rid is not None and rid in self._pending:
                        self._pending[rid].put(msg)
        except Exception:
            pass

    def _send_request(self, method: str, params: dict) -> dict | None:
        """发送 JSON-RPC 请求并等待响应。"""
        with self._lock:
            self._request_id += 1
            rid = self._request_id

        request = JSONRPCRequest(id=rid, method=method, params=params)
        queue: Queue = Queue()
        self._pending[rid] = queue

        try:
            self._process.stdin.write(format_message(request) + "\n")
            self._process.stdin.flush()
        except Exception:
            self._pending.pop(rid, None)
            return None

        try:
            response = queue.get(timeout=10)
        except Empty:
            self._pending.pop(rid, None)
            return None

        self._pending.pop(rid, None)
        if isinstance(response, JSONRPCError):
            return None
        return response.result

    def _send_notification(self, method: str, params: dict):
        """发送 JSON-RPC 通知（无回复）。"""
        notif = JSONRPCNotification(method=method, params=params)
        try:
            self._process.stdin.write(format_message(notif) + "\n")
            self._process.stdin.flush()
        except Exception:
            pass

    def call_tool(self, prefixed_name: str, args: dict) -> str:
        """代理工具调用到远端 MCP server。"""
        info = self.tools.get(prefixed_name)
        if not info:
            return f"[错误] MCP 工具未找到: {prefixed_name}"
        result = self._send_request("tools/call", {
            "name": info["original_name"],
            "arguments": args,
        })
        if result is None:
            return f"[错误] MCP 工具 {prefixed_name} 调用失败"

        text_parts = []
        for item in result.get("content", []):
            if item.get("type") == "text":
                text_parts.append(item.get("text", ""))
        return "\n".join(text_parts) if text_parts else str(result)

    def disconnect(self):
        """清理子进程。"""
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()


class MCPClientManager:
    """管理多个 MCP server 连接。"""

    def __init__(self, config_path: str = None):
        self.connections: list[MCPClientConnection] = []
        self.tools: dict[str, callable] = {}
        self.tool_definitions: list[dict] = []

        if config_path:
            self._load_config(config_path)

    def _load_config(self, config_path: str):
        """从配置文件加载 server 列表。"""
        import os
        if not os.path.exists(config_path):
            return

        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)

        for server_cfg in config.get("servers", []):
            conn = MCPClientConnection(
                name=server_cfg["name"],
                config=server_cfg,
            )
            self.connections.append(conn)

    def connect_all(self):
        """连接所有配置的 MCP server 并注册工具。"""
        for conn in self.connections:
            print(f"  [MCP] 连接 {conn.name}...", file=sys.stderr)
            try:
                ok = conn.connect()
            except Exception as e:
                print(f"  [MCP] {conn.name} 连接失败: {e}", file=sys.stderr)
                continue

            if not ok:
                print(f"  [MCP] {conn.name} 初始化失败", file=sys.stderr)
                continue

            for prefixed_name, info in conn.tools.items():
                self.tools[prefixed_name] = self._make_proxy(conn, prefixed_name)
                self.tool_definitions.append({
                    "type": "function",
                    "function": {
                        "name": prefixed_name,
                        "description": f"[MCP:{info['server']}] {info['description']}",
                        "parameters": info["schema"],
                    },
                })
            print(f"  [MCP] {conn.name}: {len(conn.tools)} 个工具已注册", file=sys.stderr)

    def call_tool(self, name: str, args: dict) -> str:
        """路由到正确的 MCP 连接。"""
        for conn in self.connections:
            if name in conn.tools:
                return conn.call_tool(name, args)
        return f"[错误] MCP 工具未找到: {name}"

    def _make_proxy(self, conn: MCPClientConnection, name: str):
        def proxy(**kwargs):
            return conn.call_tool(name, kwargs)
        return proxy

    def get_tools_table(self) -> list[dict]:
        """返回所有 MCP 工具及其状态，供 /mcp 命令显示。"""
        rows = []
        for conn in self.connections:
            status = "connected" if conn._process and conn._process.poll() is None else "disconnected"
            for pname, info in conn.tools.items():
                rows.append({
                    "server": conn.name,
                    "tool": info["original_name"],
                    "prefix": pname,
                    "status": status,
                })
        return rows

    def disconnect_all(self):
        """断开所有连接。"""
        for conn in self.connections:
            conn.disconnect()
