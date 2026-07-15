# 码搭 CodePilot

[![tests](https://github.com/xiaofengche123/codepilot/actions/workflows/test.yml/badge.svg)](https://github.com/xiaofengche123/codepilot/actions/workflows/test.yml)

智能编程助手 Agent CLI —— 仿 Claude Code 的本地 AI 编程助手。基于 ReAct 模式，支持自然语言驱动的代码浏览、搜索、修改、Git 操作和命令执行。

## 特性

- **多模型路由** — DeepSeek / Claude / OpenAI 自动检测可用性，手动 `/model` 热切换
- **15 个内置工具** — 文件读写、代码搜索、Shell 执行、Git 操作、Web 搜索/抓取、语义搜索
- **ReAct Agent** — 推理-行动-观察循环，最多 10 轮迭代，工具调用实时可见
- **MCP 协议** — 完整 Server（供 Claude Desktop 调用）+ Client（消费外部 MCP Server 工具）
- **RAG 语义搜索** — ChromaDB 向量存储，自然语言搜索代码功能，无需精确关键词
- **对话记忆** — 按项目持久化对话历史，支持上下文裁剪
- **流式输出** — 实时显示 AI 回复，工具调用过程透明

## 快速开始

### 安装

```bash
git clone https://github.com/xiaofengche123/codepilot.git
cd codepilot
python install.py
```

### 配置

复制 `.env.example` 为 `.env`，填入至少一个 API Key：

```bash
# DeepSeek（推荐，免费额度）
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

### 启动

```bash
python main.py              # 交互模式
python main.py "帮我看看这个项目"  # 单次模式
python main.py -d /path/to/project  # 指定工作目录
```

## 交互命令

| 命令 | 说明 |
|------|------|
| `/model` | 显示可用模型列表 |
| `/model <name>` | 切换模型（如 `/model deepseek-chat`） |
| `/git` | 显示 Git 仓库状态和分支 |
| `/index` | 索引当前项目代码（为语义搜索准备） |
| `/index --force` | 强制重建索引 |
| `/clear` | 清除当前项目对话历史 |
| `/history` | 查看历史提问 |
| `/mcp` | 查看 MCP 服务器连接状态 |
| `/dir <path>` | 切换工作目录 |
| `exit` | 退出 |

## 工具列表

| 分类 | 工具 | 说明 |
|------|------|------|
| 核心 | `read_file` | 读取文件内容 |
| 核心 | `write_file` | 写入/覆盖文件 |
| 核心 | `list_files` | 列出目录内容 |
| 核心 | `search_code` | 正则搜索代码 |
| 核心 | `run_shell` | 执行终端命令（需确认） |
| Git | `git_status` | 查看工作区状态 |
| Git | `git_diff` | 查看差异 |
| Git | `git_log` | 查看提交日志 |
| Git | `git_branch` | 列出分支 |
| Git | `git_add` | 暂存文件（需确认） |
| Git | `git_commit` | 提交（需确认） |
| Web | `web_search` | DuckDuckGo 搜索 |
| Web | `web_fetch` | 抓取网页内容 |
| RAG | `index_project` | 向量化索引项目 |
| RAG | `search_semantic` | 语义搜索代码 |

## MCP 集成

### 作为 MCP Server

在 Claude Desktop 配置中添加，即可在 Claude Desktop 中使用 CodePilot 的 15 个工具：

```json
{
  "mcpServers": {
    "codepilot": {
      "command": "python",
      "args": ["-m", "mcp.server"],
      "cwd": "D:/codepilot"
    }
  }
}
```

### 作为 MCP Client

编辑 `mcp_servers.json` 配置要连接的外部 MCP Server：

```json
{
  "servers": [
    {
      "name": "filesystem",
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]
    }
  ]
}
```

启动 CodePilot 后会自动连接并发现工具，工具名以 `mcp_{server}_{tool}` 格式注册。

## RAG 语义搜索

1. 进入项目目录，运行 `/index` 索引代码
2. 之后 Agent 可调用 `search_semantic` 进行自然语言搜索

```
你: 找到处理用户登录校验的代码
Agent: [调用 search_semantic("用户登录校验逻辑")]
       → auth/login.py:32  def validate_login(username, password)
       → middleware/auth.py:18  class AuthMiddleware
```

- Python 文件按函数/类 AST 精确切分
- 其他语言按 30 行固定窗口切分
- 向量模型：`all-MiniLM-L6-v2`（本地运行）
- 增量索引：按文件 mtime 跳过未修改文件

## 项目结构

```
codepilot/
├── main.py              # CLI 入口
├── agent.py             # Agent ReAct 循环
├── model_router.py      # 多模型路由
├── memory.py            # 对话记忆
├── context_mgr.py       # 上下文管理
├── config.py            # 配置加载器
├── install.py           # 一键安装
├── requirements.txt     # Python 依赖
├── mcp_servers.json     # MCP Client 配置
├── .env.example         # API Key 模板
├── config/
│   └── settings.yaml    # 配置文件
├── tools/
│   ├── __init__.py      # 工具注册中心
│   ├── core_tools.py    # 核心工具 (5)
│   ├── git_tools.py     # Git 工具 (6)
│   ├── web_tools.py     # Web 工具 (2)
│   └── rag_tools.py     # RAG 工具 (2)
├── mcp/
│   ├── protocol.py      # JSON-RPC 2.0 + MCP 消息
│   ├── server.py        # MCP Server
│   └── client.py        # MCP Client
└── rag/
    ├── indexer.py       # 代码索引器
    └── retriever.py     # 混合检索引擎
```

## 技术栈

- **LLM**: DeepSeek / Claude / OpenAI（LangChain 统一接口）
- **Agent**: ReAct 模式，Function Calling
- **向量存储**: ChromaDB + SentenceTransformers
- **MCP**: JSON-RPC 2.0 over stdio
- **Web**: DuckDuckGo Search + httpx + BeautifulSoup
- **CLI**: Rich
- **配置**: YAML + dotenv

## 测试

```bash
pip install pytest
pytest tests/ -v
```

共 31 个测试用例，覆盖工具系统、记忆模块、配置管理和上下文裁剪。

## 许可证

MIT
