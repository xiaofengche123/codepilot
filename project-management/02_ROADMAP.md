# CodePilot 功能完善路线图

更新时间：2026-08-17

## 1. 路线图原则

后续开发必须遵循四条原则：

1. **证据驱动**：优先解决现有评测已经暴露的问题。
2. **能力叠加而非删除**：保留 BM25、Vector、RRF、Rerank、MCP、Worktree 等现有能力，通过控制层连接它们。
3. **逻辑闭环**：每个功能必须具有输入、状态、输出、错误处理、回退、测试和评测。
4. **简历可解释**：每个复杂模块都必须能回答“为什么需要、如何实现、如何验证、代价是什么”。

## 2. 总体阶段

```text
M0 固化当前 RAG 与评测基线
  ↓
M1 事务式代码编辑
  ↓
M2 Agent 执行状态机
  ↓
M3 阶段化 Trace 与失败分析
  ↓
M4 自适应检索策略
  ↓
M5 AST 代码结构图扩展
  ↓
M6 Reranker 服务化、熔断和可观测性
  ↓
M7 独立测试集与重复 Agent 评测
```

M1–M3 解决 Agent 可靠执行；M4–M5 提升检索决策和跨模块理解；M6–M7 完成服务工程化和可信验证。

---

## M0：固化当前基线

状态：`IN_PROGRESS`

### 目标

把已经完成的 RAG、冻结集、CodeSearchNet 和端到端评测作为可复现基线安全保存，避免后续开发时丢失或混入无关文件。

### 工作项

- 审查当前所有未提交文件。
- 确认 `.rag-eval/results` 中报告齐全。
- 确认冻结集 manifest 和 SHA 一致。
- 确认模型缓存、外部数据、临时目录已被忽略。
- 检查文档中没有 API Key。
- 用户确认后提交到 `dev`。
- GitHub Actions 跑通后记录实际 CI 结果。

### 验收标准

- `pytest -q` 通过。
- `git diff --check` 通过。
- `codepilot-test-v1` SHA 不变。
- `install.py` 不在 diff 中。
- `.env`、`.codepilot/model-cache`、`.rag-eval/external-data` 不在提交中。
- 提交后 `dev` 可从干净 clone 运行非模型单元测试。

### 简历价值

形成可复现的 RAG 和 Agent 评测基线，后续所有优化都有前后对比。

---

## M1：事务式代码编辑工具

状态：`IN_PROGRESS`

实现进度：`EDIT-001` 至 `EDIT-009` 已完成；事务编辑能力已通过单元、CI 和一次冻结端到端复测，但端到端目标文件修改率未达到80%验收线，执行决策缺口转入 M2。

### 已知问题

端到端失败中最常见的原因是 Agent 没有修改目标文件。当前 `write_file` 更适合创建或完整覆盖文件，不适合安全修改一小段代码。

### 目标

实现支持精确局部修改、并发前置条件、原子写入、语法验证和失败回滚的编辑事务。

### 建议接口

```python
@dataclass
class EditOperation:
    old_text: str
    new_text: str
    expected_count: int = 1


@dataclass
class EditRequest:
    path: str
    edits: list[EditOperation]
    expected_sha256: str | None = None
    dry_run: bool = False


@dataclass
class EditResult:
    success: bool
    path: str
    before_sha256: str
    after_sha256: str | None
    replacements: int
    diff: str
    rolled_back: bool
    error_code: str | None
```

### 执行闭环

```text
解析路径
→ resolve 后验证仍在 Agent workdir
→ 拒绝目录、二进制和超大文件
→ 读取原编码、换行风格和 SHA
→ 检查 expected_sha256
→ 检查所有 old_text 匹配次数
→ 在内存应用全部 edits
→ 生成 diff
→ 对 Python 文件执行 ast.parse
→ 写入同目录临时文件
→ 原子替换
→ 失败时删除临时文件并保留原文件
```

### 必须处理的边界

- `..` 路径穿越。
- 绝对路径越界。
- 符号链接逃逸。
- 匹配0次或多次。
- 多个 edit 区域重叠。
- 读取后文件被外部进程修改。
- UTF-8 BOM。
- CRLF/LF。
- Python 语法错误。
- dry-run。
- Diff 过大时截断展示但不影响完整修改。
- 多 edit 中任意一个失败时不得部分写入。

### 测试计划

- 单一替换成功。
- 多 edit 原子成功。
- expected_count 不匹配拒绝。
- SHA 冲突拒绝。
- 路径越界拒绝。
- 语法错误回滚。
- CRLF/BOM 保留。
- dry-run 不落盘。
- workdir 注入不可被 LLM 覆盖。
- 原文件权限错误时不损坏内容。

### 验收标准

- 新工具单元测试全部通过。
- 全量回归通过。
- 在现有20个任务上，目标文件修改率由当前约50%提高到至少80%。
- 意外文件修改率低于5%。
- 不允许为了通过任务修改冻结 Oracle。

### 2026-08-17 验收结果

- Hybrid 目标文件修改率由 v1 的50%提升到70%，Rerank 从65%变为60%；均未达到80%。
- 严格成功率为 Hybrid 55%、Rerank 40%；单次非确定性轨迹不能用于归因。
- 所有发生编辑的26个任务均使用事务工具，旧写入为0；32次调用全部成功。
- 记录到测试文件的范围外修改为 Hybrid 10%、Rerank 15%，但冻结任务没有独立 `allowed_files` 字段，部分“意外”文件是 Agent 主动补的相关测试，不能直接当越界安全事件。
- 结论：编辑原语已稳定，未达标部分主要是 Agent 没有在10步内进入编辑；不继续为提高数字修改事务工具或冻结 Oracle，下一阶段由 M2 解决。

### 简历价值

可描述为“带 SHA 乐观并发控制、原子写入、语法验证和失败回滚的事务式代码编辑系统”。

---

## M2：Agent 执行状态机

状态：`PLANNED`

依赖：M1

### 已知问题

当前 Agent 允许模型自由循环，部分任务反复搜索和读取文件，直到10步耗尽仍没有编辑。

### 目标

把 Agent 过程拆为有约束、可回环的阶段，同时保留模型决策能力。

### 状态

```python
class AgentPhase(Enum):
    ANALYZE = "analyze"
    RETRIEVE = "retrieve"
    INSPECT = "inspect"
    PLAN = "plan"
    EDIT = "edit"
    VERIFY = "verify"
    REVIEW = "review"
    RECOVER = "recover"
    COMPLETE = "complete"
    FAILED = "failed"
```

### 允许转移

```text
ANALYZE → RETRIEVE
RETRIEVE → INSPECT
INSPECT → PLAN 或 RETRIEVE
PLAN → EDIT 或 INSPECT
EDIT → VERIFY 或 RECOVER
VERIFY → REVIEW 或 RECOVER
RECOVER → INSPECT / EDIT / FAILED
REVIEW → COMPLETE 或 EDIT
```

### 客观状态条件

- 只有发生检索工具调用才记录 `retrieved`。
- 只有读取文件才记录 `inspected`。
- 只有编辑事务成功才记录 `edited`。
- 只有测试命令返回0才记录 `verified`。
- 只有检查 diff 后才能 `complete`。

### 独立预算

```python
max_iterations = 12
max_retrieval_calls = 3
max_file_reads = 8
max_edit_attempts = 3
max_test_runs = 4
max_recoveries = 2
```

预算必须可配置，并区分不同类型工具，不能只用一个全局循环次数。

### 验收标准

- 90%以上端到端任务发生编辑尝试。
- 不再大量出现“只读不改后耗尽步数”。
- 测试失败后能够进入 RECOVER。
- 每个任务有完整 phase trace。
- 状态机不能破坏现有 CLI、Server、MCP 工具调用。

---

## M3：阶段化 Trace 与失败分析

状态：`DONE`

依赖：M2

### 目标

建立从检索到完成的结构化证据，自动判断失败发生在哪一层。

### 核心数据

```python
@dataclass
class TaskTrace:
    task_id: str
    phases: list[PhaseEvent]
    retrieval_calls: list[RetrievalTrace]
    inspected_files: list[str]
    edit_attempts: list[EditTrace]
    test_runs: list[TestTrace]
    changed_files: list[str]
    final_status: str
    failure_stage: str | None
```

### 漏斗指标

```text
任务总数
→ 检索命中 required
→ 读取正确文件
→ 读取相关测试
→ 尝试编辑
→ 修改目标文件
→ 修改满足 Oracle
→ 执行测试
→ 测试通过
→ 无范围外修改
```

### 失败分类

- `retrieval_miss`
- `wrong_file_inspected`
- `no_edit_attempt`
- `edit_precondition_failed`
- `incorrect_edit`
- `syntax_failure`
- `test_assertion_failure`
- `environment_failure`
- `iteration_budget_exhausted`
- `unexpected_file_change`

### 验收标准

- 端到端报告自动生成漏斗。
- 每个失败任务有唯一主失败阶段和可选次要原因。
- 环境失败不得被统计成代码能力失败。
- Trace 不包含 API Key、完整 `.env` 或过长源码内容。

---

## M4：自适应检索策略

状态：`PLANNED`

依赖：M1–M3 基本稳定

### 已知问题

- 内部代码库偏 BM25。
- CodeSearchNet 外部自然语言查询偏 Vector。
- 固定权重不具备普适性。
- Rerank 质量更高，但 CPU 延迟过大且端到端收益不稳定。

### 目标

保留四种检索能力，通过可解释规则选择检索计划，而不是为所有查询使用同一权重。

### 查询特征

- 标识符比例。
- 是否包含文件路径。
- 是否包含配置点号键。
- 是否包含堆栈和错误文本。
- 中英比例。
- 是否包含跨模块意图。
- 自然语言比例。
- 查询长度。

### RetrievalPlan

```python
@dataclass
class RetrievalPlan:
    bm25_weight: float
    vector_weight: float
    rrf_k: int
    candidate_count: int
    include_docs: bool
    rerank: bool
    reason: str
```

### 初版规则

- 精确符号/路径/错误文本：BM25 高权重。
- 自然语言功能定位：提高 Vector 权重。
- 中英混合：平衡 RRF。
- 跨模块且两路排名不一致：启用 Rerank。
- 高置信度精确匹配：跳过 Rerank。
- 查询明确要求说明文档：`include_docs=true`。

### 置信信号

- BM25/Vector Top-10 重合率。
- Top-1 是否一致。
- 查询标识符在候选中的覆盖率。
- Vector Top-1/Top-2 margin。
- Top-K 文件多样性。

RRF 分数不能直接当概率，必须把置信度描述为可解释启发式分数。

### 调参纪律

- 只能使用开发集。
- `test-v1` 永久冻结。
- CodeSearchNet 正式结果不能反复用于调权重。
- 参数完成后建立新独立集或 `test-v2`。

### 验收标准

- 默认检索 P95 控制在400ms以内。
- Rerank 调用比例低于30%。
- 新独立集 Recall@10 不低于固定 RRF。
- 外部自然语言结果不明显低于纯 Vector。
- 端到端任务成功率不下降。

---

## M5：AST 代码结构图扩展

状态：`DONE_WITH_GAP`

实现进度：`GRAPH-001`～`GRAPH-007` 已完成构图、扩展、预算与冻结跨模块专项评测。Recall@10点差`+0.089167`达到5pp点门槛，但区间跨0，图阶段P95额外耗时`32.754ms`、无关新增单题P95为5，性能与污染验收失败；M5因此不是完全DONE。

依赖：M4

### 定位

这是面向代码结构的轻量图扩展，不是为了名词而建设完整 Knowledge Graph RAG。

### 节点

- 文件。
- 类。
- 函数/方法。
- 配置项。
- 测试函数。
- 工具注册项。

### 边

- `contains`
- `imports`
- `calls`
- `inherits`
- `registers`
- `tests`
- `configures`

### 查询流程

```text
混合检索得到种子 Chunk
→ 映射到符号节点
→ 按查询意图扩展一跳邻居
→ 在上下文预算内打分和去重
→ 可选 Rerank
```

### 约束

- 第一版只支持 Python AST。
- 结构图只用于跨模块和调用链查询。
- 不能替换 BM25/Vector 主召回。
- 必须限制扩展深度和 Chunk 数量。
- 后续 Tree-sitter 多语言支持必须有明确需求再做。

### 验收标准

- cross_module 新测试集 Recall@10 提升至少5个百分点。
- 普通查询延迟不明显增加。
- 图扩展不会把测试、README 或无关入口大量加入上下文。

### GRAPH-007 验收结果

- 20条同仓库内部冻结专项集；数据集SHA-256为`adc8e9bb3a6ebecafca705d925aefda3bc372e95f5ee03ab9602942b84c2dfcc`，策略SHA-256为`ad354bdee9c3604dbd36cbb2d9a321ef7d7a9928b730b1a620ad2d575faf597b`。
- 固定Hybrid与图增强Recall@10为`0.567500/0.656667`，点差`+0.089167`，95% CI `[-0.045833,+0.222500]`；7题改善、3题下降、10题持平。
- 图阶段P95额外耗时`32.754ms`，超过预设`10ms`；单题无关新增P95为5，超过预设3。测试Chunk和文档Chunk新增均为0。
- 结论：召回点估计达标，普通产品路径因未接线保持不变，但专项图路径性能和无关上下文不达标；状态`DONE_WITH_GAP`，不能宣称完整验收或泛化收益。

---

## M6：Reranker 服务化与可观测性

状态：`PLANNED`

依赖：M4

### 目标

把当前锁保护的模型调用升级成具有队列、超时、熔断和指标的模型执行器。

### 模型状态

```text
UNLOADED → LOADING → READY
                    ↓
                 DEGRADED
                    ↓
                  FAILED
```

### 能力

- 启动后后台预热。
- 有界队列。
- 队列满回退 RRF。
- 推理超时回退。
- 连续失败熔断。
- 冷却后单次恢复探测。
- 区分加载失败、队列超限、推理错误和超时。
- 所有回退保存明确原因。

### 指标

- `rag_retrieval_latency_ms`
- `rag_rerank_latency_ms`
- `rag_rerank_queue_size`
- `rag_rerank_fallback_total`
- `rag_rerank_timeout_total`
- `rag_model_load_seconds`
- `rag_search_mode_total`
- `agent_edit_attempt_total`
- `agent_task_success_total`

### 验收标准

- 并发查询无死锁。
- 队列超限和超时都能安全回退。
- RRF 回退结果保持原始 `rrf_rank`。
- 健康检查不触发模型懒加载。
- 压力测试记录吞吐与 P95。

---

## M7：独立测试与重复评测

状态：`PLANNED`

依赖：M1–M6 中计划验证的阶段

### 目标

避免单次 LLM 随机性影响结论，建立可用于简历和面试的最终证据。

### 计划

- 现有20个 Agent 任务每个条件重复3次。
- 固定模型、temperature、任务定义和 Git HEAD。
- 报告 pass@1、pass@3、平均耗时和范围外修改率。
- 由另一位开发者抽审至少20%的内部检索标注。
- 新增不同仓库的小型冻结集。
- 如需修改标注，创建 `test-v2`，绝不修改 v1。

### 验收标准

- 结果包含配置、模型、代码 SHA 和数据 SHA。
- 能区分 Agent 随机失败、检索失败、编辑失败和环境失败。
- 简历只使用重复评测后稳定的指标。

---

## 3. 暂不进入主线的方向

| 方向 | 当前状态 | 原因 |
|---|---|---|
| Multi-Query | `REJECTED` | 现有证据表明主要问题不是召回覆盖不足 |
| 完整 Knowledge Graph RAG | `IDEA` | 建设成本高，先验证轻量 AST 图扩展 |
| 默认 CPU Rerank | `REJECTED` | P95 6.85秒，端到端收益仅单次5个百分点 |
| 训练自定义 Reranker | `IDEA` | 数据规模和标注独立性不足 |
| 用 test-v1 调参 | `REJECTED` | 破坏冻结集可信度 |
| 为指标修改任务答案 | `REJECTED` | 形成评测泄漏 |
