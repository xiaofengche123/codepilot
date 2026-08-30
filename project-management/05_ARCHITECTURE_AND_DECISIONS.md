# CodePilot 架构与设计决策记录

更新时间：2026-08-30

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

### ADR-019：自适应参数只在专用开发集做有界族级网格选择

- 日期：2026-08-19
- 状态：`ACCEPTED`
- 背景：ROUTE-004 的预声明1:1平衡规则在当前内部代码库上把强 BM25 结果稀释；同时直接用冻结集或正式结果修正会破坏后续可信度。
- 隔离决策：`rag.retrieval_tuning` 只接受 basename 为 `codepilot-dev.json` 的输入，在读取前拒绝其他数据集和结果文件。CLI 只输出聚合分数/参数，不持久化 query、排名或逐题失败；本轮强制离线并使用 D 盘已有模型缓存。
- 搜索决策：每条开发查询的 Vector/BM25 Top-100只计算一次，再按 QueryFeatures 和 Top-10信号形成实际路由族。每族搜索6组权重、3个RRF k、3个候选数；选择优先级固定为 Recall@10、MRR@10、较小候选池、较小RRF k和声明顺序，避免隐式人工挑数。
- 参数决策：自然语言/跨模块采用 `2.5/0.25, k=10, candidates=30`；中英混合采用 `1.5/0.5, 10, 30`；低重合 Top-1分歧采用 `2.0/0.5, 10, 40`。未出现在最终路由族中的精确代码和兼容基线保留原值；Rerank资格阈值和执行上限不变。
- 证据：未校准规则开发集 Recall@10/MRR@10 为0.652778/0.543373，固定 RRF 为0.780556/0.591005，族级选择为0.788889/0.593056。相对固定方案差值很小且来自同一调参集，只能证明选择过程可复现，不能证明泛化提升。
- 运行时边界：Retriever 不导入 Router 或 Tuning，默认产品仍读取固定配置；本 ADR 没有证明400ms P95、Rerank调用比例、外部自然语言质量或 Agent成功率。
- 下一验证：参数在 ROUTE-006 后冻结。ROUTE-007 必须建立此前未参与选择的新独立集，ROUTE-008 才进行固定/Vector/自适应比较；不得根据新结果回改该验证集答案。
- 验证：25个调参测试覆盖数据集文件名隔离、搜索空间、数值边界、确定性选择、分族、聚合隐私和模型隔离；三层定向107 passed、相关回归299 passed、全量446 passed、4 skipped。

### ADR-020：独立验证集与待测路由参数在评分前双重冻结

- 日期：2026-08-28
- 状态：`ACCEPTED`
- 数据决策：ROUTE-007 新建 `codepilot-validation-v1.json`，共50条，五类各10条。它不参与 ROUTE-006 的参数搜索，与开发集做规范化精确查重，但仍是同仓库内部标注集，不宣称外部独立性。
- 隔离决策：`rag.retrieval_validation` 只接受指定验证集文件名；模块不导入 Retriever 或 Evaluator。ROUTE-007 只校验结构、类别、ID、chunk 标签和开发集重合，不计算或持久化任何排名与指标。
- 双重冻结：manifest 除数据集、开发集和语料哈希外，还保存完整族级 BM25/Vector/RRF/candidate 参数画像。`--check` 要求验证集哈希和当前路由画像哈希同时一致，防止看完答案后改标签或改策略。
- 使用规则：ROUTE-008 才能对固定 RRF、纯 Vector和冻结自适应方案进行同条件比较；结果不得回改 v1 query/labels，也不得据此重新选择 ROUTE-006 参数。
- 限制：每类只有10条且由项目内部标注，足以隔离参数选择与一次验证，但不足以证明统计显著性、跨仓库泛化或线上收益。

### ADR-021：冻结验证只运行预声明三策略且结果不可覆盖

- 日期：2026-08-28
- 状态：`ACCEPTED`
- 预检：ROUTE-008 在任何检索前验证 ROUTE-007 数据集 SHA 和 ROUTE-006 参数画像 SHA；本地索引强制离线增量更新到当前语料，报告记录评测语料指纹。
- 策略：只允许固定 RRF `2.0/0.25,k=10,candidates=30`、纯 Vector Top-10和冻结 Router 生成的自适应计划。三种策略均不使用 Rerank、Oracle或结果驱动参数。
- 一次性：CLI 只接受固定数据集/结果文件名，结果文件存在即拒绝覆盖。JSON保留 case ID、类别、指标和路由族，不复制 query、源码、文档或 Top结果。
- 结论：自适应获得最高点估计，但 Recall 差值只来自1条改善，MRR成对95% CI跨0。保持默认固定 RRF；不以本结果自动授权运行时接线。
- 延迟边界：本机预热后的顺序墙钟 P95 低于70ms，但没有并发、服务队列或线上硬件条件，不能据此宣称达到生产400ms P95。

### ADR-022：自适应 Router 通过默认关闭的运行时开关接入

- 日期：2026-08-28
- 状态：`ACCEPTED`
- 接线路径：Retriever 在 Hybrid/Rerank 候选生成阶段消费 QueryFeatures、双路 Top-10置信信号和冻结 Router，不改变纯 Vector/BM25 模式。
- 默认策略：`rag.adaptive_routing.enabled=false`，因为独立验证只显示小幅、不稳定点估计；接入能力不等于默认发布授权。
- 同批回退：双路召回只执行一次。置信或路由失败时用已经取得的排名和配置固定权重完成 RRF，避免异常路径重复 Embedding；可通过 `fallback_on_error=false` 改为严格失败。
- 检索分域：显式文档意图可让自适应路径包含文档；全局 `rag.include_docs=true` 仍具有优先权，排名置信结果不参与分域决定。
- 可观测与隐私：返回 metadata 仅包含稳定版本、路由族、reason codes和数值计划，不复制 query、源码、hits内容或模型输出。
- 范围：本 ADR 不接入 RerankPolicy、不默认启用 Router，也不声称 ROUTE-008 的本机延迟等同线上 SLO。

### ADR-023：代码图节点身份与源码位置解耦

- 日期：2026-08-29
- 状态：`ACCEPTED`
- 节点范围：GRAPH-001 只定义 Python file、class、function三类；方法使用function类型并通过限定名和父节点区分，不提前增加配置、测试或工具专用节点类型。
- 稳定身份：节点 ID 是 `python + kind + POSIX相对路径 + qualified_name` 的SHA-256，不包含行号。插行只更新位置；改名、移动文件或改变类型产生新身份。
- 最小内容：节点只保存名称、限定名、路径、行区间、父节点 ID、语言和schema，不保存源码、docstring、decorator、AST对象或检索结果。
- 校验：路径必须仓库相对且无遍历；符号名必须是Python标识符，限定名必须是点分标识符并以自身名称结尾；所有字符串和行号有硬上界。
- 分阶段边界：GRAPH-002 负责AST解析、父节点存在性和contains/imports边；GRAPH-003再处理calls/inherits。本 ADR 不接入索引或Retriever。

### ADR-024：结构边由纯内存 AST 构图器确定性生成

- 日期：2026-08-29
- 状态：`ACCEPTED`
- 输入边界：构图器接收调用方提供的有界 Python 相对路径到源码文本映射，不自行遍历文件系统、索引或缓存；非Python、重复规范化路径、非文本和超限输入明确拒绝。
- contains语义：文件包含顶层符号，类/函数包含其直接词法嵌套符号；方法仍是function节点。边 ID由边类型和两端稳定节点 ID生成，不含行号或源码。
- imports语义：导入边从源文件指向可解析的仓库内模块文件；支持绝对与合法包内相对导入，重复导入去重。外部/缺失模块不创建伪节点，记录结构化 unresolved issue。
- 失败与隐私：语法错误保留文件节点并记录无源码错误；自导入、重复限定名分别记录 issue。节点、边和issue序列化均不复制源码、AST或查询内容。
- 分阶段边界：本轮不解析calls、inherits或tests边，不改变Indexer/Retriever路径。跨模块检索收益只能由GRAPH-007评测验证。

### ADR-025：调用与继承只连接静态唯一确定的仓库符号

- 日期：2026-08-29
- 状态：`ACCEPTED`
- calls源与目标：源是调用所在的file/class/function节点；目标仅为同文件词法符号、有效`self`/`cls`方法、明确导入符号/模块属性或本地类构造。结构边按端点去重，不保留调用次数。
- inherits源与目标：源是子类，目标是同文件或明确导入的仓库类；支持简单属性、多继承和下标泛型语法，不执行MRO或导入代码。
- 保守解析：参数、赋值、外部导入、global/nonlocal重绑定和局部导入作用域参与遮蔽；动态对象属性、工厂返回值、lambda、推导式及无法唯一确定的引用不猜测，输出有界unresolved issue。
- 自环：递归calls是有效关系并允许自环；contains/imports/inherits自环仍为非法，自继承记录issue而不构边。
- 代价与边界：这不是完整名称解析器或类型推断器，可能漏掉动态合法关系，但优先避免错误扩展上下文。本轮不建立tests边、不接入Retriever；收益由GRAPH-007独立评测。

### ADR-026：tests边以pytest收集约定和已解析调用为证据

- 日期：2026-08-29
- 状态：`ACCEPTED`
- 测试源：采用默认pytest文件模式`test_*.py`/`*_test.py`，只收集顶层`test_*`函数及顶层`Test*`类的`test_*`方法；不新增test节点类型，继续使用function节点。
- 方向与目标：tests边从测试函数/方法指向测试支持文件外的生产符号。直接calls是首要证据；pytest文件及`test/`、`tests/`目录内helper可以有界穿透，边按测试与最终目标去重。
- fixture：只解析同文件、通过明确pytest导入识别的fixture，支持显式别名、参数依赖、fixture链和autouse；conftest层级、插件fixture及运行时参数化需要pytest收集器或覆盖率证据，本轮不猜测。
- 边界与上限：helper深度最多8层，总映射步骤最多100万；循环visited去重。仅导入、动态对象属性、普通类伪测试、嵌套函数和同测试文件目标不直接生成tests边。
- 隐私与上线：只消费内存AST和已有calls边，不读取覆盖率、测试结果或源码外数据。未接入Retriever；GRAPH-007前不据此宣称质量收益。

### ADR-009：结构图只做检索后扩展

- 日期：2026-08-29
- 状态：`ACCEPTED`
- 决策：AST图不替换BM25/Vector，而是从有序检索种子做一跳扩展。GRAPH-005只输出带种子排名和边溯源的原始候选；GRAPH-006再执行结构评分、稳定UID去重和上下文预算。
- 评分：固定未调参v1策略使用边权重、方向权重和RRF式种子排名衰减的乘积。重复路径取最高分而不累加，避免高连接度或重复边人为刷高Chunk排名；证据数量单独记录。
- 预算：调用方提供准确token成本并可预留已有上下文token；选择器按分数确定性贪心装入严格token和Chunk上限。等分偏好较小Chunk；高分Chunk装不下时继续尝试后续候选。该策略可解释且有界，但不声称全局背包最优。
- 隐私与上线：结果不保存query、document、源码或AST，不读取索引或文件。GRAPH-006仍未接入Retriever；跨模块质量、延迟和上下文污染只由GRAPH-007验证。
- 原因：避免建设昂贵且难验证的完整知识图谱，同时解决跨模块链路问题。
- GRAPH-007证据：冻结20条真实一跳calls专项集和完整5+5策略后，Recall@10点差`+0.089167`，但区间跨0；图阶段P95额外耗时`32.754ms`，无关新增单题P95为5。决策继续保持“检索后可选扩展”且不接入默认Retriever；上线前必须先做邻接预索引、查询意图相关评分和候选收紧，不能只凭点召回提升启用。

### ADR-027：Rerank Worker 生命周期与执行资源解耦

- 日期：2026-08-30
- 状态：`ACCEPTED`
- 状态契约：固定`UNLOADED/LOADING/READY/DEGRADED/FAILED`五阶段，由枚举事件产生不可变、带递增revision的快照；非法转换明确拒绝，不由调用方任意写目标状态。
- 恢复语义：`FAILED`后的模型重载进入`LOADING`；冷却后的单次恢复探测进入`DEGRADED`，成功才回到`READY`。二者分离，避免未来熔断实现把加载和推理健康混为一谈。
- 请求与模型健康分层：队列满和调用方等待超时是请求级回退，不自动把可用模型降级；加载失败、推理失败和失败阈值才驱动生命周期。具体计数、冷却和并发原子性由MODEL-002～004执行器实现。
- 最小健康数据：快照只保存phase、revision、last event和有界reason code，不保存query、任意异常文本、源码、模型输出或时间戳；后续指标适配器消费快照而不触发模型加载。
- 分阶段边界：MODEL-001不导入模型、config、线程、队列或时钟，也不接入现有Reranker/Retriever。单元测试通过不代表队列、超时、熔断、吞吐或P95验收完成。

### ADR-028：Rerank生产者以非阻塞有界FIFO承受背压

- 日期：2026-08-30
- 状态：`ACCEPTED`
- 队列语义：采用线程安全固定容量FIFO；生产者不等待可用槽位，`offer`立即接受或返回稳定的full/closed原因，防止HTTP/Agent调用线程在模型队列外形成第二层无界等待。
- 关闭语义：关闭是幂等状态变更；新请求立即拒绝，已排队请求保留并可按FIFO排空，空队列上的等待消费者全部唤醒。`None`不是内部哨兵，避免用户值与控制消息混淆。
- 内容边界：队列是泛型不透明运输层，不定义或记录query、候选、结果和异常；快照仅含size/capacity/closed。容量10000和显式等待86400秒是防错误配置的schema硬上界，不是生产推荐值或SLO。
- 超时边界：消费者的`take(timeout)`只限制等待队列项目的内部轮询，不是请求端到端或模型推理超时。MODEL-003再定义请求/结果信封、队列满RRF回退和推理deadline。
- 上线边界：MODEL-002不接入现有Reranker/Retriever，不创建Worker线程，不改变默认Rerank路径；MODEL-007之前的单元并发测试不能替代吞吐、P95和死锁压力验收。

### ADR-029：Rerank deadline释放调用方但不强杀模型线程

- 日期：2026-08-30
- 状态：`ACCEPTED`
- 执行模型：显式Rerank复用一个daemon Worker线程和一个惰性加载模型，不按请求创建线程；这延续PyTorch串行安全边界，同时让MODEL-002队列成为唯一等待入口。
- deadline语义：默认30秒，从请求被队列接受开始，覆盖排队和执行等待。到期立即释放调用方并回退RRF；未开始Future可取消并由Worker跳过，已经运行的Python/PyTorch调用无法可靠安全强杀，必须在Worker中自然结束。
- 隔离策略：每次Worker请求复制候选对象和metadata；即使推理在调用方超时后才写入rerank_score，也不会修改已经返回给Retriever的原始RRF候选。
- 稳定失败：queue full/closed、load error、inference error、timeout均为Worker自有固定reason code。Retriever只信任`RerankWorkerError`，其他异常统一为`rerank_unexpected_error`，不传播任意异常消息或外部reason属性。
- 回退不变量：回退复用原候选Top-N，不重新融合、不改`rrf_rank`；metadata只增加回退布尔值和原因。`fallback_on_error=false`继续允许调用方选择抛错。
- 局限：单次底层推理永久卡死仍会占住Worker，随后队列会满并快速回退；MODEL-004熔断不能中断已经卡死的线程，进程级隔离若成为真实需求必须另立方案。MODEL-007前不声明吞吐、P95或无死锁达标。

### ADR-030：Rerank熔断只由真实模型结果驱动并采用单探测half-open

- 日期：2026-08-30
- 状态：`ACCEPTED`
- 失败口径：连续计数只包含模型加载和推理异常；成功的完整请求清零。queue full/closed、调用方deadline及开路拒绝是请求/容量事件，不作为模型健康失败；超时后台调用最终若真实失败仍计数。
- 参数：默认连续3次失败开路、冷却60秒；阈值1～100、冷却(0,86400]为schema安全边界。这是保守运行默认值，不是线上故障数据调参结果。
- 开路一致性：达到阈值时MODEL-001状态进入FAILED；新请求在入队前快速拒绝，已入队旧请求在执行前二次门控，避免阈值后继续冲击模型。
- half-open：冷却后以锁保护原子预约唯一探测，状态FAILED→DEGRADED；其余请求明确返回probe-in-progress。探测成功DEGRADED→READY并清零；真实失败DEGRADED→FAILED并从失败时重新冷却。
- 未执行探测：若唯一探测尚未开始就因queue拒绝或Future取消而放弃，释放预约并恢复FAILED，但不增加失败、不重启冷却；下一请求可再次争抢已经到期的探测资格。
- 可观测边界：circuit快照不保存开路绝对时刻，只暴露阶段、计数、固定参数、剩余冷却和探测占用；MODEL-006再把它映射到健康与指标。熔断无法强杀运行中PyTorch，进程隔离不在本任务授权范围。

### ADR-031：Rerank压力验收分离控制面证据与真实模型性能

- 日期：2026-08-30
- 状态：`ACCEPTED`
- 决策：使用fake loader和可控sleep操作压力测试单Worker、有界队列、deadline、close、快照与熔断竞争；调用线程/监控线程均为daemon并有join硬上限，使死锁能转化为报告字段而非永久挂住测试进程。
- 报告：只输出数值配置、稳定终态、吞吐、调用/完成延迟、队列峰值、线程数和关闭判定，不接收或保存query、候选、异常详情、模型名或模型输出。终态吞吐明确包含快速拒绝，另报完成吞吐避免混淆。
- 验收：持续、过载和deadline三场景均完成全部调用与Worker关闭；竞争测试证明队列不越界、恢复期只放行一个探测、并发回退保持原始RRF排名。
- 边界：fake操作只验证Python控制面，不作为Cross-Encoder真实性能SLO；运行中PyTorch不能强杀和进程级隔离仍是已知边界。

### ADR-032：重复Agent评测将协议冻结与费用授权分离

- 日期：2026-08-30
- 状态：`ACCEPTED`
- 决策：M7-001只冻结任务SHA、评测Git commit/tree、模型参数、120运行矩阵、结果目录、预算与停止条件；冻结状态为`frozen_unfunded`，不能解释为允许调用付费API。
- 授权：实际执行必须使用独立authorization JSON，绑定protocol SHA且批准金额不得超过冻结的50 USD建议上限。每40次暂停复核，达到费用上限或连续3次Worker/API失败立即停止。
- 可比性：已有40次事务评测使用不同代码提交，不计入三次重复样本；三轮必须从同一冻结Git树构造隔离副本，禁止覆盖已有结果或看结果后修改任务。
- 防护：只读校验器验证双哈希、Git tree/blob、固定矩阵、结果路径和授权，不运行Agent、不读旧结果内容、不创建结果或Worktree。

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
