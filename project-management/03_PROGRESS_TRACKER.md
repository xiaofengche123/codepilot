# CodePilot 进度跟踪表

更新时间：2026-08-17

## 1. 使用方法

- 每项任务都有稳定 ID，AI 和用户讨论时应引用任务 ID。
- 一次开发尽量只领取一个主任务和必要的子任务。
- 开工时把状态改为 `IN_PROGRESS`。
- 完成后填写验证命令、结果、相关提交和遗留问题，再改为 `DONE`。
- 如果发现任务定义不合理，先记录原因，不要静默改变验收标准。

## 2. 总览

| 里程碑 | 状态 | 当前结论 |
|---|---|---|
| M0 当前基线固化 | `DONE` | dev 基线、测试矩阵与 Docker 均已验证 |
| M1 事务式代码编辑 | `DONE_WITH_GAP` | 工具与复测完成；事务调用32/32成功，目标修改率70%/60%未达80% |
| M2 Agent 状态机 | `IN_PROGRESS` | STATE-001～004 状态基础完成；强制预算与恢复策略待实施 |
| M3 Trace 与失败分析 | `PLANNED` | 可与状态机同步设计 |
| M4 自适应检索 | `PLANNED` | 不得使用 test-v1 调参 |
| M5 AST 结构图扩展 | `PLANNED` | 只解决跨模块问题 |
| M6 模型服务化 | `PLANNED` | 在自适应 Rerank 后实施 |
| M7 重复和独立评测 | `PLANNED` | 各阶段完成后执行 |

## 3. 已完成任务

### RAG 与索引

- [x] `RAG-001` `DONE`：实现 BM25 与 MiniLM 双路召回。
- [x] `RAG-002` `DONE`：实现 Weighted RRF，保存两路排名和 `rrf_rank`。
- [x] `RAG-003` `DONE`：实现 Cross-Encoder `(query, chunk)` Top-30 精排。
- [x] `RAG-004` `DONE`：Rerank 失败回退 RRF 并标记 `rerank_fallback`。
- [x] `RAG-005` `DONE`：模型懒加载锁和 predict 串行锁。
- [x] `RAG-006` `DONE`：在线查询 `local_files_only=true`。
- [x] `RAG-007` `DONE`：模型缓存统一到 D 盘项目目录。
- [x] `IDX-001` `DONE`：索引 metadata 增加 `content_type`。
- [x] `IDX-002` `DONE`：Schema v2 自动重建旧索引。
- [x] `IDX-003` `DONE`：默认 code 过滤，`include_docs=true` 可包含文档。
- [x] `IDX-004` `DONE`：排除评测、缓存、面试资料和损坏虚拟环境。
- [x] `IDX-005` `DONE`：Windows/POSIX Chunk ID 统一。

### 评测

- [x] `EVAL-001` `DONE`：建立150条五分类内部冻结集。
- [x] `EVAL-002` `DONE`：标注拆分 required/supporting。
- [x] `EVAL-003` `DONE`：生成 manifest、数据 SHA 和语料 SHA。
- [x] `EVAL-004` `DONE`：实现 Recall@5/10、MRR、graded nDCG。
- [x] `EVAL-005` `DONE`：实现分类指标、P95、最大延迟、失败案例和 fallback 率。
- [x] `EVAL-006` `DONE`：实现 bootstrap 置信区间和相对 Hybrid 的成对差异。
- [x] `EVAL-007` `DONE`：完成一次内部冻结集正式评测。
- [x] `EVAL-008` `DONE`：完成 CodeSearchNet 99查询 judged-pool 评测。
- [x] `EVAL-009` `DONE`：建立20个端到端缺陷注入任务。
- [x] `EVAL-010` `DONE`：完成 Hybrid/Rerank 共40次 DeepSeek Agent 调用。
- [x] `EVAL-011` `DONE`：记录 harness 假阴性、范围外修改和协议限制。

### 回归

- [x] `TEST-001` `DONE`：当前 Windows 本地133 passed、4 skipped；提交 `568ff57` 的 CI run `31990221352` 中 Python 3.11、3.12 与 Docker 全部通过。
- [x] `TEST-002` `DONE`：`git diff --check` 通过。
- [x] `TEST-003` `DONE`：冻结集 SHA 评测后复核一致。
- [x] `TEST-004` `DONE`：确认 `install.py` 未被本轮修改。

## 4. 当前进行中

### 基线固化

- [x] `BASE-001` `DONE`：审查当前未提交的 RAG、评测和测试文件。
  - 依赖：无。
  - 验收结果：47个 JSON 可解析；结果文件约1.34MB；密钥扫描、冻结 SHA、全量测试和 diff 检查通过；模型、外部数据和临时目录不提交。
  - 注意：不得 reset 或清理用户工作区。

- [x] `BASE-002` `DONE`：建立项目管理文档中心。
  - 依赖：无。
  - 验收结果：9个 Markdown 文档齐全，内部链接0个缺失，密钥扫描通过。

- [x] `BASE-003` `DONE`：准备当前阶段提交。
  - 依赖：BASE-001、BASE-002。
  - 验收结果：提交 `f80a8fd` 已推送到 `origin/dev`；未提交 `.env`、`install.py`、模型缓存、外部数据或临时目录。

- [x] `BASE-004` `DONE`：验证 GitHub Actions。
  - 依赖：BASE-003。
  - 验收结果：补充 `dev` push/PR 触发；修复 Linux 下 Windows 路径测试；运行 `31776231906` 的 Python 3.11、3.12 和 Docker 三个作业全部通过。

## 5. 下一阶段：事务式编辑

- [x] `EDIT-001` `DONE`：设计编辑请求和结果数据结构。
  - 输出：`EditOperation`、`EditRequest`、`EditResult`。
  - 验收结果：结构已落地，结果包含前后 SHA、替换数、diff、回滚状态、错误码和消息。

- [x] `EDIT-002` `DONE`：实现路径与文件安全检查。
  - 依赖：EDIT-001。
  - 验收结果：拒绝 workdir 外路径、符号链接逃逸、目录、二进制、非 UTF-8 和超大文件。

- [x] `EDIT-003` `DONE`：实现匹配和事务预检查。
  - 依赖：EDIT-001。
  - 验收结果：所有 edit 基于原文完成 expected_count、重叠区域、可选 SHA 和写入前字节检查。

- [x] `EDIT-004` `DONE`：实现原子写入和失败回滚。
  - 依赖：EDIT-002、EDIT-003。
  - 验收结果：同目录临时文件 + fsync + `os.replace`；失败清理临时文件，回读异常时恢复原始字节。

- [x] `EDIT-005` `DONE`：实现语法验证和 diff 返回。
  - 依赖：EDIT-003。
  - 验收结果：Python 使用 `ast.parse`；返回 unified diff，并受 `tools.diff_max_chars` 限制。

- [x] `EDIT-006` `DONE`：注册工具并定义 JSON Schema 与风险等级。
  - 依赖：EDIT-004、EDIT-005。
  - 验收结果：作为第16个工具注册，Agent/MCP 核心循环零修改；LLM 不能覆盖 workdir。

- [x] `EDIT-007` `DONE`：补齐事务编辑测试。
  - 依赖：EDIT-002–EDIT-006。
  - 验收结果：新增22个本地通过测试；Windows 因权限跳过的符号链接用例已在 Ubuntu CI 实际通过。

- [x] `EDIT-008` `DONE`：在端到端任务中加入编辑阶段指标。
  - 依赖：EDIT-006。
  - 验收结果：报告 schema v2 记录事务/旧写入次数、成功、前置失败、写入失败、回滚、错误码、目标路径及其与 changed/expected files 的交集；新增5个测试。

- [x] `EDIT-009` `DONE`：复测20个 Agent 任务。
  - 依赖：EDIT-007、EDIT-008。
  - 授权：用户已在2026-08-14本轮明确授权真实模型 API 费用。
  - 运行方案：`agent-v2-transactional`，20个冻结任务 × Hybrid/Rerank，共40次；旧 `agent-v1` 永不覆盖。
  - 验收：任务和答案不变；报告目标文件修改率、任务成功率和范围外修改率。
  - 结果：40/40 报告齐全，冻结 SHA 一致；严格成功 Hybrid 11/20、Rerank 8/20；目标文件修改 Hybrid 14/20、Rerank 12/20。
  - 编辑指标：有编辑的26个任务全部使用事务工具，旧写入0；事务调用 Hybrid 17/17、Rerank 15/15 成功。
  - 异常：A16-Hybrid 在尝试 `pip install sentence-transformers` 后900秒超时，作为正式失败保留且没有重跑；已修复 shell/runner 子进程树清理和未来 worker 的 venv/offline 环境。
  - 判定：EDIT-009 执行完成；M1 的80%目标未达成，不修改冻结 Oracle，差距转交 M2。

## 6. Agent 状态机任务

- [x] `STATE-001` `DONE`：定义 `AgentPhase` 和合法转移表。
  - 验收结果：独立 `execution_state.py` 定义 INIT、DISCOVER、INSPECT、PLAN、EDIT、VERIFY、RECOVER、REVIEW、COMPLETE、FAILED；终态不可离开，非法转移返回 `invalid_phase_transition`。
- [x] `STATE-002` `DONE`：定义 `TaskExecutionState`。
  - 验收结果：每个 Agent 用户轮次创建独立状态，记录 session/task、迭代、计数、修改文件、错误码、测试返回码、有限转移历史、时间和终态原因；不保存源码、模型上下文或 shell 输出。
- [x] `STATE-003` `DONE`：用工具事件驱动客观状态更新。
  - 验收结果：search/read/edit/test 的真实完成事件更新状态；事务编辑仅 `success=true` 且非 dry-run 计为成功，失败进入 RECOVER；pytest 返回码为0才产生验证成功证据。模型最终文字不能让缺少编辑/验证证据的 mutation 任务进入 COMPLETE。
- [x] `STATE-004` `DONE`：按检索、阅读、编辑、测试划分预算。
  - 验收结果：新增 discovery/inspect/edit/verify/recovery 可配置预算、计数、剩余额度和 `enforce_budget` 决策接口；`max_iterations` 仍是硬上限。复杂强制调度策略尚未启用。
- [ ] `STATE-005` `PLANNED`：实现测试失败后的 RECOVER。
- [ ] `STATE-006` `PLANNED`：完成前强制 Diff 审核。
- [ ] `STATE-007` `PLANNED`：保持 CLI、Server 和 MCP 兼容。
- [ ] `STATE-008` `PLANNED`：状态机单元测试和端到端复测。

### STATE-001～STATE-004 完成记录

- 状态：`DONE`
- 日期：2026-08-17
- 修改文件：`execution_state.py`、`agent.py`、`tools/core_tools.py`、`server.py`、配置、测试和项目管理文档。
- 对外行为：CLI/API/MCP 调用方式不变；`run_shell` 文本末尾新增稳定的 `[returncode] N`，供状态机客观判断测试结果。
- 测试结果：新增状态测试17 passed；全量151 passed、4 skipped；未调用真实模型。
- 遗留问题：本轮不强制阻止超预算工具调用，不自动选择恢复动作，也未进行付费端到端复测。
- 下一任务：`STATE-005`。

## 7. Trace 与失败分析任务

- [ ] `TRACE-001` `PLANNED`：定义 Phase/Retrieval/Edit/Test Trace。
- [ ] `TRACE-002` `PLANNED`：Trace 脱敏和长度限制。
- [ ] `TRACE-003` `PLANNED`：实现阶段漏斗统计。
- [ ] `TRACE-004` `PLANNED`：实现主失败阶段分类。
- [ ] `TRACE-005` `PLANNED`：区分环境失败和代码失败。
- [ ] `TRACE-006` `PLANNED`：在 Dashboard 和 metrics 暴露聚合指标。

## 8. 自适应检索任务

- [ ] `ROUTE-001` `PLANNED`：实现 QueryFeatures。
- [ ] `ROUTE-002` `PLANNED`：实现 RetrievalPlan。
- [ ] `ROUTE-003` `PLANNED`：实现检索置信信号。
- [ ] `ROUTE-004` `PLANNED`：实现规则式路由器。
- [ ] `ROUTE-005` `PLANNED`：实现 RerankPolicy 和延迟预算。
- [ ] `ROUTE-006` `PLANNED`：在开发集调参，不读取 test-v1 结果。
- [ ] `ROUTE-007` `PLANNED`：建立新独立验证集。
- [ ] `ROUTE-008` `PLANNED`：对比固定 RRF、纯 Vector 和自适应策略。

## 9. 结构图任务

- [ ] `GRAPH-001` `PLANNED`：定义文件、类、函数节点模型。
- [ ] `GRAPH-002` `PLANNED`：解析 contains/imports 边。
- [ ] `GRAPH-003` `PLANNED`：解析简单 calls/inherits 边。
- [ ] `GRAPH-004` `PLANNED`：建立 tests 关系映射。
- [ ] `GRAPH-005` `PLANNED`：实现种子 Chunk 一跳扩展。
- [ ] `GRAPH-006` `PLANNED`：实现上下文预算和去重。
- [ ] `GRAPH-007` `PLANNED`：跨模块专项评测。

## 10. 模型服务任务

- [ ] `MODEL-001` `PLANNED`：定义 Rerank Worker 状态机。
- [ ] `MODEL-002` `PLANNED`：有界请求队列。
- [ ] `MODEL-003` `PLANNED`：推理超时和队列满回退。
- [ ] `MODEL-004` `PLANNED`：连续失败熔断和冷却探测。
- [ ] `MODEL-005` `PLANNED`：后台预热。
- [ ] `MODEL-006` `PLANNED`：指标与健康状态。
- [ ] `MODEL-007` `PLANNED`：并发压力和死锁测试。

## 11. 每个任务完成时填写

复制以下模板到任务下面或开发日志：

```markdown
### TASK-ID 完成记录

- 状态：DONE
- 日期：YYYY-MM-DD
- 修改文件：
- 设计说明：
- 测试命令：
- 测试结果：
- 指标变化：
- 提交 SHA：
- 遗留问题：
- 下一任务：
```
