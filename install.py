"""
码搭 CodePilot · 一键安装脚本

用法: python install.py
"""

import sys
import os
import subprocess
from pathlib import Path


def check_python() -> bool:
    v = sys.version_info
    if v >= (3, 11):
        print(f"  [OK] Python {v.major}.{v.minor}.{v.micro}")
        return True
    print(f"  [FAIL] Python {v.major}.{v.minor}.{v.micro}，需要 3.11+")
    return False


def check_git() -> bool:
    try:
        result = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            print(f"  [OK] {result.stdout.strip()}")
            return True
    except Exception:
        pass
    print("  [WARN] Git 未安装或不在 PATH 中，git 工具将不可用")
    return False


def setup_venv(project_dir: Path) -> Path:
    venv_dir = project_dir / "venv"
    if venv_dir.exists():
        print(f"  [OK] 虚拟环境已存在: {venv_dir}")
    else:
        print(f"  [..] 正在创建虚拟环境...")
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
        print(f"  [OK] 已创建: {venv_dir}")
    return venv_dir


def install_deps(venv_dir: Path):
    pip = venv_dir / "Scripts" / "pip.exe"
    if os.name != "nt":
        pip = venv_dir / "bin" / "pip"

    req = venv_dir.parent / "requirements.txt"
    print(f"  [..] 安装依赖（可能需要几分钟）...")
    result = subprocess.run(
        [str(pip), "install", "-r", str(req)],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode == 0:
        print(f"  [OK] 依赖安装完成")
    else:
        print(f"  [FAIL] 依赖安装失败:\n{result.stderr[-500:]}")


def check_env(project_dir: Path):
    env_path = project_dir / ".env"
    example = project_dir / ".env.example"
    if env_path.exists():
        with open(env_path) as f:
            has_key = any("xxx" not in line and "=" in line and len(line.split("=")[1].strip()) > 5
                          for line in f if not line.startswith("#"))
        if has_key:
            print(f"  [OK] .env 已配置")
        else:
            print(f"  [WARN] .env 存在但 Key 可能未填写，请编辑后使用")
    else:
        print(f"  [WARN] 未找到 .env，请复制 .env.example → .env 并填入 API Key")


def main():
    project_dir = Path(__file__).parent.resolve()
    print(f"\n码搭 CodePilot · 安装程序")
    print(f"项目目录: {project_dir}\n")

    all_ok = True

    # 1. Python 版本
    print("─ 检查环境 ─")
    if not check_python():
        all_ok = False
    check_git()

    if not all_ok:
        print("\n请先解决上述问题后重新运行。")
        sys.exit(1)

    # 2. 虚拟环境
    print("\n─ 设置虚拟环境 ─")
    venv_dir = setup_venv(project_dir)

    # 3. 安装依赖
    print("\n─ 安装依赖 ─")
    install_deps(venv_dir)

    # 4. 配置检查
    print("\n─ 配置检查 ─")
    check_env(project_dir)

    # 完成
    print(f"""
╔══════════════════════════════════════╗
║     安装完成！                       ║
║                                      ║
║   启动:                              ║
║     {venv_dir.name}\\Scripts\\python main.py      ║
║                                      ║
║   或者先激活虚拟环境:                  ║
║     {venv_dir.name}\\Scripts\\activate             ║
║     python main.py                   ║
╚══════════════════════════════════════╝
""")


if __name__ == "__main__":
    main()
