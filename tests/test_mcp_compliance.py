import json
import sys

from mcp.client import MCPClientConnection, MCPClientManager
from mcp.protocol import JSONRPCNotification, JSONRPCRequest, JSONRPCError
import mcp.server as mcp_server_module
from mcp.server import MCPServer


def test_server_accepts_standard_initialized_notification():
    server = MCPServer()
    notification = JSONRPCNotification(
        method="notifications/initialized",
        params={},
    )
    assert server._dispatch(notification) is None


def test_server_does_not_reply_to_unknown_notification():
    server = MCPServer()
    notification = JSONRPCNotification(method="notifications/custom", params={})
    assert server._dispatch(notification) is None


def test_server_returns_protocol_error_for_unknown_tool():
    server = MCPServer()
    request = JSONRPCRequest(
        id=7,
        method="tools/call",
        params={"name": "missing", "arguments": {}},
    )
    response = server._dispatch(request)
    assert isinstance(response, JSONRPCError)
    assert response.error["code"] == -32602


def test_tools_list_schema_and_dangerous_call_error_shape():
    server = MCPServer()
    listed = server._dispatch(JSONRPCRequest(id=1, method="tools/list", params={}))
    assert len(listed.result["tools"]) == 16
    assert all("inputSchema" in tool for tool in listed.result["tools"])

    rejected = server._dispatch(JSONRPCRequest(
        id=2, method="tools/call",
        params={"name": "run_shell", "arguments": {"command": "pytest -q"}},
    ))
    assert rejected.result["isError"] is True
    assert rejected.result["content"][0]["type"] == "text"


def test_structured_edit_json_is_not_double_encoded(monkeypatch):
    payload = json.dumps({"success": True, "error_code": None})
    monkeypatch.setattr(mcp_server_module, "execute_tool", lambda *args, **kwargs: payload)
    response = MCPServer()._dispatch(JSONRPCRequest(
        id=3, method="tools/call",
        params={"name": "edit_file_transaction", "arguments": {
            "path": "x.py", "edits": [{"old_text": "a", "new_text": "b"}],
        }},
    ))
    text = response.result["content"][0]["text"]
    assert text == payload
    assert json.loads(text)["success"] is True


def test_allowed_mcp_shell_preserves_returncode_marker(monkeypatch):
    original_get = mcp_server_module.config.get
    monkeypatch.setattr(
        mcp_server_module.config, "get",
        lambda key, default=None: True if key == "mcp.allow_dangerous" else original_get(key, default),
    )
    monkeypatch.setattr(
        mcp_server_module, "execute_tool", lambda *args, **kwargs: "ok\n[returncode] 0"
    )
    response = MCPServer()._dispatch(JSONRPCRequest(
        id=4, method="tools/call",
        params={"name": "run_shell", "arguments": {"command": "pytest -q"}},
    ))
    assert response.result["content"][0]["text"].endswith("[returncode] 0")


def test_failed_connection_cleans_up_subprocess():
    connection = MCPClientConnection("broken", {
        "command": sys.executable,
        "args": ["-c", "raise SystemExit(0)"],
        "timeout": 0.2,
    })
    assert connection.connect() is False
    assert connection._process is None


def test_client_uses_standard_initialized_notification(tmp_path):
    server_script = tmp_path / "standard_server.py"
    server_script.write_text(
        """
import json
import sys

initialized = False
for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        print(json.dumps({
            "jsonrpc": "2.0",
            "id": msg["id"],
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "stub", "version": "1.0"},
            },
        }), flush=True)
    elif method == "notifications/initialized":
        initialized = True
    elif method == "tools/list":
        tools = [{"name": "echo", "description": "echo", "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        }}] if initialized else []
        print(json.dumps({
            "jsonrpc": "2.0", "id": msg["id"], "result": {"tools": tools}
        }), flush=True)
    elif method == "tools/call":
        text = msg["params"]["arguments"]["text"]
        print(json.dumps({
            "jsonrpc": "2.0",
            "id": msg["id"],
            "result": {"content": [{"type": "text", "text": text}]},
        }), flush=True)
""",
        encoding="utf-8",
    )
    config_path = tmp_path / "mcp_servers.json"
    config_path.write_text(json.dumps({"servers": [{
        "name": "standard",
        "command": sys.executable,
        "args": [str(server_script)],
        "timeout": 2,
    }]}), encoding="utf-8")

    manager = MCPClientManager(str(config_path))
    manager.connect_all()
    try:
        assert "mcp_standard_echo" in manager.tools
        assert manager.call_tool("mcp_standard_echo", {"text": "ok"}) == "ok"
    finally:
        manager.disconnect_all()
