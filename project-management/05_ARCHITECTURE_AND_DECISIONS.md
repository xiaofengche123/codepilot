# CodePilot 架构与设计决策记录

更新时间：2026-08-14

## 1. 目标架构

CodePilot 后续按三个平面演进：

```text
Retrieval Plane
  QueryAnalyzer → RetrievalPlan → Recall/Fusion/Rerank → EvidenceBundle

Execution Plane
  AgentStateMachine → TransactionalEdit → TestSelector → Recovery → DiffReview

Evaluation Plane
  Unit/Integration → Frozen Retrieval → External Benchmark → Agent Tasks → Trace Analysis
```

三个平面必须通过结构化数据连接，不能依赖不可审计的提示词文本约定。

## 2. 当前核心调用链

### Agent 工具链

```text
AgentSession.run
→ 模型 bind_tools
→ 工具调用解析
→ 危险等级确认
→ execute_tool(workdir=AgentSession.working_dir)
→ 工具结果 ToolMessage
→ 下一轮或最终回答
```

### RAG 链路

```text
search_semantic
→ hybrid_search
→ retrieve
→ BM25 与 Vector 并行召回
→ Weighted RRF
→ 可选 Cross-Encoder Top-30
→ 最终 Top-K
→ 格式化来源、行号和各路排名
```

### 索引链路

```text
扫描文件
→ 排除缓存/评测/环境目录
→ 识别 code/document
→ AST 或固定窗口切分
→ 本地 MiniLM embedding
→ Chroma upsert
→ 保存 mtime 和 schema version
→ 清理已删除文件残留
```

## 3. 设计决策表

### ADR-001：默认使用 Weighted RRF

- 状态：`ACCEPTED`
- 决策：默认使用 BM25 + Vector + Weighted RRF。
- 原因：内部 Recall@10 为0.8401，平均78.8ms；质量接近 BM25 上限且延迟适合在线工具调用。
- 代价：固定 BM25 高权重对外部自然语言查询泛化不足。
- 后续：通过 M4 自适应路由解决，不直接废弃 RRF。

### ADR-002：Cross-Encoder 保留但默认关闭

- 状态：`ACCEPTED`
- 决策：Rerank 作为显式高质量模式和未来条件式能力。
- 原因：内部 Recall@10 提升到0.9254，但 CPU P95 为6.85秒；两轮单次端到端评测分别表现为 Rerank +5个百分点和 -15个百分点，且不少任务未实际调用语义检索，不能证明稳定任务收益。
- 回退：加载或推理失败返回原始 RRF，并标记原因。

### ADR-003：默认排除文档

- 状态：`ACCEPTED`
- 决策：`include_docs=false`。
- 原因：CodePilot 的主要任务是定位和修改实现，README/说明容易压过源码。
- 例外：查询明确要求使用说明时，由未来 QueryAnalyzer 打开文档。

### ADR-004：模型查询阶段禁止隐式下载

- 状态：`ACCEPTED`
- 决策：Embedding 和 Cross-Encoder 默认 `local_files_only=true`。
- 原因：避免线上查询卡住、不可控网络访问和系统盘缓存。
- 缓存：统一位于 `D:\codepilot\.codepilot\model-cache`。
- 下载：只允许用户显式执行准备命令。

### ADR-005：冻结 test-v1

- 状态：`ACCEPTED`
- 决策：评测后不得修改 v1。
- 原因：避免根据结果调整答案形成泄漏。
- 修订：任何修订创建 v2，并保留迁移记录。

### ADR-006：暂不增加 Multi-Query

- 状态：`REJECTED_FOR_NOW`
- 原因：内部主要问题是排序和 Agent 执行；Rerank 已针对排序，端到端失败主要发生在无编辑和错误编辑。
- 重新评估条件：新独立集证明召回覆盖是主要瓶颈。

### ADR-007：先做事务式编辑，再做更复杂 RAG

- 状态：`ACCEPTED`
- 原因：端到端失败中 Hybrid 10次、Rerank 7次没有修改目标文件；继续提升 Recall 不能直接解决执行问题。

### ADR-008：Agent 使用可回环状态机而非完全固定流水线

- 日期：2026-08-17
- 状态：`ACCEPTED`
- 决策：使用独立、实例级的 `TaskExecutionState` 旁路观察现有 Agent 循环；主链为 INIT → DISCOVER → INSPECT → PLAN → EDIT → VERIFY → REVIEW → COMPLETE，编辑/测试失败进入 RECOVER，RECOVER 可回到 INSPECT、EDIT 或 VERIFY。为兼容真实自由循环和只读任务，允许有客观工具证据的快速入口，例如 INIT → INSPECT/EDIT/VERIFY，以及 read-only 的 DISCOVER/INSPECT → COMPLETE；其他跳转显式拒绝。
- 任务模式：提供 `auto`、`read_only`、`mutation_required`。默认 `auto` 保持调用方兼容，并根据是否出现编辑尝试采用确定性规则，不增加第二个 LLM 分类器。
- 客观证据：状态只由真实工具结果更新；事务编辑解析 JSON，shell 测试解析稳定返回码。mutation 任务必须同时有成功编辑和返回码0的测试证据才能 COMPLETE，模型最终文字本身不是完成证据。
- 隔离：每个 `AgentSession.run` 用户轮次创建新状态；会话历史和模型连接仍可复用，状态计数与转移不跨任务污染。
- 预算：discovery/inspect/edit/verify/recovery 分预算可配置，当前提供统计与耗尽决策接口，总 `max_iterations` 保持硬上限；强制阻止和动作重规划留给后续 STATE 任务。
- 安全：状态仅保存阶段、计数、路径、错误码、返回码和最多500条转移，不保存工具参数、源码、模型上下文或 shell 输出。
- 代价与风险：初版是非侵入式控制基础，尚不能主动阻止重复搜索，也不会自行选择恢复动作；这些能力必须在保持 ToolMessage 配对的前提下增量接入。
- 验证：确定性 fake tool/model 测试覆盖合法/非法转移、隔离、恢复、预算、证据门槛、脱敏、并发和多 tool call 配对；全量151 passed、4 skipped。

### ADR-009：结构图只做检索后扩展

- 状态：`PLANNED`
- 决策：未来 AST 图不替换 BM25/Vector，而是从检索种子做一跳扩展。
- 原因：避免建设昂贵且难验证的完整知识图谱，同时解决跨模块链路问题。

### ADR-010：结构化 Trace 不保存敏感内容

- 状态：`PLANNED`
- 决策：Trace 保存阶段、路径、行号、分数、错误码和摘要，不保存 `.env`、API Key 和无限长源码。

### ADR-011：事务编辑采用单文件原子提交

- 日期：2026-08-14
- 状态：`ACCEPTED`
- 背景：端到端 Agent 失败主要发生在未修改目标文件或错误覆盖文件，完整 `write_file` 缺少局部匹配和并发前置条件。
- 决策：增加独立的 `edit_file_transaction`；所有 edit 基于同一原文预检，使用可选 SHA-256 乐观并发控制、Python AST 校验、同目录临时文件、`fsync`、`os.replace` 和回读验证。
- 边界：一次事务只修改一个现有 UTF-8 文件；多文件原子事务不在本阶段范围内。
- 安全：真实路径必须位于系统注入 workdir；拒绝符号链接逃逸、二进制、非 UTF-8 与超大文件；Agent 参数不能覆盖 workdir。
- 并发：同一进程按路径锁串行；写入前再次比较原始字节以发现外部修改。原子替换后的极小竞态窗口无法提供跨进程强事务保证，此限制必须如实说明。
- 回退：任何预检失败不落盘；临时写入/替换失败保留原文件并清理临时文件；回读验证失败尝试恢复原始字节并报告 `rolled_back`。
- 选择理由：比让 Agent 生成整文件更可验证，同时避免多文件锁顺序、日志和恢复协议尚未成熟时过早引入分布式事务式复杂度。
- 验证：新增路径、匹配、SHA、重叠、并发、BOM/CRLF、语法、dry-run、写入失败和回滚测试；本地全量116 passed、4 skipped。

### ADR-012：Shell 超时以完整进程树为隔离边界

- 日期：2026-08-17
- 状态：`ACCEPTED`
- 背景：A16-Hybrid 中 `subprocess.run(..., shell=True, timeout=30)` 只结束外层 shell，遗留 pip 子进程持有输出管道和 Chroma 文件，最终触发900秒 worker 超时。
- 决策：`run_shell` 与付费评测 runner 都在独立进程组中启动命令；超时后 Windows 使用 `taskkill /T /F`，POSIX 使用 process-group signal，随后有界等待和单进程兜底。
- 评测隔离：未来 Agent worker 把项目 venv 放在 PATH 首位，设置 `VIRTUAL_ENV`，并启用 PIP/Hugging Face 离线模式；正式 API 调用不受影响。
- 代价与风险：依赖操作系统进程组语义；Windows `taskkill` 和 POSIX 分支均需 CI/平台测试，强杀无法保证第三方程序自身事务回滚。
- 验证：新增 runner timeout、synthetic failure、清理重试、worker 环境和 `run_shell` timeout 测试；A16 原始失败保留，不通过重跑隐藏事故。

## 4. 复杂度准入标准

新增复杂机制必须回答：

1. 它解决哪个已测量的问题？
2. 为什么现有机制不能解决？
3. 输入、状态和输出是什么？
4. 失败时如何处理？
5. 是否有回退或回滚？
6. 如何单元测试？
7. 如何端到端验证？
8. 它带来多少延迟、内存和维护成本？
9. 面试时能否用真实数据解释取舍？

无法回答这些问题的功能保持 `IDEA`，不得直接进入主线。

## 5. 新决策模板

```markdown
### ADR-XXX：决策标题

- 日期：YYYY-MM-DD
- 状态：PROPOSED / ACCEPTED / REJECTED / SUPERSEDED
- 背景：
- 决策：
- 备选方案：
- 选择理由：
- 代价与风险：
- 回退策略：
- 验证方式：
- 替代的旧决策：
```
