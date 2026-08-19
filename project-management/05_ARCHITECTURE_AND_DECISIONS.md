# CodePilot 架构与设计决策记录

更新时间：2026-08-19

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
- 客观证据：状态只由真实工具结果更新；事务编辑解析 JSON，shell 测试解析稳定返回码。mutation 任务必须具有最新字节编辑、对应 revision 的返回码0测试和其后的非空 `git_diff` review 才能 COMPLETE，模型最终文字本身不是完成证据。
- 隔离：每个 `AgentSession.run` 用户轮次创建新状态；会话历史和模型连接仍可复用，状态计数与转移不跨任务污染。
- 预算：discovery/inspect/edit/verify/recovery 分预算可配置；Agent 在执行前拒绝超预算工具并返回结构化 ToolMessage，总 `max_iterations` 保持硬上限。恢复预算在每次进入 RECOVER 前生效。
- 安全：状态仅保存阶段、计数、路径、错误码、返回码和最多500条转移，不保存工具参数、源码、模型上下文或 shell 输出。
- 代价与风险：`auto` 仅能在发生编辑尝试后确定性切换为 mutation，无法识别模型从未尝试编辑的修改请求；调用方应对已知修改任务显式使用 `mutation_required`。
- 验证：确定性 fake tool/model 测试覆盖合法/非法转移、隔离、恢复、预算、revision 证据门槛、Diff Review、脱敏、并发和多 tool call 配对；全量182 passed、4 skipped。

### ADR-013：恢复与完成控制使用确定性决策和临时指令

- 日期：2026-08-19
- 状态：`ACCEPTED`
- 背景：模型可能在编辑/测试失败后盲目重试，也可能在缺少真实编辑、测试或 review 时提前回答；一条 AIMessage 还可能包含多个 tool call，不能破坏消息配对。
- 决策：编辑 JSON 错误码和测试返回码通过纯函数映射到 `RecoveryDecision`；不可恢复安全/一致性错误直接 FAILED，可恢复错误受 recovery budget 约束。缺少完成证据由 `CompletionDecision` 指向 EDIT、VERIFY 或 REVIEW，不在第一次提前回答时立即 FAILED。
- 指令：恢复/完成 directive 由固定模板生成，最长500字符，只在下一次模型调用时插入到系统提示之后，不永久写入历史，也不复制 shell 输出、源码或异常文本。
- 证据版本：真实字节变化推进 `mutation_revision`，并清空 `verified_revision`/`reviewed_revision`；测试返回码0绑定当前 revision，同时作废先前 review；成功 review 要求最新验证后实际调用 `git_diff` 且结果非空、非错误。
- ToolMessage：执行、工具失败、预算拒绝和终态拒绝统一逐 call 产生 ToolMessage；控制 SystemMessage 只在一轮所有 tool call 配对完成后进入下一次请求。
- 安全：API 继续默认拒绝 `run_shell`。显式 mutation 在无安全验证通道时以 `verification_unavailable` 失败，不伪造测试证据；MCP 默认危险工具仍返回 `isError=true`。
- 代价：当前没有统一 `allowed_files`，仅记录有界 reviewed paths 并检测明显的 modified/reviewed 路径不相交；完整范围策略留给 M3 Trace/新版任务协议。
- 验证：fake model/fake tool 集成轨迹、真实临时 Git 仓库 diff 格式、CLI/API/MCP 兼容与全量回归；未调用付费模型。

### ADR-014：QueryFeatures 使用有界确定性词法启发式

- 日期：2026-08-19
- 状态：`ACCEPTED`
- 背景：M4 需要先客观描述查询形态，再由后续任务生成检索计划；本阶段不能提前引入路由、调权或第二个模型分类器。
- 决策：新增独立不可变 `QueryFeatures` 和纯函数 `extract_query_features`。字段覆盖查询长度、词元数、标识符/自然语言比例、中英文比例、混合语言、路径、配置点号键、错误/异常、堆栈痕迹、跨模块意图和固定 reason codes。
- 确定性规则：标识符来自点号标识符、路径/文件引用、函数调用、snake_case、camelCase、类名和大写常量；自然语言是未归为标识符的字母/CJK 词元；路径、配置键、异常名、错误词和常见 Python/Java 堆栈帧使用预编译正则；跨模块由显式关系短语、至少两个不同文件引用，或多个代码引用加关系词触发。
- 语义边界：所有分类都是可解释启发式，不是语义真值或置信概率；跨模块、配置键和自然语言判断允许保守的误报/漏报。ROUTE-001 不产生 `RetrievalPlan`，也不改变固定 RRF 或 Rerank 默认值。
- 隐私与有界性：结构不保存 query、检索结果、源码或模型内容。只分析前16,384个 Unicode 码点，保留完整字符数和截断 reason code；正则不作用于无限输入，比例空分母定义为0.0并统一限制到 `[0.0, 1.0]`。
- 依赖：模块只使用 Python 标准库，不读文件系统、不联网、不调用 LLM、Embedding、Chroma 或 Reranker。
- 验证：32个本地确定性测试覆盖任务样例、空白、Unicode、超长输入、序列化、不可变性、重复调用和禁止模型导入；全量243 passed、4 skipped。

### ADR-015：RetrievalPlan 先定义严格数据契约再接入路由

- 日期：2026-08-19
- 状态：`ACCEPTED`
- 背景：ROUTE-003～005 将分别增加置信信号、规则路由和 RerankPolicy；如果在计划结构未稳定前直接接入运行时，会把数据契约、决策规则和行为变化混在同一改动中。
- 决策：ROUTE-002 只新增不可变 `RetrievalPlan`。它保存 BM25/Vector 权重、`rrf_k`、候选数、文档/Rerank 开关、有界解释和稳定 reason codes，并用 schema version 输出 JSON-ready 字典；不保存 query 或检索结果。
- 数值边界：权重必须有限、非负且不能双零；`rrf_k` 为1～10,000，候选数为1～100。严格拒绝 NaN、无穷、布尔伪装数值和整数伪装布尔值，避免序列化后出现跨语言歧义。
- 解释边界：人类可读原因必须非空、单行且最多500字符；reason codes 最多16个、唯一并符合小写标识符格式。调用方不得把完整 query 放入原因；结构性有界防止无限文本，但语义隐私仍由未来规则路由器使用固定模板保证。
- 兼容基线：模块提供与当前默认 `bm25_weight=2.0`、`vector_weight=0.25`、`rrf_k=10`、候选30、排除文档、关闭 Rerank 完全一致的不可变计划，并用 `config.DEFAULTS` 同步测试防止漂移。
- 运行时边界：`rag.retriever` 不导入该模块，现有检索仍只读取原配置；ROUTE-002 不选择计划、不动态调权、不启用 Rerank。
- 依赖与验证：模块只依赖标准库；56个定向测试覆盖契约、边界、序列化、不可变性、默认同步和禁止模型/配置运行时导入；全量299 passed、4 skipped。

### ADR-016：检索置信只保存可解释原始信号，不合成概率

- 日期：2026-08-19
- 状态：`ACCEPTED`
- 背景：ROUTE-004 需要利用两路排名关系决定计划，但 RRF 分数和不同向量模型的 score 没有统一概率语义；在开发集校准前合成单一“置信度”会制造不可验证的精确感。
- 决策：`calculate_retrieval_confidence` 只消费 query 与调用方提供的 Vector/BM25 排名，返回不可变 `RetrievalConfidenceSignals`。输出包含固定-K重合率、可缺省 Top-1 一致性、标识符覆盖率、可缺省原始 Vector margin 和文件多样性，不产生总分、概率或高/低分类。
- 排名定义：每路只取前K并按规范化 UID 去重；重合率以固定K为分母，稀疏结果不会被误报为满重合。候选集合是两路唯一 UID 并集，文件多样性以唯一文件数除以该候选数。
- 标识符定义：QueryFeatures 模块新增只供瞬时计算的有界标识符提取接口；最多返回256个大小写折叠的代码标识符。覆盖只检查候选有界文件名和文档中的精确代码词元，输出只保存数量和比例，不保存标识符内容。
- Margin 定义：使用当前 Vector 命中 higher-is-better 分数的原始 Top-1减Top-2；不足两个有限分数时为 `None`，逆序时钳制为0并添加稳定异常 reason code。该数值不是概率，不能跨模型或未经校准的索引直接比较。
- 有界与隐私：K为1～100；query 分析16,384码点；候选文件名和文档字段各扫描最多20,000字符。结果不保存 query、文档、UID列表、标识符列表或检索对象。
- 运行时边界：模块不执行检索、不读取 config、不导入 Retriever/Reranker/Plan；ROUTE-003 不改变固定 RRF 或默认 Rerank 策略。
- 验证：40个定向测试覆盖满/部分/零重合、缺失排名、标识符覆盖、margin 异常、文件多样性、去重、类型/长度边界、不可变与模型隔离；全量339 passed、4 skipped。

### ADR-017：v1 规则路由采用显式优先级并延后 Rerank 决策

- 日期：2026-08-19
- 状态：`ACCEPTED`
- 背景：ROUTE-001～003 已提供查询形态、计划契约和原始排名信号；ROUTE-004 需要把它们映射为计划，同时避免把 Rerank 的高延迟决策混入尚无预算模型的规则中。
- 决策：新增无状态纯函数规则路由器，输入 `QueryFeatures` 和可选 `RetrievalConfidenceSignals`，输出经过 ROUTE-002 校验的 `RetrievalPlan`。优先级依次为检索器可用性回退、跨模块、中英混合、自然语言、精确代码、兼容基线；低重合 Top-1 分歧可把查询规则改为平衡扩展计划。
- 规则表：兼容默认 `2.0/0.25`，精确代码 `2.5/0.25`，自然语言 `0.75/1.5`，混合/跨模块/分歧 `1.0/1.0`；`rrf_k=10`，普通候选30、混合40、跨模块或分歧50。单路缺失时只给可用检索器权重1，双路皆空回到默认。
- 文档意图：QueryFeatures 增加确定性、有界的 documentation/README/docs/guide/manual/tutorial 及中文文档短语识别；只有显式命中才打开 `include_docs`，一般“解释功能”不会打开文档。
- Rerank 边界：ROUTE-004 返回的每个计划都强制 `rerank=false`，并附 `rerank_deferred_to_policy`。跨模块分歧只形成候选信号，不在缺少 ROUTE-005 延迟预算时直接承担 CPU P95 风险。
- 解释：人类原因由固定模板和稳定 reason codes 生成，不复制 query、原始 signal 文本或分数；Top-1 一致/分歧和标识符覆盖只作为启发式依据，不称为概率。
- 调参纪律：自然语言0.75、标识符0.5、低重合0.2等阈值是预先声明的 v1 工程常量，没有读取冻结集或正式结果选择。质量与阈值优化只能在 ROUTE-006 使用开发集进行。
- 运行时边界：Router 不读 config、不执行检索；Retriever 尚未导入 Router，默认产品行为不变。
- 验证：30个定向测试覆盖规则类别、优先级、文档意图、信号覆盖、单路回退、无候选、固定解释、类型契约、确定性及模型隔离；全量369 passed、4 skipped。

### ADR-018：RerankPolicy 使用显式资格门和调用方延迟估计

- 日期：2026-08-19
- 状态：`ACCEPTED`
- 背景：Cross-Encoder 在不同机器、模型和候选规模下成本差异显著；固定打开会破坏默认延迟目标，把一次本机测量直接写成通用规则也不可解释。
- 决策：新增独立纯函数 `decide_rerank`，输入已验证的 `RetrievalPlan`、`QueryFeatures`、可选置信信号、调用方延迟预算与成本估计，以及显式允许/模型可用标志；输出不可变 `RerankDecision` 和新计划，不执行计划。
- 资格门：启用必须同时满足调用方允许、模型可用、跨模块意图、双路结果存在、Top-1 分歧、固定-K重合率不高于0.2和估计成本不超过剩余预算。Top-1 一致且查询标识符完全覆盖的精确查询优先跳过；任何缺失证据均安全关闭。
- 延迟模型：`LatencyBudget` 将 `total_ms - elapsed_ms - reserve_ms` 钳制为非负剩余预算；`RerankCostEstimate` 由调用方提供固定成本与每候选成本。策略不读墙钟，也不把历史 CPU P95、冻结结果或特定硬件数字硬编码为通用事实。
- 有界与隐私：精排候选最多30；所有输入数值必须有限非负，总预算必须为正；结构只保存数值、布尔、计划和固定 reason codes，不保存 query、文档、hits 或模型输出。规则是保守启发式，不是置信概率或语义真值。
- 兼容与回退：拒绝时保持原计划权重、RRF、候选数和文档开关，仅返回 `rerank=false`；启用时只把候选数截到30并打开标志。Retriever 尚未导入策略，因此默认产品运行时仍是固定 Weighted RRF 且 Rerank 关闭。
- 调参纪律：0.2重合阈值和30候选上限沿用预声明保守规则，不宣称最优；ROUTE-006 只能使用开发集校准，禁止读取 test-v1 或正式结果选阈值。
- 验证：52个定向测试覆盖预算/成本边界、资格门、精确跳过、候选上限、策略重入、不可变/序列化、隐私、确定性、类型契约和模型隔离；相关回归274 passed，全量421 passed、4 skipped。

### ADR-009：结构图只做检索后扩展

- 状态：`PLANNED`
- 决策：未来 AST 图不替换 BM25/Vector，而是从检索种子做一跳扩展。
- 原因：避免建设昂贵且难验证的完整知识图谱，同时解决跨模块链路问题。

### ADR-010：结构化 Trace 不保存敏感内容

- 日期：2026-08-19
- 状态：`ACCEPTED`
- 决策：每个 `TaskExecutionState` 拥有独立 `TaskTrace`，记录有时间戳的迭代、阶段转移、review 和 completion decision，以及 Retrieval/Edit/Test 客观事件、已读取路径和实际变更路径。Trace 不保存 query、工具参数、模型上下文、源码、完整 diff 或 shell 输出；`.env` 路径替换为稳定占位符。
- 接口：`execution_state.snapshot()` 嵌入 Trace；评测 schema v2 新增可选 `execution_trace`，现有回调和必填字段不变。新报告另增 Oracle/Agent 终态分离字段和失败分析字段。
- 分析：共享纯函数层按固定优先级产生唯一 `failure_stage`、有界次要原因及 `code`/`environment`/`control` 域；worker/Worktree/模型通道失败优先归为环境，代码症状保留为次要原因。
- 聚合：离线报告生成十级任务漏斗；在线 TaskQueue 生成八级运行漏斗，Prometheus 使用固定 metric 和 label，Dashboard 只消费聚合数字，不加载原始 Trace 内容。
- 边界：旧报告缺少逐轮 Trace，只能使用原有编辑、测试和 worker 字段兼容分类；文本环境模式保持保守且可审计，不宣称绝对根因。
- 验证：覆盖完整 mutation、最后一轮 review 后无 completion response、敏感数据、长度边界、隔离、十类失败、环境优先、历史报告兼容、Server metrics 和 Dashboard；全量211 passed、4 skipped。

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
