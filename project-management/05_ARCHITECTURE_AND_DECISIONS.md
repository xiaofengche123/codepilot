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
- 原因：内部 Recall@10 提升到0.9254，但 CPU P95 为6.85秒；端到端单次成功率只增加5个百分点。
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

- 状态：`PLANNED`
- 原因：完全自由循环缺少约束，完全固定流程又无法处理测试失败和重新定位；可回环状态机兼顾控制和适应性。

### ADR-009：结构图只做检索后扩展

- 状态：`PLANNED`
- 决策：未来 AST 图不替换 BM25/Vector，而是从检索种子做一跳扩展。
- 原因：避免建设昂贵且难验证的完整知识图谱，同时解决跨模块链路问题。

### ADR-010：结构化 Trace 不保存敏感内容

- 状态：`PLANNED`
- 决策：Trace 保存阶段、路径、行号、分数、错误码和摘要，不保存 `.env`、API Key 和无限长源码。

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
