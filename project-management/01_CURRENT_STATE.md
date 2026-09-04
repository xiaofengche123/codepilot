# CodePilot 当前状态

更新时间：2026-09-02

## 1. 项目定位

CodePilot 是一个本地代码智能体与 Agent 工程平台，当前已覆盖：

- ReAct 风格工具调用循环。
- 声明式工具注册与危险等级管理。
- CLI 与 FastAPI 双运行模式。
- MCP Client/Server 双端。
- Git Worktree 任务隔离。
- BM25 + Vector + Weighted RRF + Cross-Encoder Rerank。
- 对话记忆、上下文裁剪、流式输出和多模型路由。
- 单元测试、CI、Dashboard、任务事件与 metrics 端点。
- 内部冻结集、外部 CodeSearchNet 和端到端 Agent 任务评测。

当前项目不再处于“缺少功能”的阶段，而处于“提高可靠性和建立闭环”的阶段。

## 2. 已实现能力

### 2.1 Agent 与工具系统

- Agent 主循环支持模型工具调用。
- 目前注册16个文件、事务编辑、Git、Web、RAG工具。
- 工具通过函数、JSON Schema 和风险等级注册。
- Agent 工作目录由系统注入，LLM 不能覆盖 `workdir`。
- 危险工具支持 CLI 人工确认、API 默认拒绝、MCP 配置禁用。
- 工具调用支持流式优先、失败后 invoke 回退。
- `edit_file_transaction` 支持 SHA 乐观并发控制、全量匹配预检、Python AST 校验、同目录原子替换、回读验证和失败恢复。
- 事务编辑限制在系统注入的 workdir 内，保留 UTF-8 BOM 与 CRLF/LF，并按文件路径串行化进程内并发。

### 2.2 MCP

- MCP Server 使用 JSON-RPC 2.0 和 stdio 暴露本地工具。
- MCP Client 以子进程连接外部 Server。
- 外部工具使用 `mcp_{server}_{tool}` 命名空间。
- 连接失败不阻塞 Agent 启动。
- 已有 stdio 全链路测试。

### 2.3 服务与隔离

- FastAPI 提供任务 API。
- 同步 Agent 循环在线程池中执行。
- Semaphore 控制并发数量。
- Git Worktree 为任务提供分支和目录隔离。
- TaskEvent 使用单调 sequence 支持增量进度拉取。
- 模型按任务创建独立实例，减少并发路由串扰。

### 2.4 RAG 索引

- Python 使用 AST 按顶层函数和类切分。
- 其他文件使用固定行窗口切分。
- Chunk ID 和 metadata 使用 POSIX 相对路径。
- ChromaDB 持久化向量索引。
- 通过文件 mtime 实现增量索引。
- 删除源文件后清理 Chroma 残留。
- 索引 Schema v2 增加 `content_type=code|document`。
- 旧 Schema 会触发完整重建，避免默认 code 过滤返回空结果。
- `.rag-eval`、`.codepilot`、本地非源码资料、`venv.broken-*` 等目录不会进入索引。

### 2.5 RAG 查询

- BM25 词法召回。
- `all-MiniLM-L6-v2` 向量召回。
- Weighted Reciprocal Rank Fusion。
- `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` 精排。
- Cross-Encoder 对 `(query, chunk)` 成对打分。
- 精排前从 RRF 取 Top-30，最终截断为请求 Top-K。
- 保存 `vector_rank`、`bm25_rank`、`rrf_rank`、`rerank_score`。
- 模型失败时回退 RRF，并写入 `rerank_fallback`。
- 模型懒加载有加载锁，`predict` 有串行锁。
- 在线查询 `local_files_only=true`，不会隐式下载。
- 模型缓存统一位于 D 盘项目目录。

### 2.6 当前产品默认值

```yaml
rag:
  include_docs: false
  candidate_multiplier: 3
  rrf_k: 10
  vector_weight: 0.25
  bm25_weight: 2.0
  reranker:
    enabled: false
    candidate_count: 30
    batch_size: 16
    max_length: 512
    local_files_only: true
    fallback_on_error: true
```

默认关闭 Rerank 是经过评测后的产品决策，不是功能未完成。

## 3. 已完成评测

### 3.1 内部冻结集

- 文件：`.rag-eval/codepilot-test-v1.json`
- 数量：150条。
- 分类：identifier、natural_language、bug_symptom、cross_module、mixed_language，各30条。
- required 标签：209个。
- supporting 标签：106个。
- Dataset SHA-256：`c74dde28140e5d03bc2d0a5ffef323777b3174bf2a5111569c81cbc86600bd55`
- 正式评测只运行一次，之后 SHA 校验保持一致。

正式结果：

| 方法 | Required Recall@10 | MRR@10 | nDCG@10 | Avg | P95 | Max |
|---|---:|---:|---:|---:|---:|---:|
| BM25 | 0.8390 | 0.6794 | 0.6550 | 41.5ms | 71.1ms | 78.5ms |
| Vector | 0.6112 | 0.4684 | 0.4584 | 45.5ms | 83.0ms | 111.2ms |
| Weighted RRF | 0.8401 | 0.6818 | 0.6578 | 78.8ms | 136.9ms | 201.9ms |
| Rerank | 0.9254 | 0.7810 | 0.7653 | 3421.8ms | 6854.7ms | 7306.8ms |

Rerank 相对 Hybrid 的 Required Recall@10 平均提升0.0853，95% bootstrap CI 为 `[0.0333, 0.1387]`。但有6道题发生 Recall@10 退化，不能把精排描述为对所有查询稳定提升。

### 3.2 CodeSearchNet 外部 judged-pool

- 官方查询：99个。
- 官方唯一 URL：2874个。
- 成功恢复：2752个，覆盖率95.76%。
- 这是人工判断 URL 并集，不是约200万函数的全语料排行榜成绩。

| 方法 | nDCG@10 | nDCG@100 |
|---|---:|---:|
| BM25 | 0.4264 | 0.6338 |
| Vector | 0.5685 | 0.7408 |
| Weighted RRF | 0.4421 | 0.6496 |
| Rerank Top-30 | 0.5263 | 0.6359 |

外部结果说明当前 `bm25_weight=2.0`、`vector_weight=0.25` 带有 CodePilot 内部代码库偏好，不是通用最优参数。

### 3.3 端到端 Agent 初始基线（agent-v1）

- 任务：20个缺陷注入任务。
- 条件：Hybrid 与 Rerank 各20次。
- 模型：`deepseek-chat`。
- Agent 最大迭代：10步。
- 原始严格结果：Hybrid 7/20，Rerank 8/20。
- 剔除 A03 已确认的 harness 假阴性后：Hybrid 8/20，Rerank 9/20。

| 条件 | 校正成功率 | Agent Avg | Agent P95 | 隔离进程 Avg | 隔离进程 P95 |
|---|---:|---:|---:|---:|---:|
| Hybrid | 40% | 22.9s | 29.9s | 62.6s | 75.0s |
| Rerank | 45% | 30.7s | 42.2s | 70.5s | 107.4s |

成对结果：

- 两边都成功：5题。
- 仅 Hybrid：3题。
- 仅 Rerank：4题。
- 两边都失败：8题。

当前端到端数据不能证明 Rerank 稳定提高任务成功率，反而明确证明它增加延迟。

### 3.4 事务式编辑复测（agent-v2-transactional）

- 日期：2026-08-17。
- 冻结任务：沿用20个 v1 缺陷注入任务，SHA-256 `71caa70e7b441380c79745c701bb02a77f8b4d0efcfb2d892b3a91f053d7ac09`，运行前后未变化。
- 被测代码：提交 `daee3cc1f4c7c8226d173fd7b295c32d1b2d5c1f`。
- 完整性：40/40 报告齐全；A16-Hybrid 为保留在正式统计中的 worker 超时失败，未选择性重跑。

| 条件 | 严格成功 | 目标文件已改 | 目标已改且测试通过 | 实际调用语义检索的任务 | 可比 Agent Avg | P95 | 正常最大值 | 运行异常最大值 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Hybrid | 11/20（55%） | 14/20（70%） | 14/20（70%） | 11/20 | 26.2s | 42.6s | 42.6s | 900.1s |
| Rerank | 8/20（40%） | 12/20（60%） | 10/20（50%） | 14/20 | 31.0s | 43.5s | 72.4s | 72.4s |

严格成对结果：两边都成功5题、仅 Hybrid 6题、仅 Rerank 3题、两边都失败6题。所有发生编辑的任务都使用 `edit_file_transaction`：Hybrid 14/14、Rerank 12/12，旧 `write_file` 为0；32次事务编辑调用全部返回成功，没有前置条件、写入或回滚错误。这证明新工具在被 Agent 选中时可稳定执行，但目标文件修改率仍未达到 M1 的80%验收线，瓶颈已经转移到“是否及时进入编辑/测试阶段”。

结果必须带以下限制解释：Hybrid 只有11题、Rerank只有14题真正调用语义检索；每个条件只有一次非确定性 Agent 轨迹；A16-Hybrid 因 Agent 用系统 Python 尝试安装依赖而发生900秒基础设施超时。因此55%与40%是端到端观测，不能作为 Rerank 造成15个百分点下降的因果或统计显著结论。A07/A10/A18 存在功能等价但未逐字恢复 Oracle 的修复，严格成功率可能偏保守，但冻结 v1 不做事后改标。

## 4. 当前测试状态

- 全量 pytest：574 passed，4 skipped（包含 ROUTE、运行时接线及 GRAPH-001～004 回归）。
- `git diff --check`：通过，存在 Windows LF/CRLF 提示但没有空白错误。
- 冻结集 SHA：复核一致。
- Agent v1 与 v2 原始结果：各40个 JSON 文件齐全。
- `install.py`：未修改。

## 5. 当前最重要的问题

端到端评测显示，主要失败不是完全检索不到代码，而是：

- Agent 没有修改目标文件。
- 已经读到实现和测试，却在步数耗尽前没有编辑。
- 修改了目标文件，但没有准确恢复缺陷。
- 测试失败后缺少结构化恢复策略。
- 少量任务产生范围外文件修改。

因此当前优先级不是继续增加 RAG 范式，而是提高 Agent 执行可靠性。

## 6. 当前不应做的事情

- 不默认开启 CPU Rerank。
- 不根据 test-v1 结果修改冻结答案。
- 不把 CodeSearchNet judged-pool 描述成官方全库排行榜。
- 不立即增加 Multi-Query。
- 不立即建设完整 Knowledge Graph RAG。
- 不为改善表面指标而修改任务 Oracle 或放宽成功条件。
- 不清理当前未提交工作区。
- 不触碰用户已有的 `install.py`。

## 7. 当前推荐的下一项实现

M2 STATE-001～008、M3 TRACE-001～006、`ROUTE-001`～`ROUTE-008`、`ROUTE-RUNTIME-001`、`GRAPH-001`～`GRAPH-007` 与 `MODEL-001`～`MODEL-007` 已完成，M6标记`DONE`。M5仍为`DONE_WITH_GAP`；图路径未接入Retriever。M7的`M7-001/002/003`已完成，`M7-004`按用户决定取消独立人工审计硬要求并以`DONE_WITH_GAP`关闭：千问三轮120/120项，Hybrid pass@1/pass@3为21.67%/30%，Rerank为16.67%/35%；三个外部仓库12题冻结集上两模式Recall@10均为1.0，Rerank MRR@10点估计低0.075且CI跨0。最终报告已完成，25%盲审包保留为可选后续；不能宣称结果已通过独立人工标签验证。
