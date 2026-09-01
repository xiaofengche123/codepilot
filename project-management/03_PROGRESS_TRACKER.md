# CodePilot 进度跟踪表

更新时间：2026-09-01

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
| M4 自适应检索 | `DONE_WITH_GAP` | Router 已通过默认关闭的特性开关接入；剩余 RerankPolicy、灰度与线上观测缺口 |
| M5 AST 结构图扩展 | `DONE_WITH_GAP` | GRAPH-001～007完成；Recall点差+8.92pp，但图P95开销32.754ms、无关新增P95=5未达门槛 |
| M6 模型服务化 | `DONE` | MODEL-001～007完成；fake-inference持续/过载/deadline压力无死锁，真实模型性能不由此宣称 |
| M7 重复和独立评测 | `IN_PROGRESS` | 千问正式r1/r2均完成40/40并暂停；r3、新仓库集和外部抽审待执行 |

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
- [x] `ROUTE-005` `DONE`：实现 RerankPolicy 和延迟预算。
- [x] `ROUTE-006` `DONE`：在开发集调参，不读取 test-v1 结果。
- [x] `ROUTE-007` `DONE`：建立新独立验证集。
- [x] `ROUTE-008` `DONE`：对比固定 RRF、纯 Vector 和自适应策略。

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

### ROUTE-005 完成记录

- 状态：`DONE`
- 日期：2026-08-19
- 修改文件：`rag/rerank_policy.py`、`tests/test_rerank_policy.py` 和项目管理文档。
- 数据契约：不可变 `LatencyBudget` 明确总预算、已消耗时间、预留时间和钳制为非负的剩余预算；不可变 `RerankCostEstimate` 使用调用方提供的固定成本和每候选成本；不可变 `RerankDecision` 返回新计划、开关、候选数、预算/估计值和固定 reason codes，均可 JSON 序列化。
- 策略：只有调用方显式允许、模型可用、查询具有跨模块意图、Vector/BM25 双路都有结果、Top-1 不同且固定-K重合率不高于0.2，并且估计成本不超过剩余预算时启用 Rerank。高置信精确匹配、缺信号、单路缺失、证据不足或预算不足均确定性关闭。
- 有界与隐私：实际精排候选最多30；数值拒绝布尔、NaN、无穷和负值；解释使用固定模板，不保存 query、hits、文档或模型内容。延迟值由调用方提供，未把本机历史 P95 硬编码成通用阈值。
- 运行时：策略不读时钟、config 或文件，不执行检索、不加载模型；`rag.retriever` 未导入策略，当前固定 Weighted RRF、四种模式和默认关闭 Rerank 的行为不变。
- 测试：RerankPolicy 定向52 passed；特征/Plan/信号/Router/RAG/配置/工具相关回归274 passed；全量421 passed、4 skipped；`git diff --check` 通过。
- 评测：未读取 test-v1 或正式结果调参，未运行正式 RAG 或付费 Agent 评测，冻结数据未修改。
- 限制：v1 资格规则、0.2分歧阈值和30候选上限尚未经开发集校准；调用方必须提供可信的机器/模型成本估计；尚未验证 P95、质量收益或 Rerank 调用比例。
- 下一任务：`ROUTE-006`。

### ROUTE-006 完成记录

- 状态：`DONE`
- 日期：2026-08-19
- 修改文件：`rag/retrieval_router.py`、`rag/retrieval_tuning.py`、`tests/test_retrieval_router.py`、`tests/test_retrieval_tuning.py`、`tests/test_rerank_policy.py`、`.rag-eval/adaptive-routing-dev-2026-08-19.md` 和项目管理文档。
- 隔离：调参入口只接受文件名 `codepilot-dev.json`，在文件读取前拒绝 test、Agent任务或结果文件；实验只用30条开发集、本地 Chroma 和 D 盘已有 Embedding 缓存，强制离线，未读取 test-v1、冻结 Oracle或正式报告。
- 搜索：每条查询收集一次 Vector/BM25 Top-100，按实际 QueryFeatures/Top-10置信信号分成路由族；每族比较6组权重×3个RRF k×3个候选数，共54组，依次按 Recall@10、MRR@10、较低候选成本和稳定声明顺序选择。
- 参数：自然语言/跨模块 `2.5/0.25, k=10, candidates=30`；中英混合 `1.5/0.5, 10, 30`；低重合 Top-1分歧 `2.0/0.5, 10, 40`；精确代码与兼容基线分别保留 `2.5/0.25` 和 `2.0/0.25`。
- 开发集结果：未校准规则 Recall@10/MRR@10 `0.652778/0.543373`；固定 RRF `0.780556/0.591005`；族级校准 `0.788889/0.593056`。相对固定方案只增加 `+0.008333/+0.002051`，不宣称显著或泛化收益。
- 运行时：Router 常量已冻结为开发集选择，但 Retriever 仍未导入 Router/Tuning/RerankPolicy；默认产品继续固定 Weighted RRF，Rerank继续关闭。
- 测试：Router/Tuning/RerankPolicy 定向107 passed；特征/Plan/信号/Router/Tuning/RAG/配置/工具相关回归299 passed；全量446 passed、4 skipped；`git diff --check` 通过。
- 限制：开发集仅30条、最小路由族2条且由当前项目反推；没有验证新独立集、外部自然语言、400ms P95、Rerank调用比例或 Agent成功率。
- 下一任务：`ROUTE-007`。

### ROUTE-007 完成记录

- 状态：`DONE`
- 日期：2026-08-28
- 修改文件：`rag/retrieval_validation.py`、`tests/test_retrieval_validation.py`、`.rag-eval/codepilot-validation-v1.json`、对应 manifest、评测协议和项目管理文档。
- 数据集：50条新 query，identifier、natural_language、bug_symptom、cross_module、mixed_language 五类各10条；共66个 required 标签，全部映射到当前源码 chunk，未用测试代码充当 required。
- 隔离：验证集与30条开发集做大小写折叠和空白归一后的精确查重，重合为0；冻结入口在读取前拒绝非 `codepilot-validation-v1.json`，不会被 ROUTE-006 调参入口接受。
- 双重冻结：manifest 记录验证集 SHA-256、开发集 SHA-256、语料指纹以及完整 ROUTE-006 路由参数画像和哈希；`--check` 同时拒绝答案漂移和验证前重新调参。
- 评测纪律：本轮状态为 `frozen_unscored`，没有调用 Retriever/Evaluator、Embedding、Reranker、网络或付费 API，没有生成 Recall/MRR；固定 RRF、纯 Vector和冻结自适应策略的单次比较留待 ROUTE-008。
- 测试：Validation/Tuning/Evaluate 定向43 passed；全量456 passed、4 skipped；manifest 双哈希检查和 `git diff --check` 通过。
- 限制：该集合只独立于 ROUTE-006 参数选择，仍来自同一仓库和内部标注流程；样本量每类10条，不能冒充外部或统计显著的泛化证据。
- 下一任务：`ROUTE-008`。

### ROUTE-008 完成记录

- 状态：`DONE`
- 日期：2026-08-28
- 修改文件：`rag/retrieval_comparison.py`、`tests/test_retrieval_comparison.py`、`.rag-eval/adaptive-routing-validation-2026-08-28.json`、同名 Markdown 报告和项目管理文档。
- 预检：ROUTE-007 数据集 SHA 与 ROUTE-006 路由画像双哈希通过；本地旧索引在强制离线下增量更新61个文件、639个 chunk，评测语料 SHA-256 为 `f73d8a579afd2acdbc2a644cd0d67697e2be304c746ea82ba710ac4a0fb14b8f`。
- 协议：入口只接受冻结验证集和固定结果文件名，已有结果时拒绝覆盖；只运行固定 RRF `2.0/0.25,k=10,candidates=30`、纯 Vector、ROUTE-006 冻结自适应三种策略，不启用 Rerank。
- 结果：固定 RRF Recall@10/MRR@10 `0.466667/0.248905`；纯 Vector `0.380000/0.196333`；冻结自适应 `0.486667/0.265857`。本机预热后 P95 分别为66.858/31.349/68.405ms，但不是生产 SLO。
- 成对证据：自适应相对固定 Recall `+0.020000`，95% CI `[0.000000,0.060000]`，1条改善/0条退化/49条持平；MRR `+0.016952`，95% CI `[-0.006143,0.051167]`，7条改善/5条退化/38条持平。只记录小幅点估计，不宣称稳定优势。
- 隔离：结果不包含 query、源码或排名内容；未联网、未调用付费 API、未使用 test-v1、旧正式结果或 Agent Oracle做选择，未回改验证标注或路由参数。
- 测试：Comparison/Validation/Tuning/Router/Confidence 定向115 passed；全量466 passed、4 skipped；双哈希检查与 `git diff --check` 通过。
- 运行时缺口：Retriever 没有接入 Router/RerankPolicy，默认产品仍为固定 RRF、关闭 Rerank；若上线应另立带回退与观测的接线任务。

### ROUTE-RUNTIME-001 完成记录

- 状态：`DONE`
- 日期：2026-08-29
- 修改文件：`rag/retriever.py`、`config.py`、`config/settings.yaml`、`tests/test_retriever.py`、`tests/test_config.py`、README和项目管理文档。
- 接线：Hybrid 与 Rerank 的候选生成路径在 `rag.adaptive_routing.enabled=true` 时提取有界 QueryFeatures，使用 Vector/BM25 Top-10置信信号调用冻结 Router，再按计划权重、RRF k和候选数融合。
- 兼容：开关默认 false，关闭时保持原固定 RRF及原 metadata；文档意图可在自适应路径显式包含文档，但不会覆盖用户已有的 `rag.include_docs=true`。
- 回退：特征/预路由失败直接回到固定路径；取得双路排名后的置信或路由失败复用同批候选固定融合，不重复请求 Embedding。`fallback_on_error=false` 可用于严格失败。
- 观测：自适应结果只附加路由版本、族、固定 reason codes、权重、RRF k和候选数，不保存 query、文档或模型输出。
- 验证：Retriever/Config/Router/Confidence/Reranker 定向98 passed；全量471 passed、4 skipped；真实本地离线 smoke 返回3条全部带 `adaptive_routing=true`，路由版本 `rule_router_v1`、族 `natural_language`、候选数30。
- 遗留：RerankPolicy仍未接入；默认不开启自适应，后续启用需灰度、回退指标和线上延迟/质量观测。

## 9. 结构图任务

- [x] `GRAPH-001` `DONE`：定义文件、类、函数节点模型。
- [x] `GRAPH-002` `DONE`：解析 contains/imports 边。
- [x] `GRAPH-003` `DONE`：解析简单 calls/inherits 边。
- [x] `GRAPH-004` `DONE`：建立 tests 关系映射。
- [x] `GRAPH-005` `DONE`：实现种子 Chunk 一跳扩展。
- [x] `GRAPH-006` `DONE`：实现上下文预算和去重。
- [x] `GRAPH-007` `DONE_WITH_GAP`：跨模块专项评测完成；性能与无关上下文验收未通过。

### GRAPH-001 完成记录

- 状态：`DONE`
- 日期：2026-08-29
- 修改文件：`rag/code_graph.py`、`tests/test_code_graph.py`、README和项目管理文档。
- 契约：`GraphNodeKind` 仅包含 file/class/function，方法统一为function；不可变 slots 节点保存 schema、稳定 ID、名称、限定名、POSIX相对路径、行区间、父节点和固定 Python language。
- 身份：SHA-256节点 ID由语言、类型、路径和限定名生成，不包含行号，因此普通插行不改变身份；改名、换文件或换类型会改变身份。
- 边界：路径拒绝绝对路径、遍历、空/点段、换行和超长；行号拒绝 bool、倒序、非正数和超过1,000万；符号名称遵守Python标识符与点分限定名契约，并支持Unicode标识符。
- 隐私：序列化不包含source、content、docstring或decorators；模块不导入AST、文件I/O、Indexer或Retriever。
- 范围：父节点 ID 只做格式要求，父子存在性及contains/imports正确性由GRAPH-002验证；本轮不解析 calls/inherits、不构图、不改变检索行为。
- 测试：节点模型定向30 passed；节点/Indexer相关回归36 passed；全量501 passed、4 skipped；`git diff --check`通过。
- 下一任务：`GRAPH-002`。

### GRAPH-002 完成记录

- 状态：`DONE`
- 日期：2026-08-29
- 修改文件：`rag/code_graph.py`、`rag/code_graph_builder.py`、`tests/test_code_graph_builder.py`、README和项目管理文档。
- 契约：新增不可变 contains/imports 边和稳定SHA-256边 ID；构图器只消费调用方提供的内存源码映射，不遍历仓库或读取文件。
- 解析：Python AST生成类、函数、异步函数及直接词法 contains 边；绝对和相对导入只连接仓库内模块文件，重复边确定性去重。
- 失败处理：语法错误、缺失/外部导入、自导入和重复限定名返回有界结构化 issue；非法路径、非Python、非文本、过大输入、模块歧义和图规模超限明确拒绝。
- 隐私与范围：结果不保存源码或AST；不实现 calls/inherits/tests 边，不接入索引或Retriever，不产生检索质量收益声明。
- 测试：节点/构图定向53 passed；节点/构图/Indexer相关回归59 passed；全量524 passed、4 skipped；`git diff --check`通过。
- 下一任务：`GRAPH-003`。

### GRAPH-003 完成记录

- 状态：`DONE`
- 日期：2026-08-29
- 修改文件：`rag/code_graph.py`、`rag/code_graph_builder.py`、`tests/test_code_graph_builder.py`、`tests/test_code_graph_relations.py`、README和项目管理文档。
- calls语义：调用所在file/class/function指向静态唯一确定的函数、类或方法；支持同文件词法符号、递归、`self`/`cls`、直接导入别名、模块属性和类构造调用，重复调用只保留一条结构边。
- inherits语义：子类指向同文件或明确导入的仓库内基类，支持简单模块属性、多继承和下标泛型基类。
- 保守边界：参数、赋值、外部导入及global重绑定会阻止猜测；函数局部导入不泄漏；动态调用/基类及无法解析引用只生成有界issue。lambda、推导式和动态分派不在简单解析范围。
- 契约：递归calls允许自环；contains/imports/inherits继续拒绝自环。结果仍不包含源码或AST。
- 范围：不实现tests边，不接入Indexer或Retriever，不运行图质量评测或宣称检索收益。
- 测试：节点/结构构图定向76 passed；节点/结构构图/Indexer相关回归82 passed；全量547 passed、4 skipped；`git diff --check`通过。
- 下一任务：`GRAPH-004`。

### GRAPH-004 完成记录

- 状态：`DONE`
- 日期：2026-08-29
- 修改文件：`rag/code_graph.py`、`rag/code_graph_builder.py`、`tests/test_code_graph_builder.py`、`tests/test_code_graph_tests_mapping.py`、README和项目管理文档。
- 识别：只从默认pytest文件名`test_*.py`/`*_test.py`收集顶层`test_*`函数和顶层`Test*`类的`test_*`方法；异步测试和Windows规范化路径同样支持。
- 映射：tests边由测试节点指向测试支持文件外、通过calls可达的生产函数/类/方法；pytest文件及`test/`、`tests/`目录内helper可穿透最多8层，循环有visited去重，总映射步数硬限100万。
- fixture：识别同文件明确导入的`pytest.fixture`及别名、参数依赖、fixture链和`autouse=True`；不按参数名猜测conftest或插件fixture。
- 保守边界：只导入未调用、动态对象调用、普通文件中的同名函数、普通类测试方法及嵌套伪测试均不构tests边；同测试文件目标视为helper而非生产目标。
- 范围：仍为纯内存构图，不读取覆盖率、测试结果、冻结数据或文件系统；不接入Indexer/Retriever，不宣称检索收益。
- 验证：节点/五类结构边定向103 passed；节点/构图/Indexer相关回归109 passed；全量574 passed、4 skipped；`git diff --check`通过。
- 真实源码smoke：仅加载`rag/`与`tests/`的56个Python文件，生成867个节点和408条tests边；仅证明可构建性和规模，不是质量评测。
- 下一任务：`GRAPH-005`。

### GRAPH-005 完成记录

- 状态：`DONE`
- 日期：2026-08-29
- 修改文件：`rag/code_graph_expansion.py`、`tests/test_code_graph_expansion.py`、README和项目管理文档。
- 映射：有序种子Chunk按“精确符号、最小包含符号、同文件节点”保守映射；邻居符号按精确Chunk、最小包含Chunk或未来拆分Chunk映射，方法可回落到当前Indexer生成的类Chunk，文件邻居映射该文件全部可用Chunk。
- 遍历：支持 outgoing/incoming/both及显式边类型过滤；默认双向可从生产符号反向找到调用者或tests节点。递归calls在both模式只输出一次，候选记录种子排名、节点、边和遍历方向。
- 阶段边界：结果只含Chunk引用和结构溯源，不含query、document、源码或AST；不同种子/边命中的重复Chunk有意保留，评分、上下文预算和去重留给GRAPH-006。仅设硬安全上限，超限明确失败而不静默截断。
- 范围：纯内存逻辑，不读取索引、文件、冻结数据或正式结果；未接入Indexer/Retriever，默认检索行为不变，不宣称质量或泛化收益。
- 验证：一跳扩展定向24 passed；代码图/Indexer/Retriever相关回归147 passed；全量598 passed、4 skipped；`git diff --check`通过。
- 下一任务：`GRAPH-006`。

### GRAPH-006 完成记录

- 状态：`DONE`
- 日期：2026-08-29
- 修改文件：`rag/code_graph_context.py`、`tests/test_code_graph_context.py`、README和项目管理文档。
- 评分：固定未调参v1策略按边类型、方向和种子排名相乘；calls/inherits/tests/imports/contains权重依次为`1.0/0.9/0.8/0.65/0.5`，outgoing/incoming为`1.0/0.9`，种子排名使用`(k+1)/(k+rank)`且`k=10`。调用方可显式传入同契约自定义策略。
- 去重：按稳定Chunk UID分组，同UID多条路径不累加分数，采用最高分证据并记录`evidence_count`；UID相同但文件或行区间冲突时明确拒绝。等分时按token成本、种子排名和UID稳定排序，输入顺序不影响结果。
- 预算：调用方必须提供精确`uid → token_count`，纯逻辑层不以字符数猜测token；支持总预算、已由种子/系统占用的reserved tokens和最大Chunk数。先评分去重，再按分数贪心装入；装不下的高分Chunk会跳过并继续尝试较小候选，结果记录预算与Chunk上限遗漏数且永不超限。
- 边界：选择结果只含Chunk引用、token计数、分数组件和最佳结构证据，不含query、document、源码或AST；固定权重未经开发集或冻结集调参。未接入Indexer/Retriever，未运行正式评测，不宣称质量收益。
- 验证：结构评分/预算/去重定向50 passed；代码图/Indexer/Retriever相关回归197 passed；全量648 passed、4 skipped；`git diff --check`通过。
- 下一任务：`GRAPH-007`。

### GRAPH-007 完成记录

- 状态：`DONE_WITH_GAP`
- 日期：2026-08-29
- 修改文件：`rag/code_graph_evaluation.py`、`tests/test_code_graph_evaluation.py`、`.rag-eval/codepilot-graph-cross-module-v1.{json,manifest.json}`、`.rag-eval/graph-cross-module-validation-2026-08-29.{json,md}`、README和项目管理文档。
- 冻结：20条cross_module查询、52个required标签，其中32个跨文件目标均由种子Chunk沿真实一跳calls边可达；与开发集规范化query重合为0。检索前冻结数据集SHA`adc8e9bb…dfcc`和完整策略SHA`ad354bde…597b`，结果未回调查询、标注、权重、预算或5+5合并。
- 策略：固定Hybrid Top-10对比“前5个固定命中+最多5个生产Python outgoing calls邻居+固定命中回填”；图预算2048本地embedding-tokenizer token、最多5个Chunk，排除测试种子/目标、文档和已在基线Top-10的UID。未启用Rerank或自适应Router。
- 质量：固定Hybrid与图增强Recall@10 `0.567500/0.656667`，点差`+0.089167`，95% CI `[-0.045833,+0.222500]`；7题改善、3题下降、10题持平。MRR点差`-0.010476`，95% CI `[-0.029286,0.000000]`。只说明同仓库内部专项点估计，不是独立外部泛化收益。
- 性能与污染：图阶段P95额外耗时`32.754ms`，超过10ms门槛；新增88个Chunk中标注相关9、无关79，单题无关新增P95为5，超过门槛3；测试与文档新增均为0。总体验收未完全通过。
- 运行边界：强制离线，使用已有本地模型和增量索引；未调用付费API。结果不保存query、Top结果、document或源码。图路径仍未接入产品Retriever，普通查询运行时不变。
- 验证：GRAPH-007定向27 passed；代码图/Indexer/Retriever/Evaluate相关回归232 passed；全量675 passed、4 skipped；`git diff --check`通过。
- 下一任务：M6 `MODEL-001`；图上线前仍需邻接预索引、意图相关评分和更严格候选控制。

## 10. 模型服务任务

- [x] `MODEL-001` `DONE`：定义 Rerank Worker 状态机。
- [x] `MODEL-002` `DONE`：有界请求队列。
- [x] `MODEL-003` `DONE`：推理超时和队列满回退。
- [x] `MODEL-004` `DONE`：连续失败熔断和冷却探测。
- [x] `MODEL-005` `DONE`：后台预热。
- [x] `MODEL-006` `DONE`：指标与健康状态。
- [x] `MODEL-007` `DONE`：并发压力和死锁测试。

### MODEL-001 完成记录

- 状态：`DONE`
- 日期：2026-08-30
- 修改文件：`rag/rerank_worker_state.py`、`tests/test_rerank_worker_state.py`、README和项目管理文档。
- 契约：定义`UNLOADED/LOADING/READY/DEGRADED/FAILED`五阶段和固定事件；加载成功/失败、推理降级/恢复、连续失败到FAILED、重载、恢复探测和显式卸载均有唯一目标阶段，非法转换使用稳定错误码拒绝。
- 状态：每次合法事件返回新的不可变slots快照并递增revision；快照校验初始状态和`last_event → phase`自洽，可JSON序列化。reason code只接受最长64字符的snake-case标识符，不保存query、异常消息、源码或模型输出。
- 边界：模块不导入模型、config、线程或队列，不读时钟和文件；没有接入现有`reranker.py`/Retriever。队列满和超时属于后续请求级回退，不在本任务改变模型健康状态。
- 验证：状态机定向22 passed；状态机/Reranker/RerankPolicy/Retriever相关回归94 passed；全量697 passed、4 skipped；`git diff --check`通过。
- 实现提交：`e42d28c`。
- 遗留问题：当前只是生命周期契约，不提供线程安全执行器、排队、超时、熔断计数、预热或指标，默认Rerank运行时行为不变。
- 下一任务：`MODEL-002`有界请求队列。

### MODEL-002 完成记录

- 状态：`DONE`
- 日期：2026-08-30
- 修改文件：`rag/rerank_request_queue.py`、`tests/test_rerank_request_queue.py`、README和项目管理文档。
- 队列：固定容量、线程安全FIFO；生产者只使用非阻塞`offer`，不会因模型繁忙继续堆积调用线程。接受、满载和关闭分别返回`rerank_queue_accepted/full/closed`及操作后的size/capacity。
- 生命周期：消费者可阻塞或使用有界等待取出最早请求；`close`幂等，立即拒绝新请求、保留并允许排空已有请求，同时唤醒空队列上的等待消费者。显式`None`被拒绝，不能偷偷充当关闭哨兵。
- 有界与隐私：容量限制为1～10000，显式等待限制为0～86400秒；不可变快照仅含size/capacity/closed，不序列化不透明请求、query或候选内容。队列本身不导入模型、config、Retriever或MODEL-001状态机。
- 验证：队列定向24 passed；队列/状态机/Reranker/RerankPolicy/Retriever相关回归118 passed；全量721 passed、4 skipped；`git diff --check`通过。
- 实现提交：`fe2daad`。
- 遗留问题：尚未定义运行时请求/结果信封，未接入Reranker/Retriever；满载只产生结构化拒绝，不执行RRF回退；消费者等待上限不是推理超时。
- 下一任务：`MODEL-003`推理超时和队列满回退。

### MODEL-003 完成记录

- 状态：`DONE`
- 日期：2026-08-30
- 修改文件：`rag/rerank_worker.py`、`rag/reranker.py`、`rag/retriever.py`、`config/settings.yaml`、`tests/test_rerank_worker.py`、`tests/test_reranker.py`、`tests/test_config.py`、README和项目管理文档。
- Worker：单daemon线程消费MODEL-002有界FIFO；首个实际请求在Worker中惰性加载模型并驱动MODEL-001的LOADING/READY/FAILED状态，推理失败进入DEGRADED、后续成功回到READY。默认队列容量8，不为每个请求创建推理线程。
- 超时：每个已接受请求默认30秒调用方deadline，含队列等待；到期请求立即以`rerank_timeout`返回。未开始的排队任务通过Future取消并跳过；已运行的Python/PyTorch调用不能安全强杀，会在单Worker内完成后再处理下一项。
- 回退：Retriever显式rerank路径接入Worker；queue full/closed、load error、inference error、timeout使用固定原因码，返回原候选Top-N并保留`rrf_rank`，metadata记录`rerank_fallback`和原因。任意第三方异常文本或伪造reason不会写入warning/metadata。
- 隔离：Worker对候选和metadata做副本，超时后迟到推理不能异步改写已返回RRF结果。状态/队列快照仍不保存query、候选、异常文本或模型输出。
- 验证：Worker/Reranker定向26 passed；Worker/Queue/State/Reranker/Retriever/Config相关回归94 passed；全量741 passed、4 skipped；`git diff --check`通过。未加载真实模型、未联网、未运行正式评测或付费API。
- 实现提交：`6ae1ea0`。
- 遗留问题：deadline无法停止正在执行的底层推理，长时间卡死会占住唯一Worker并最终填满队列；连续失败阈值、冷却窗口和单次恢复探测尚未执行。
- 下一任务：`MODEL-004`连续失败熔断和冷却探测。

### MODEL-004 完成记录

- 状态：`DONE`
- 日期：2026-08-30
- 修改文件：`rag/rerank_worker.py`、`rag/reranker.py`、`config/settings.yaml`、`tests/test_rerank_worker.py`、`tests/test_reranker.py`、`tests/test_config.py`、README和项目管理文档。
- 计数：真实模型加载或推理失败才递增连续失败；任一完整请求成功清零。默认阈值3，硬上限100。queue full/closed、调用方deadline和熔断快速拒绝不计数；超时后后台推理若最终真实失败，仍按模型结果计数。
- 开路：达到阈值后记录单调时钟开路点、Worker状态进入FAILED；默认60秒冷却，硬上限86400秒。开路请求立即返回`rerank_circuit_open`，阈值触发前已排队但未执行的旧请求在执行前复核并快速拒绝。
- 探测：冷却结束只允许一个请求将状态切到DEGRADED并占有探测资格，其他并发请求返回`rerank_recovery_probe_in_progress`。成功回READY并关闭熔断；加载/推理失败回FAILED并重新开始完整冷却。尚未执行就被队列拒绝或取消的探测释放资格且不增加失败。
- 快照：不可变、JSON-ready的circuit快照只含closed/open/half_open、连续失败、阈值、冷却及剩余时间、探测占用，不含query、候选、异常或模型输出。
- 验证：Worker/Reranker定向49 passed；Worker/Queue/State/Reranker/Retriever/Config相关回归117 passed；全量764 passed、4 skipped；`git diff --check`通过。未加载真实模型、未联网、未运行正式评测或付费API。
- 实现提交：`4fdeee5`。
- 遗留问题：熔断不能终止已经卡死的底层推理；进程内单Worker永久阻塞时只能让后续请求经deadline/queue full回退。尚无后台预热、指标、健康接口或压力证据。
- 下一任务：`MODEL-005`后台预热。

### MODEL-005 完成记录

- 调度：新增不可变、JSON-ready且不含内容的`RerankWarmupResult`；Rerank启用且`background_warmup=true`时，服务启动向现有单daemon Worker非阻塞投递一次预热，不创建第二条模型执行线程。重复调度、已加载、队列满、Worker关闭和熔断均返回固定reason code。
- 生命周期：预热与查询共享同一有界FIFO、加载状态和熔断计数。模型加载失败只把Worker置为FAILED并记录真实失败，不向API启动线程传播；服务退出停止接收请求并非阻塞关闭Worker，尚未执行的预热会被取消。
- 默认与边界：配置默认允许预热，但`rag.reranker.enabled=false`仍是产品默认，因此常规启动不会创建Worker或加载模型。预热不下载模型，仍遵守`local_files_only`；未加载真实模型、未联网、未运行正式评测或付费API。
- 验证：Worker/Reranker/Config/Server定向70 passed、3 skipped；Retriever/Queue/State相关回归60 passed；全量771 passed、4 skipped；`git diff --check`通过。
- 实现提交：`d3637d8`。
- 遗留问题：后台预热只消除启用后的首请求加载等待，不改变CPU推理成本，也不能强杀已卡死的PyTorch调用；Worker状态、队列、熔断、预热结果尚未接入健康与指标输出。
- 下一任务：`MODEL-006`指标与健康输出。

### MODEL-006 完成记录

- 运行时快照：新增不可变Worker与Reranker健康快照，输出enabled/loaded、五阶段、revision、最后事件、固定reason code、队列size/capacity/closed、熔断phase/连续失败/剩余冷却、预热占用和线程存活；未创建Worker时只读配置并返回unloaded，不触发模型加载。
- 健康接口：`/health`保留顶层`status=ok`，因为Rerank是可回退的可选子系统；新增`reranker`子对象报告上述状态。响应不含模型名、缓存路径、query、候选、异常消息或模型输出，FAILED状态也只保留稳定reason code。
- 指标：新增线程安全、进程内累计的`rag_retrieval_latency_ms`、`rag_rerank_latency_ms`、`rag_rerank_queue_size`、`rag_rerank_fallback_total`、`rag_rerank_timeout_total`、`rag_model_load_seconds`和`rag_search_mode_total`。延迟/加载用Prometheus summary的count/sum，mode与fallback reason严格映射到固定集合，未知值归一到`unknown`/`rerank_unexpected_error`。
- 验证：Metrics/Worker/Reranker/Retriever/Server定向86 passed、3 skipped；全量781 passed、4 skipped；`git diff --check`通过。未加载真实模型、未联网、未运行正式评测或付费API。
- 实现提交：`07a9fbf`。
- 遗留问题：指标仅为单进程内存累计，不跨多Worker聚合，也没有直方图分位数；本轮不是负载测试，尚未证明高并发下无死锁、队列上限和deadline行为稳定。
- 下一任务：`MODEL-007`并发压力和死锁测试。

### MODEL-007 完成记录

- 压力夹具：新增`rag.rerank_stress`，只使用fake loader和sleep推理，不读检索/冻结数据、不加载模型、不联网、不写结果文件。输入请求数、并发、容量、操作时长、deadline和join上限均有硬边界；内容最小化JSON报告记录终态计数、线程退出、关闭结果、队列峰值、单Worker线程数、吞吐及调用/完成P50/P95/最大延迟。
- 竞争覆盖：11项定向测试覆盖持续压力、队列过载、deadline饱和、5轮重复创建/关闭、submit/close/runtime snapshot竞争、32并发冷却恢复只放行一个探测、64并发Retriever queue-full/timeout回退保持原始`rrf_rank`，以及参数硬边界。调用线程和监控线程均为daemon，检测失败可报告而不会让pytest自身永久挂起。
- 实测持续场景：1000请求、并发8、容量8、fake操作2ms、deadline250ms；1000成功、0回退，完成吞吐`380.208 req/s`，调用与完成P95均`23.676ms`，最大队列7，单Worker线程，全部线程与关闭完成，无死锁。
- 实测过载场景：1000请求、并发64、容量8、fake操作2ms；8成功、992个`rerank_queue_full`快速拒绝，最大观测队列6且未超过容量，全部终结并关闭，无死锁。终态吞吐包含快速拒绝，不等同模型推理吞吐。
- 实测deadline场景：256请求、并发64、容量8、fake操作30ms、deadline5ms；248个queue-full、8个timeout，0未完成调用，监控和Worker关闭成功，无死锁。
- 验证：MODEL-007定向11 passed；Worker/Queue/State/Reranker/Retriever/Metrics/Server相关143 passed、3 skipped；全量792 passed、4 skipped；`git diff --check`通过。
- 实现提交：`85b88e9`。
- 边界：本轮验证Python控制面并发和回退不变量，不加载真实Cross-Encoder，不能替代生产硬件上的真实模型吞吐/P95、进程崩溃恢复或永久卡死推理隔离。底层运行中PyTorch仍不能强杀。
- 下一里程碑：`M7`重复和独立评测。

## 11. M7重复与独立评测任务

- [x] `M7-001` `DONE`：冻结重复评测协议、预算和停止条件。
- [ ] `M7-002` `IN_PROGRESS_2_OF_3`：已完成正式r1/r2各40/40并暂停；r3待执行。
- [ ] `M7-003` `PLANNED`：建立不同仓库的小型独立冻结集。
- [ ] `M7-004` `PLANNED`：完成至少20%外部抽审和最终统计报告。

### M7-001 完成记录

- 冻结矩阵：固定评测提交`7917e004…db42`及Git tree`42fd3899…e981`、`agent-tasks-v1.json` SHA-256 `71caa70e…ac09`、20个A01～A20任务、Hybrid/Rerank、每任务条件3次，共120个新Agent运行。固定`deepseek-chat`、temperature 0.3、10轮上限、mutation_required和900秒Worker上限。
- 独立性：旧`agent-v2-transactional`固定在`daee3cc…`，与当前评测提交不同，明确禁止复用为重复样本。任务标签和test-v1不得修改；新仓库集及20%外部抽审分别留给M7-003/004。
- 预算与停止：提出50 USD硬上限、最多120次Agent/1200模型轮次/108000 Worker秒，每40次暂停复核；费用封顶、授权缺失、任务/Git树漂移、结果路径冲突、连续3次Worker或API失败均停止。50 USD只是协议上限，不是用户付费授权。
- 防护：新增只读`rag.agent_repeat_protocol`，验证协议/任务双哈希、冻结commit/tree中的任务blob、固定矩阵、禁止覆盖的三轮结果路径及独立授权文件。当前manifest为`frozen_unfunded`，授权文件和三个结果目录均不存在；`--require-authorization`稳定拒绝为`m7_authorization_missing`。
- 验证：Protocol定向13 passed；Agent Eval/Report/Metrics/Evaluate/Trace相关58 passed；全量805 passed、4 skipped；protocol `--check-pristine`和`git diff --check`通过。校验器不读取结果内容；本轮未修改或覆盖旧正式结果，未创建Worktree/新结果/授权文件，未加载模型、未联网、未调用付费API。
- 实现提交：`0176ef2`。
- 下一任务：`M7-002`，但必须先获得用户对独立授权文件和不高于10 CNY费用上限的明确授权。

### M7-001 低成本v2修订

- 用户选择：正式M7统一使用`qwen3.7-flash`；`glm-4.7-flash`只用于A01/A02×Hybrid/Rerank最多4项免费兼容预跑和排障，预跑结果禁止并入正式统计。
- 冻结基线：实现提交`cf3be67…`、Git tree`3823ceb3…`、任务SHA不变；v1原样保留，v2使用新协议、manifest、授权路径、三轮run-id及结果目录。
- 成本与审计：按当前32K内千问Flash价格冻结0.2/0.8 CNY每百万输入/输出Token，建议硬上限10 CNY；授权前必须复核价格。每轮报告记录provider usage，出现未计量轮次立即停止。
- 路由防护：新增智谱OpenAI兼容路由；显式模型不可用时稳定失败，不再静默回退到其他模型。智谱和千问均启用流式usage请求，评测报告汇总输入、输出、总Token和未计量轮次。
- 当前边界：未创建`.env`密钥、v2授权、结果目录或Worktree，未联网、未调用免费或付费API，未修改旧正式结果和冻结任务。

### M7-v2 GLM免费预跑记录

- 范围：按协议只执行A01/A02×Hybrid/Rerank四项，run-id为`m7-agent-repeat-v2-glm-preflight`；未运行千问正式矩阵。
- 结果：1/4 Oracle成功；Hybrid 0/2、Rerank 1/2。A01-Hybrid触发`429/1305`限流，A02-Hybrid触发`400/1214 messages参数非法`；A01-Rerank修复和测试成功但最终状态failed，A02-Rerank耗尽10轮且未编辑。
- 审计：28/28模型轮次计量完整，输入112,478、输出2,672、未计量0；四个Worker退出码0，manifest completed，无Worker/清理失败。Agent耗时合计1056.161秒，runner墙钟约27分47秒。
- 判定：智谱端点、工具调用和usage基本兼容，但免费端点稳定性和消息兼容未达到正式评测要求；维持“只作预跑排障、禁止并入正式统计”的v2决策。
- 边界：没有读取或提交`.env`，没有创建千问费用授权或正式结果目录，未覆盖旧结果；免费资格和账单以智谱控制台为准。

### M7-002 千问正式r1记录

- 授权：用户明确授权正式千问评测，费用硬上限10 CNY；独立授权文件绑定v2 protocol SHA并复核北京地域目录价。
- 结果：`m7-agent-repeat-v2-qwen-r1`完成40/40；Hybrid 5/20（25%）、Rerank 3/20（15%），合计8/40。只有一轮，尚不能计算pass@3或宣称稳定差异。
- 用量：352/352实际模型回合provider usage完整；输入1,692,644、输出71,182 Token，目录价估算0.395474 CNY；无Agent API或清理失败。
- 夹具缺陷：A06两条件在模型调用前worker failure；冻结mutation旧文本在目标文件中出现2次，无法唯一替换。两份零模型回合报告没有usage对象，故条件汇总`complete=false`，但未计量模型回合仍为0。
- 中断审计：首次执行因FreeTierOnly连续403在25/40停止，独立归档且不入正式统计；关闭“免费额度用完即停”并验证后从空r1目录重跑。
- 暂停：已在第一轮40项后暂停，未执行r2/r3；详见`.rag-eval/qwen-r1-2026-08-31.md`。

### M7-002 千问正式r2记录

- 结果：`m7-agent-repeat-v2-qwen-r2`完成40/40；Hybrid 4/20（20%）、Rerank 4/20（20%），合计8/40；成对结果为both success 3、Hybrid-only 1、Rerank-only 1、both failed 15。
- 两轮中间统计：r1+r2 Hybrid 9/40（22.5%）、Rerank 7/40（17.5%），合计16/80；两条件都各有6/20个任务在前两轮至少成功一次。r3尚未执行，不能报告最终pass@3或稳定条件差异。
- 用量：r2的334/334实际模型回合provider usage完整，输入1,629,192、输出78,076 Token，目录价估算0.388299 CNY；正式r1+r2合计686/686回合、0.783773 CNY。
- 故障：A06两条件再次因冻结mutation文本不唯一在模型调用前形成2个worker failure；无Agent API或清理失败。A16 Rerank一次本地Cross-Encoder失败后按设计回退RRF，worker正常完成。
- 暂停：已在第二轮40项后按协议暂停，未创建或执行r3；详见`.rag-eval/qwen-r2-2026-09-01.md`。

## 12. 每个任务完成时填写

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
