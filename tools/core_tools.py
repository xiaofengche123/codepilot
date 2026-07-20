"""
码搭 CodePilot · 核心工具
read_file / write_file / list_files / search_code / run_shell
"""

import os
import re
import subprocess
from pathlib import Path
from typing import Optional


def _resolve(path: str, workdir: Optional[str] = None) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    return Path(workdir or os.getcwd()) / p


def read_file(path: str, start_line: int = 0, end_line: Optional[int] = None, workdir: Optional[str] = None) -> str:
    """
    读取文件内容。参数 path: 文件路径（相对于工作目录）、start_line: 起始行号(0表示从开头)、end_line: 结束行号(省略表示读到末尾)。返回文件内容字符串。
    """
    filepath = _resolve(path, workdir)
    if not filepath.exists():
        return f"[错误] 文件不存在: {path}"
    if filepath.is_dir():
        return f"[错误] 路径是目录而非文件: {path}"

    try:
        lines = filepath.read_text(encoding="utf-8").split("\n")
    except UnicodeDecodeError:
        return f"[错误] 文件 {path} 不是文本文件，无法读取"

    if end_line is None:
        end_line = len(lines)
    end_line = min(end_line, len(lines))
    selected = lines[start_line:end_line]

    result = []
    for i, line in enumerate(selected, start=start_line + 1):
        result.append(f"{i:4d} | {line}")
    return "\n".join(result)


def write_file(path: str, content: str, workdir: Optional[str] = None) -> str:
    """
    写入或覆盖文件。参数 path: 文件路径、content: 要写入的完整内容。返回操作结果。
    """
    filepath = _resolve(path, workdir)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    try:
        filepath.write_text(content, encoding="utf-8")
        return f"[成功] 已写入 {path} ({len(content)} 字符)"
    except Exception as e:
        return f"[错误] 写入失败: {e}"


def list_files(path: str = ".", workdir: Optional[str] = None) -> str:
    """
    列出目录下的文件和子目录。参数 path: 目录路径，默认当前目录。返回文件列表。
    """
    dirpath = _resolve(path, workdir)
    if not dirpath.exists():
        return f"[错误] 目录不存在: {path}"

    items = []
    for item in sorted(dirpath.iterdir()):
        suffix = "/" if item.is_dir() else ""
        items.append(f"  {item.name}{suffix}")

    if not items:
        return f"[空] {path} 下没有文件"
    return "\n".join(items)


def search_code(pattern: str, path: str = ".", workdir: Optional[str] = None) -> str:
    """
    在代码中搜索关键词或正则表达式。参数 pattern: 搜索模式(支持正则)、path: 搜索目录。返回匹配行。
    """
    dirpath = _resolve(path, workdir)
    if not dirpath.exists():
        return f"[错误] 目录不存在: {path}"

    results = []
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"[错误] 正则表达式无效: {e}"

    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode"}
    for filepath in dirpath.rglob("*"):
        if set(filepath.parts) & skip_dirs:
            continue
        if not filepath.is_file():
            continue
        if filepath.suffix not in {
            ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs",
            ".html", ".css", ".vue", ".yaml", ".yml", ".json", ".md", ".txt",
            ".sh", ".sql", ".xml", ".toml", ".cfg", ".ini",
        }:
            continue

        try:
            for i, line in enumerate(filepath.read_text(encoding="utf-8", errors="ignore").split("\n"), 1):
                if regex.search(line):
                    results.append(f"  {filepath.relative_to(dirpath)}:{i}: {line.strip()[:120]}")
                if len(results) >= 50:
                    break
        except Exception:
            continue
        if len(results) >= 50:
            break

    if not results:
        return f"[未找到] 在 {path} 中没有匹配 '{pattern}' 的内容"
    return f"找到 {len(results)} 条结果:\n" + "\n".join(results)


def run_shell(command: str, workdir: Optional[str] = None) -> str:
    """
    执行终端命令（执行前会显示命令并请求确认）。参数 command: 要执行的 shell 命令。返回命令输出。
    危险命令（rm -rf、format、mkfs 等）会被拦截。
    """
    dangerous = [
        "rm -rf", "rm -fr", "rm -r -f", "rm -f -r",
        "mkfs", "format", "dd if=", ":(){", "chmod 777 /",
        "rd /s", "rmdir /s", "del /f", "remove-item",
        "shutdown", "reboot",
    ]
    cmd_lower = command.lower().replace(" ", "")
    for d in dangerous:
        if d.replace(" ", "") in cmd_lower:
            return f"[拦截] 命令 '{command}' 包含危险操作，已阻止执行"

    try:
        from config import config
        _timeout = config.get("tools.shell_timeout", 30)
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_timeout, cwd=workdir or os.getcwd(),
        )
        output = result.stdout
        if result.stderr:
            output += "\n[stderr]\n" + result.stderr
        if not output.strip():
            output = f"[完成] 命令执行成功（无输出），返回码: {result.returncode}"
        _max_chars = config.get("tools.output_max_chars", 4000)
        return output[:_max_chars]
    except subprocess.TimeoutExpired:
        return f"[超时] 命令 '{command}' 执行超过 {_timeout} 秒，已终止"
    except Exception as e:
        return f"[错误] 执行失败: {e}"


CORE_TOOLS = {
    "read_file": read_file,
    "write_file": write_file,
    "list_files": list_files,
    "search_code": search_code,
    "run_shell": run_shell,
}

CORE_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文件内容，支持指定行号范围",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径，相对于工作目录"},
                    "start_line": {"type": "integer", "description": "起始行号，0 表示从第一行开始，默认 0"},
                    "end_line": {"type": "integer", "description": "结束行号，省略表示读到文件末尾"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "写入或覆盖文件，会自动创建父目录",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径，相对于工作目录"},
                    "content": {"type": "string", "description": "要写入的完整文件内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出目录下的所有文件和子目录",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录路径，默认当前目录"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "在代码中搜索关键词或正则表达式",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "搜索关键词或正则表达式"},
                    "path": {"type": "string", "description": "搜索的起始目录，默认当前目录"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "执行终端命令。危险命令会被自动拦截。执行前会向用户请求确认。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的 shell 命令"},
                },
                "required": ["command"],
            },
        },
    },
]

CORE_DANGEROUS_TOOLS = {"run_shell"}
