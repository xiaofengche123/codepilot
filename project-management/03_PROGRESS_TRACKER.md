# CodePilot 进度跟踪表

更新时间：2026-08-14

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
| M1 事务式代码编辑 | `IN_PROGRESS` | 当前最高优先级，从 EDIT-001 开始 |
| M2 Agent 状态机 | `PLANNED` | 依赖事务编辑接口稳定 |
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

- [x] `TEST-001` `DONE`：当前全量测试89 passed、3 skipped。
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

- [ ] `EDIT-001` `IN_PROGRESS`：设计编辑请求和结果数据结构。
  - 输出：`EditOperation`、`EditRequest`、`EditResult`。
  - 验收：错误码覆盖路径、匹配、冲突、语法和写入失败。

- [ ] `EDIT-002` `PLANNED`：实现路径与文件安全检查。
  - 依赖：EDIT-001。
  - 验收：拒绝 workdir 外路径、符号链接逃逸、目录、二进制和超大文件。

- [ ] `EDIT-003` `PLANNED`：实现匹配和事务预检查。
  - 依赖：EDIT-001。
  - 验收：所有 edit 在写入前完成 expected_count、重叠区域和 SHA 检查。

- [ ] `EDIT-004` `PLANNED`：实现原子写入和失败回滚。
  - 依赖：EDIT-002、EDIT-003。
  - 验收：任一阶段失败时原文件字节不变；临时文件不残留。

- [ ] `EDIT-005` `PLANNED`：实现语法验证和 diff 返回。
  - 依赖：EDIT-003。
  - 验收：Python 语法错误拒绝写入；diff 包含正确行但受长度限制。

- [ ] `EDIT-006` `PLANNED`：注册工具并定义 JSON Schema 与风险等级。
  - 依赖：EDIT-004、EDIT-005。
  - 验收：无需修改 Agent 核心循环即可使用；LLM 不能覆盖 workdir。

- [ ] `EDIT-007` `PLANNED`：补齐事务编辑测试。
  - 依赖：EDIT-002–EDIT-006。
  - 验收：至少覆盖路线图 M1 中列出的全部边界。

- [ ] `EDIT-008` `PLANNED`：在端到端任务中加入编辑阶段指标。
  - 依赖：EDIT-006。
  - 验收：记录 edit attempted、precondition failure、rollback 和 changed files。

- [ ] `EDIT-009` `PLANNED`：复测20个 Agent 任务。
  - 依赖：EDIT-007、EDIT-008。
  - 验收：任务和答案不变；报告目标文件修改率、任务成功率和范围外修改率。

## 6. Agent 状态机任务

- [ ] `STATE-001` `PLANNED`：定义 `AgentPhase` 和合法转移表。
- [ ] `STATE-002` `PLANNED`：定义 `TaskExecutionState`。
- [ ] `STATE-003` `PLANNED`：用工具事件驱动客观状态更新。
- [ ] `STATE-004` `PLANNED`：按检索、阅读、编辑、测试划分预算。
- [ ] `STATE-005` `PLANNED`：实现测试失败后的 RECOVER。
- [ ] `STATE-006` `PLANNED`：完成前强制 Diff 审核。
- [ ] `STATE-007` `PLANNED`：保持 CLI、Server 和 MCP 兼容。
- [ ] `STATE-008` `PLANNED`：状态机单元测试和端到端复测。

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
