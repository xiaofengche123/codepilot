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
    from agent import run as agent_run

    console.print("  [dim]输入 exit 退出, /dir <路径> 切换工作目录[/dim]\n")

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

        if user_input.startswith("/dir "):
            new_dir = user_input[5:].strip()
            if Path(new_dir).exists():
                working_dir = new_dir
                console.print(f"  [dim]工作目录已切换为: {working_dir}[/dim]")
            else:
                console.print(f"  [red]目录不存在: {new_dir}[/red]")
            continue

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
        console.print()


if __name__ == "__main__":
    main()
