# CodePilot 进度跟踪表

更新时间：2026-08-19

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
| M2 Agent 状态机 | `DONE` | STATE-001～008 完成；恢复、预算门控、最新证据与 Diff Review 已接管循环 |
| M3 Trace 与失败分析 | `DONE` | 有界脱敏 Trace、十级漏斗、唯一失败分类及 Dashboard/Prometheus 已完成 |
| M4 自适应检索 | `IN_PROGRESS` | ROUTE-001～004 已完成；下一项 ROUTE-005，不得使用 test-v1 调参 |
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
- [x] `STATE-005` `DONE`：实现结构化 RECOVER、确定性错误映射、有界临时指令和真正生效的 recovery budget。
- [x] `STATE-006` `DONE`：引入 mutation/verified/reviewed revision，强制最新测试与非空 `git_diff` review 后才允许 mutation COMPLETE。
- [x] `STATE-007` `DONE`：保持 CLI、Server、MCP 与评测回调兼容；API 危险 shell 仍默认拒绝并明确返回 `verification_unavailable`。
- [x] `STATE-008` `DONE`：使用 fake model/fake tool 和临时目录覆盖正常、恢复、证据失效、预算拒绝、多 tool call、并发与脱敏轨迹。

### STATE-005～STATE-008 完成记录

- 状态：`DONE`
- 日期：2026-08-19
- 设计：`RecoveryAction`/`RecoveryDecision` 纯映射失败；`CompletionDecision` 在证据不足时继续下一轮而非立即失败；控制指令仅临时插入下一次模型请求。
- 完成门槛：每次真实字节编辑推进 revision 并作废旧证据；pytest 返回码0绑定当前 revision；最后一次测试后实际执行的非空 `git_diff` 绑定 review revision。
- 协议：每个 tool call 无论成功、失败、预算拒绝或终态拒绝均生成对应 `ToolMessage`；API/MCP 危险工具策略未放宽。
- 测试：状态机定向35 passed；CLI/API/MCP 兼容回归通过；全量182 passed、4 skipped；`git diff --check` 通过。
- 评测：仅确定性测试，未调用真实模型、未生成正式结果；付费重复评测待新授权。
- 限制：`auto` 在完全没有编辑尝试时无法可靠识别修改请求；API 默认拒绝 shell，显式 mutation 当前可能以 `verification_unavailable` 结束，等待未来安全 TestRunner。
- 下一任务：`TRACE-001`。

### STATE-001～STATE-004 完成记录

- 状态：`DONE`
- 日期：2026-08-17
- 修改文件：`execution_state.py`、`agent.py`、`tools/core_tools.py`、`server.py`、配置、测试和项目管理文档。
- 对外行为：CLI/API/MCP 调用方式不变；`run_shell` 文本末尾新增稳定的 `[returncode] N`，供状态机客观判断测试结果。
- 测试结果：新增状态测试17 passed；全量151 passed、4 skipped；未调用真实模型。
- 遗留问题：本轮不强制阻止超预算工具调用，不自动选择恢复动作，也未进行付费端到端复测。
- 下一任务：`STATE-005`。

## 7. Trace 与失败分析任务

- [x] `TRACE-001` `DONE`：定义并接入 Phase/Retrieval/Edit/Test Trace；记录迭代、review 和 completion decision，API snapshot/评测报告向后兼容暴露。
- [x] `TRACE-002` `DONE`：统一标识符、路径和字符串脱敏；事件、路径、检索文件及失败原因全部有界。
- [x] `TRACE-003` `DONE`：评测汇总自动生成检索命中、正确读取、测试读取、编辑、Oracle、测试和范围漏斗。
- [x] `TRACE-004` `DONE`：十类失败按稳定优先级产生唯一主阶段和有界次要原因。
- [x] `TRACE-005` `DONE`：稳定错误码、worker 状态和有限错误模式区分 code/environment/control；环境失败优先于代码症状。
- [x] `TRACE-006` `DONE`：TaskQueue 聚合运行时 Trace，Prometheus 和 Dashboard 暴露八级在线执行漏斗及失败标签。

### TRACE-001～TRACE-006 完成记录

- 状态：`DONE`
- 日期：2026-08-19
- 设计：采集层 `task_trace.py` 不保存 query、源码、diff、shell 输出或模型上下文；分析层 `trace_analysis.py` 只消费安全 Trace、稳定错误码和 Oracle 字段。
- 评测：新报告保留原 Oracle `success`，同时增加 `agent_completed`、`failure_stage`、`secondary_failure_reasons`、`failure_domain` 和十级漏斗；Oracle 成功但状态机失败不会被误报为 COMPLETE。
- 在线：Server 的任务对象保存独立 Trace；Worktree、模型和 worker 级失败归入 environment；Prometheus/Dashboard 展示检索、读取、编辑、测试、review 和完成计数。
- 验证：Trace/评测/API 定向92 passed、3 skipped；全量211 passed、4 skipped；旧40份正式报告只读兼容汇总成功；冻结数据、密钥扫描与 `git diff --check` 通过。
- 遗留：旧报告没有逐轮 Trace，只能从兼容字段做失败分类；环境文本模式是保守规则而非根因证明。下一任务 `ROUTE-001`。

## 8. 自适应检索任务

- [x] `ROUTE-001` `DONE`：实现 QueryFeatures。
- [x] `ROUTE-002` `DONE`：实现 RetrievalPlan。
- [x] `ROUTE-003` `DONE`：实现检索置信信号。
- [x] `ROUTE-004` `DONE`：实现规则式路由器。
- [ ] `ROUTE-005` `PLANNED`：实现 RerankPolicy 和延迟预算。
- [ ] `ROUTE-006` `PLANNED`：在开发集调参，不读取 test-v1 结果。
- [ ] `ROUTE-007` `PLANNED`：建立新独立验证集。
- [ ] `ROUTE-008` `PLANNED`：对比固定 RRF、纯 Vector 和自适应策略。

### ROUTE-001 完成记录

- 状态：`DONE`
- 日期：2026-08-19
- 修改文件：`rag/query_features.py`、`tests/test_query_features.py` 和项目管理文档。
- 设计：不可变 `QueryFeatures` 仅保存原始字符数、有界分析长度/长度桶、词元数、标识符/自然语言/中英文比例、混合语言及路径、配置键、错误、堆栈和跨模块启发式布尔值，并附固定 reason codes；不保存原始 query。
- 有界与隐私：最多分析前16,384个 Unicode 码点；所有正则只作用于该窗口，空分母为0.0，比例限制在 `[0.0, 1.0]`，超长输入保留精确字符数并标记 `analysis_truncated`。
- 运行时：模块只依赖 Python 标准库，不读文件、不联网、不加载 Embedding/Reranker/LLM；未接入 `retrieve` 或调整 BM25/Vector/RRF/Rerank 行为。
- 测试：QueryFeatures 定向32 passed；检索/精排/评测/索引/配置/工具相关回归64 passed；全量243 passed、4 skipped；`git diff --check` 通过。
- 限制：中英文统计分别限定 CJK 基本区/扩展A和 ASCII 字母；路径、错误、堆栈及跨模块判断是保守词法启发式，存在误报/漏报，不代表语义真值。
- 评测：未读取冻结 test-v1 结果调参，未运行正式 RAG 或付费 Agent 评测，冻结数据未修改。
- 下一任务：`ROUTE-002`。

### ROUTE-002 完成记录

- 状态：`DONE`
- 日期：2026-08-19
- 修改文件：`rag/retrieval_plan.py`、`tests/test_retrieval_plan.py` 和项目管理文档。
- 设计：不可变 `RetrievalPlan` 定义 BM25/Vector 权重、`rrf_k`、候选数、文档/Rerank 开关、有界人类可读原因和稳定 reason codes；`to_dict()` 输出显式 schema version 和 JSON-ready 基础类型。
- 约束：权重必须是有限非负数且不能同时为0；`rrf_k` 限制为1～10,000，候选数限制为1～100；布尔字段拒绝整数替代；原因最多500字符且单行，reason codes 最多16个、唯一且使用有界小写标识符。
- 兼容计划：`BASELINE_RETRIEVAL_PLAN` 精确对应当前 `config.DEFAULTS` 的固定 Weighted RRF 参数，并由测试防止漂移；它没有接入 `retrieve`，不改变当前配置或运行时行为。
- 隐私与依赖：结构没有 query、hits 或 results 字段；原因契约禁止复制原始 query并有硬长度边界。模块只依赖标准库，不加载 config、Chroma、Embedding 或 Reranker。
- 测试：RetrievalPlan 定向56 passed；QueryFeatures/RAG/配置/工具相关回归152 passed；全量299 passed、4 skipped；`git diff --check` 通过。
- 评测：未读取 test-v1 结果调参，未运行正式 RAG 或付费 Agent 评测，冻结数据未修改。
- 限制：ROUTE-002 只定义计划契约；尚未计算排名一致性、标识符覆盖率等置信信号，也没有规则路由器或 RerankPolicy。
- 下一任务：`ROUTE-003`。

### ROUTE-003 完成记录

- 状态：`DONE`
- 日期：2026-08-19
- 修改文件：`rag/query_features.py`、`rag/retrieval_confidence.py`、`tests/test_retrieval_confidence.py` 和项目管理文档。
- 设计：不可变 `RetrievalConfidenceSignals` 保存 Top-K 两路结果数、重合数/率、可缺省 Top-1 一致性、查询标识符数/命中数/覆盖率、可缺省 Vector Top-1/Top-2 原始分数差、候选/文件计数、多样性比例及固定 reason codes。
- 确定性定义：重合率为双路唯一 UID 交集数除以固定 `top_k`；标识符覆盖在两路 Top-K 唯一候选并集的有界文件名/文档词元上计算；文件多样性为唯一文件数除以唯一候选数；Vector margin 是 higher-is-better 的原始 `top1.score - top2.score`，逆序时钳制为0并显式标记。
- 缺失语义：任一路无结果时 Top-1 一致性为 `None`；少于两个有限向量分数时 margin 为 `None`；所有空分母比例定义为0.0。没有生成聚合置信分或概率。
- 有界与隐私：`top_k` 限制1～100，查询仍只分析前16,384码点且最多提取256个临时标识符，每个候选的文件名和文档字段各最多扫描20,000字符；输出不保存 query、标识符列表、文档或 hits。
- 运行时：模块只消费调用方提供的排名，不执行检索、不读取 config、不加载模型，也不选择 `RetrievalPlan`；现有固定 RRF/Rerank 行为不变。
- 测试：置信信号定向40 passed；QueryFeatures/Plan/RAG/配置/工具相关回归192 passed；全量339 passed、4 skipped；`git diff --check` 通过。
- 评测：未读取 test-v1 结果调参，未运行正式 RAG 或付费 Agent 评测，冻结数据未修改。
- 限制：标识符覆盖是大小写折叠后的精确代码词元匹配，不做语义同义词或命名风格转换；Vector margin 保留模型原始尺度，不能跨模型直接比较。
- 下一任务：`ROUTE-004`。

### ROUTE-004 完成记录

- 状态：`DONE`
- 日期：2026-08-19
- 修改文件：`rag/query_features.py`、`rag/retrieval_router.py`、`tests/test_retrieval_router.py` 和项目管理文档。
- 输入输出：纯函数 `route_retrieval(QueryFeatures, RetrievalConfidenceSignals | None) -> RetrievalPlan`；严格要求已验证的结构化输入，不读取 query、config、文件或模型。
- 查询规则：精确代码使用 BM25/Vector `2.5/0.25`；自然语言使用 `0.75/1.5`；中英混合和跨模块使用 `1.0/1.0`；不明确/空查询保持当前 `2.0/0.25`。跨模块候选扩到50，中英混合为40，其余为30，`rrf_k` 保持10。
- 置信规则：只有 BM25/Vector 有结果时分别退化为 `1/0` 或 `0/1`；两路都无候选回到兼容默认；Top-1 不同且固定-K重合率不高于0.2时使用平衡权重并把候选扩到50。Top-1 一致和标识符全覆盖只记录解释，不伪造概率。
- 文档与 Rerank：QueryFeatures 增加显式 documentation/README/guide/文档意图，命中时 `include_docs=true`；所有 ROUTE-004 计划均强制 `rerank=false` 并写入延后策略 reason code，等待 ROUTE-005。
- 调参纪律：上述权重、阈值和候选数是代码审查可见的 v1 保守常量，未读取 test-v1 或正式结果选择；必须到 ROUTE-006 才能使用开发集调参。
- 运行时：`rag.retriever` 未导入路由器，当前固定配置、四种检索模式和默认关闭 Rerank 的行为不变。
- 测试：路由定向30 passed；特征/Plan/信号/RAG/配置/工具相关回归222 passed；全量369 passed、4 skipped；`git diff --check` 通过。
- 评测：未运行真实检索、正式 RAG 或付费 Agent 评测；冻结数据未修改。
- 限制：尚无 RerankPolicy、延迟预算或运行时接线；v1 常量只证明确定性和协议正确，不证明质量最优。
- 下一任务：`ROUTE-005`。

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
