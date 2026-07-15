"""
码搭 CodePilot · CLI 入口

用法:
  python main.py                        # 交互模式
  python main.py "帮我看看这个项目"       # 单次模式
  python main.py -d /d/myproject         # 指定工作目录
"""

import sys
import os
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.live import Live
from rich.spinner import Spinner

console = Console()

BANNER = """
╔══════════════════════════════════════╗
║        🧠 码搭 CodePilot             ║
║   智能编程助手 · 多模型 Agent CLI      ║
╚══════════════════════════════════════╝
"""


def main():
    # 解析参数
    working_dir = os.getcwd()
    args = sys.argv[1:]

    if "-d" in args:
        idx = args.index("-d")
        if idx + 1 < len(args):
            working_dir = args[idx + 1]
            args.pop(idx)
            args.pop(idx)

    user_input = " ".join(args) if args else None

    # 显示横幅
    console.print(BANNER, style="bold cyan")
    console.print(f"  工作目录: {working_dir}\n", style="dim")

    if user_input:
        _run_once(user_input, working_dir)
    else:
        _run_interactive(working_dir)


def _run_once(user_input: str, working_dir: str):
    """单次模式：一个问题，一个回答"""
    from agent import run as agent_run

    console.print(f"  [bold]你:[/bold] {user_input}\n")

    tool_log = []

    def on_tool(tool_name, args, result):
        tool_log.append((tool_name, args, result))
        arg_str = ", ".join(f"{k}={v}" for k, v in list(args.items())[:2])
        console.print(f"  [dim]🔧 {tool_name}({arg_str})[/dim]")

    with console.status("[cyan]思考中...[/cyan]", spinner="dots"):
        try:
            answer = agent_run(user_input, working_dir, on_tool_call=on_tool)
        except RuntimeError as e:
            console.print(f"\n  [red]启动失败:[/red] {e}")
            console.print("  [dim]请检查 .env 文件中的 API Key 配置[/dim]")
            return

    console.print()
    console.print(Panel(Markdown(answer), title="码搭", border_style="cyan"))


def _run_interactive(working_dir: str):
    """交互模式：持续对话，直到用户输入 exit"""
    from agent import AgentSession

    session = AgentSession(working_dir=working_dir)
    console.print("  [dim]输入 exit 退出, /model /git /index /clear /history /mcp /dir[/dim]\n")

    while True:
        try:
            user_input = console.input("  [bold cyan]你:[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n  再见！")
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit", "q"):
            console.print("  再见！")
            break

        # 斜杠命令分发
        if user_input.startswith("/"):
            _handle_slash(user_input, session, working_dir)
            continue

        tool_log = []

        def on_tool(tool_name, args, result):
            tool_log.append((tool_name, args, result))
            arg_str = ", ".join(f"{k}={v}" for k, v in list(args.items())[:2])
            console.print(f"  [dim]🔧 {tool_name}({arg_str})[/dim]")

        with console.status("[cyan]思考中...[/cyan]", spinner="dots"):
            try:
                answer = session.run(user_input, on_tool_call=on_tool)
            except RuntimeError as e:
                console.print(f"\n  [red]启动失败:[/red] {e}")
                console.print("  [dim]请检查 .env 文件中的 API Key 配置[/dim]")
                return

        console.print()
        console.print(Panel(Markdown(answer), title="码搭", border_style="cyan"))
        console.print()


def _handle_slash(user_input: str, session, working_dir: str):
    """处理 / 开头的斜杠命令。返回 True 表示已处理。"""
    from model_router import switch_model, get_current, list_models
    from memory import clear_history, get_summary
    from rich.table import Table

    parts = user_input.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "/model":
        if not arg:
            current = get_current()
            models = list_models()
            table = Table(title="可用模型", border_style="cyan")
            table.add_column("状态", style="bold")
            table.add_column("模型名")
            table.add_column("供应商")
            table.add_column("成本档位")
            for m in models:
                marker = "  *" if m["current"] else ""
                status = "[green]可用[/green]" if m["available"] else "[yellow]需 Key[/yellow]"
                table.add_row(f"{status}{marker}", m["name"], m["provider"], m["cost_tier"])
            console.print(table)
            console.print("  [dim]使用 /model <名称> 切换模型[/dim]")
        elif arg.lower() == "list":
            models = list_models()
            table = Table(title="可用模型", border_style="cyan")
            table.add_column("状态", style="bold")
            table.add_column("模型名")
            table.add_column("供应商")
            table.add_column("成本档位")
            for m in models:
                marker = "  *" if m["current"] else ""
                status = "[green]可用[/green]" if m["available"] else "[yellow]需 Key[/yellow]"
                table.add_row(f"{status}{marker}", m["name"], m["provider"], m["cost_tier"])
            console.print(table)
            console.print("  [dim]使用 /model <名称> 切换模型[/dim]")
        else:
            model_name = arg.strip()
            if switch_model(model_name):
                console.print(f"  [green]已切换到: {model_name}[/green]")
            else:
                console.print(f"  [red]切换失败: {model_name} 不可用或不存在[/red]")
                console.print("  [dim]输入 /model list 查看可用模型[/dim]")

    elif cmd == "/clear":
        clear_history(working_dir)
        console.print("  [green]对话历史已清除[/green]")

    elif cmd == "/history":
        questions = get_summary(working_dir)
        if not questions:
            console.print("  [dim]暂无对话历史[/dim]")
        else:
            console.print(f"  [bold]历史提问（最近 {len(questions)} 条）:[/bold]")
            for i, q in enumerate(questions, 1):
                console.print(f"  [dim]{i}.[/dim] {q}")

    elif cmd == "/index":
        force = arg.strip() == "--force"
        from rag.indexer import index_project as do_index
        wd = session.working_dir
        console.print("  [dim]正在索引项目...[/dim]")
        try:
            result = do_index(wd, force=force)
            console.print(f"  [green]{result}[/green]")
        except Exception as e:
            console.print(f"  [red]索引失败: {e}[/red]")

    elif cmd == "/git":
        from tools.git_tools import git_status, git_branch
        import os
        wd = session.working_dir
        try:
            status_out = git_status(wd)
            branch_out = git_branch(wd)
            console.print(f"  [bold]分支:[/bold]")
            for line in branch_out.split("\n"):
                console.print(f"  [dim]{line}[/dim]")
            console.print(f"  [bold]状态:[/bold]")
            for line in status_out.split("\n"):
                console.print(f"  [dim]{line}[/dim]")
        except Exception as e:
            console.print(f"  [red]{e}[/red]")

    elif cmd == "/mcp":
        from mcp.client import MCPClientManager
        from rich.table import Table
        try:
            mgr = MCPClientManager("mcp_servers.json")
            table = Table(title="MCP 服务器", border_style="cyan")
            table.add_column("服务器")
            table.add_column("工具名")
            table.add_column("前缀")
            table.add_column("状态")
            for conn in mgr.connections:
                status = "connected" if conn._process and conn._process.poll() is None else "disconnected"
                for pname, info in conn.tools.items():
                    table.add_row(conn.name, info["original_name"], pname, status)
                if not conn.tools:
                    table.add_row(conn.name, "-", "-", status)
            if not mgr.connections:
                console.print("  [dim]未配置 MCP 服务器。在 mcp_servers.json 中添加服务器配置。[/dim]")
            else:
                console.print(table)
        except FileNotFoundError:
            console.print("  [dim]mcp_servers.json 不存在，未连接任何 MCP 服务器[/dim]")
        except Exception as e:
            console.print(f"  [red]MCP 错误: {e}[/red]")

    elif cmd == "/dir" and arg:
        new_dir = arg.strip()
        if Path(new_dir).exists():
            session.working_dir = new_dir
            console.print(f"  [dim]工作目录已切换为: {new_dir}[/dim]")
        else:
            console.print(f"  [red]目录不存在: {new_dir}[/red]")

    else:
        console.print(f"  [yellow]未知命令: {cmd}[/yellow]")
        console.print("  [dim]可用: /model, /git, /index, /clear, /history, /mcp, /dir[/dim]")


if __name__ == "__main__":
    main()
