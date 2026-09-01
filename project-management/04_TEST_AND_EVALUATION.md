# CodePilot 测试与评测规范

更新时间：2026-09-01

## 1. 目的

本文件定义 CodePilot 的测试层级、正式数据、可运行命令、结果解释和防泄漏规则。任何 AI 不得根据正式测试结果静默修改答案或协议。

## 2. 测试层级

### L1：单元测试

验证单一模块和边界条件：

- 配置合并和默认值。
- 工具路径与风险控制。
- 事务编辑的匹配预检、并发冲突、语法校验、原子替换和失败恢复。
- Agent 上下文裁剪。
- MCP 协议。
- 索引切分与迁移。
- BM25、RRF、Rerank。
- 模型锁和回退。
- Worktree 和事件序列。

### L2：集成测试

验证模块之间的调用链：

- Agent → 工具分发 → workdir 注入。
- MCP stdio Client/Server。
- RRF Top-30 → Cross-Encoder → Top-K。
- 索引状态 → Chroma metadata。
- 模型加载失败 → RRF fallback。

### L3：内部冻结检索评测

验证当前 CodePilot 仓库中的真实代码定位能力。

### L4：外部检索基准

验证方法是否只对 CodePilot 当前语料有效。

### L5：端到端 Agent 任务

验证 Agent 是否真正定位、修改正确文件并通过测试。

### L6：服务压力和恢复测试

未来验证并发、队列、超时、熔断、回退和死锁。

## 3. 常用命令

### 全量测试

```powershell
.\venv\Scripts\python.exe -m pytest -q
```

ROUTE-006 当前已验证：

```text
446 passed, 4 skipped
```

其中 Router/Tuning/RerankPolicy 定向107 passed，相关回归299 passed。离线 CLI 仅接受30条 `codepilot-dev.json`，搜索空间为每个路由族54组；族级方案开发集 Recall@10/MRR@10 为0.788889/0.593056，固定 RRF 为0.780556/0.591005。差值只用于参数选择，不能作为独立验证结果；test-v1、冻结 Oracle和正式结果未读取。

ROUTE-007 建立 `.rag-eval/codepilot-validation-v1.json`：50条、五类各10条、66个 required 标签，开发集规范化 query 重合为0。冻结 manifest 状态为 `frozen_unscored`，同时封存数据集 SHA-256 `4c45a8b848328d44a234468753cd11757818df3fedc49759a252e2ddef8fa71f` 与路由参数画像 SHA-256 `630542eff259aa3c3eecb9a98d419a6c9a3d188e9c89f56a6350f2f5486c1009`。本轮只验证结构、标签到 chunk 的映射和防漂移契约，没有运行任何策略并且没有指标；定向43 passed，全量456 passed、4 skipped；对比留待 ROUTE-008。

ROUTE-008 在上述双哈希通过后完成唯一一次离线比较：固定 RRF、纯 Vector、自适应 Recall@10/MRR@10 为 `0.466667/0.248905`、`0.380000/0.196333`、`0.486667/0.265857`。自适应相对固定 Recall 差值 `+0.020000`（成对95% CI `[0.000000,0.060000]`），MRR差值 `+0.016952`（`[-0.006143,0.051167]`）；证据不足以宣称稳定优势。报告为 `.rag-eval/adaptive-routing-validation-2026-08-28.{json,md}`，不包含 query、源码或 Top 结果。定向115 passed，全量466 passed、4 skipped。

ROUTE-RUNTIME-001 将冻结 Router 接入 Retriever 的可选运行时路径。默认关闭时固定 RRF 行为不变；启用后 Hybrid/Rerank 候选使用自适应计划，异常复用同批原始排名回退。定向98 passed，全量471 passed、4 skipped；强制离线真实 smoke 验证 `adaptive_routing=true`、`rule_router_v1`、`natural_language` 和30候选 metadata。该 smoke 只验证接线，不重复运行冻结评测或产生新质量结论。

GRAPH-001 只定义节点契约，不运行图检索或质量评测。30个定向测试覆盖稳定身份、行移动、类型/文件/改名区分、父节点约束、路径与数值边界、Python/Unicode限定名、不可变性、JSON序列化、内容最小化和依赖隔离；节点/Indexer相关回归36 passed，全量501 passed、4 skipped。跨模块 Recall 变化必须等到 GRAPH-007，不能由节点单测宣称。

GRAPH-002 新增纯内存 Python AST 构图和 contains/imports 边。53个节点/构图定向测试覆盖嵌套词法关系、异步函数、绝对/相对/函数内导入、仓库外导入、自导入、重复符号及其函数体导入、语法错误、确定性、Windows路径、输入与大小边界、内容最小化及未来边类型隔离；节点/构图/Indexer相关回归59 passed，全量524 passed、4 skipped。构图尚未接入 Retriever，因此没有运行 GRAPH-007 质量评测，也不宣称 Recall 或端到端收益。

GRAPH-003 增加保守 calls/inherits 解析。76个节点/结构构图定向测试覆盖同文件和嵌套调用、递归、`self`/`cls`、直接/模块/相对导入、构造和静态方法调用、本地/跨模块/多继承/泛型基类、作用域隔离、遮蔽与global重绑定、装饰器归属、动态引用拒绝、去重和内容最小化；节点/结构构图/Indexer相关回归82 passed，全量547 passed、4 skipped。未解析引用为结构化issue而非伪边；lambda、推导式与动态分派未纳入简单解析。代码图仍未接入Retriever，因此没有新的Recall、延迟或端到端收益结论。

GRAPH-004 增加pytest tests关系映射。103个节点/五类结构边定向测试覆盖默认测试文件/函数/`Test*`类识别、异步与Windows路径、方向、直接调用、pytest及`test/`/`tests/`支持模块helper链、循环/深度与总步数上限、同文件fixture/别名/依赖链/autouse、作用域隔离、动态引用拒绝、去重、确定性和内容最小化；节点/构图/Indexer相关回归109 passed，全量574 passed、4 skipped。仅加载`rag/`和`tests/`的56文件smoke生成867节点、408条tests边。该smoke没有相关性标注，且不解析conftest/插件fixture或覆盖率，因此不能作为映射准确率或检索收益证据。

GRAPH-005 增加纯内存种子Chunk一跳扩展。24个定向测试覆盖精确/包含/文件节点映射、方法到类Chunk回退、未来符号拆分、五类边过滤、双向遍历、递归、自顶向下及反向tests、重复路径保留、结构化issue、硬规模上限和内容最小化；代码图/Indexer/Retriever相关回归147 passed，全量598 passed、4 skipped。该层未接入Retriever，也不评分或去重，因此没有检索收益结论。

GRAPH-006 增加纯内存结构评分、UID去重和上下文预算。50个定向测试覆盖固定/自定义策略、边/方向/种子排名分数组件、重复证据取最佳而不累加、冲突UID拒绝、精确token成本、reserved tokens、严格token/Chunk上限、超大候选跳过、稳定等分规则、序列化内容最小化及GRAPH-005到006串联；代码图/Indexer/Retriever相关回归197 passed，全量648 passed、4 skipped。固定权重未调参，贪心预算不是全局背包最优；跨模块Recall、上下文污染和延迟必须由GRAPH-007独立评测。

GRAPH-007在检索前冻结20条内部cross_module专项集、52个required标签和完整5+5图策略；32个目标均由种子Chunk通过真实跨文件一跳calls边可达，数据集/策略SHA分别为`adc8e9bb…dfcc`和`ad354bde…597b`。唯一一次离线比较中，固定Hybrid与图增强Recall@10为`0.567500/0.656667`，点差`+0.089167`、95% CI `[-0.045833,+0.222500]`，7题改善、3题下降、10题持平；MRR点差`-0.010476`。图阶段P95额外耗时`32.754ms`，新增88个Chunk仅9个命中标注相关项，无关新增单题P95为5；测试和文档新增均为0。召回点门槛通过，但性能与无关上下文门槛失败，M5标记`DONE_WITH_GAP`。定向27 passed；相关回归232 passed；全量675 passed、4 skipped。

MODEL-001只验证Rerank Worker生命周期协议，不运行模型、检索或正式评测。22个定向测试覆盖初始态、加载成功/失败、重复推理失败、健康恢复、失败阈值、重载与恢复探测分流、显式卸载、非法转换、revision与事件自洽、不可变性、JSON序列化、reason code脱敏边界和运行时依赖隔离；Reranker/RerankPolicy/Retriever相关回归94 passed，全量697 passed、4 skipped。该结果不证明队列、超时、熔断、吞吐或P95已达标。

MODEL-002只验证有界队列协议，不运行模型、检索或正式评测。24个定向测试覆盖FIFO、满载非阻塞拒绝、20线程并发入队严格容量、等待消费者唤醒、空队列等待上限、关闭后排空、关闭幂等与唤醒、容量/等待数值边界、不可变内容最小快照和运行时依赖隔离；相关回归118 passed，全量721 passed、4 skipped。队列尚未运行真实Worker，因而不能据此宣称队列满RRF回退、推理超时、吞吐、P95或无死锁验收完成。

MODEL-003以fake loader/inference验证单模型Worker和Retriever回退，不加载真实Cross-Encoder、不联网且不运行正式评测。新增20项测试，连同既有Reranker共26项定向，覆盖惰性单次加载、单线程复用、加载重试、DEGRADED恢复、运行中deadline、排队取消、实际queue full、关闭卸载、数值边界、内容最小快照、RRF顺序/原因、第三方异常脱敏以及迟到推理不污染回退对象；相关回归94 passed，全量741 passed、4 skipped。deadline是调用方等待上限，不是底层推理强杀，因此不能据此宣称卡死恢复、P95、吞吐、熔断或无死锁压力验收完成。

MODEL-004以注入单调时钟和fake loader/inference验证熔断，不等待真实冷却、不加载模型、不联网且不运行正式评测。新增23项测试，连同既有Worker/Reranker共49项定向，覆盖精确失败阈值、成功重置、开路快速拒绝、剩余冷却、single half-open probe、探测失败重开、加载失败后探测重载、开路清退旧排队请求、timeout/queue full不计数、稳定Retriever回退原因、不可变内容最小快照和参数硬边界；相关回归117 passed，全量764 passed、4 skipped。这些确定性测试不证明真实模型故障分布、吞吐、P95、进程级卡死恢复或无死锁压力验收完成。

MODEL-007使用纯离线fake sleep推理完成控制面压力验收。11项定向测试覆盖持续与过载吞吐、调用/完成P95、deadline饱和、5轮重复启停、submit/close/snapshot竞争、32并发single half-open探测、64并发Retriever回退与`rrf_rank`不变量。持续场景1000请求全部成功，完成吞吐380.208 req/s、P95 23.676ms；过载场景8成功/992 queue-full；deadline场景248 queue-full/8 timeout。三类实测均0未完成调用、单Worker线程、队列不超容量、监控与关闭完成且无死锁。相关回归143 passed、3 skipped，全量792 passed、4 skipped。数字只代表注入的2ms/30ms fake操作和本机控制面，不代表真实Cross-Encoder性能或永久卡死进程恢复。

M7-001只冻结重复评测协议，不运行Agent。协议固定M6完成提交`7917e00…`、任务SHA`71caa70e…`、DeepSeek Chat/temperature 0.3、20任务×2条件×3次=120个新运行，并提出50 USD硬上限和每40次暂停点。旧40次事务评测来自不同代码提交，不能复用。manifest状态`frozen_unfunded`；独立授权文件和结果目录均不存在，`--require-authorization`按预期失败。Protocol定向13 passed，相关58 passed，全量805 passed、4 skipped。校验器不读取结果内容；本轮未修改或覆盖旧结果，没有创建Worktree、新结果或授权文件，也未调用网络或付费API。

低成本v2修订保留上述DeepSeek v1历史协议，正式模型改为`qwen3.7-flash`并冻结实现提交`cf3be67…`；建议上限降为10 CNY。新增`glm-4.7-flash`免费预跑路由、显式模型不可用禁止回退、逐轮provider Token记录和条件级汇总；GLM预跑不得计入正式统计。模型/用量定向38 passed，Protocol定向13 passed，全量811 passed、4 skipped；未创建密钥、授权、结果或Worktree，未联网、未调用任何API。

2026-08-31执行协议限定的GLM四项免费预跑：1/4 Oracle成功，Hybrid 0/2、Rerank 1/2。A01-Hybrid为429限流，A02-Hybrid为400 messages参数非法，A01-Rerank修复/测试成功但无完成状态，A02-Rerank耗尽10轮。28/28轮usage完整，输入112,478、输出2,672；四个Worker正常退出且无清理失败。该结果只证明部分接口/工具兼容并暴露故障，不进入正式M7统计，也不改变千问v2冻结协议。

2026-08-31获得10 CNY上限授权并完成千问正式r1的40/40。Hybrid成功5/20、Rerank成功3/20，成对结果为both success 2、Hybrid-only 3、Rerank-only 1、both failed 14；只有一次重复，不能报告pass@3或稳定收益。352/352实际模型回合usage完整，输入1,692,644、输出71,182 Token，目录价估算0.395474 CNY，无Agent API或清理失败。A06两条件因冻结mutation旧文本在目标文件出现2次而在模型调用前worker failure，导致每条件19/20报告含usage及汇总`complete=false`，但未计量模型回合为0。首次FreeTierOnly中断批次独立归档且不入正式统计；r2/r3未执行。

2026-09-01完成千问正式r2的40/40。Hybrid与Rerank均成功4/20，成对结果为both success 3、Hybrid-only 1、Rerank-only 1、both failed 15。334/334实际模型回合usage完整，输入1,629,192、输出78,076 Token，目录价估算0.388299 CNY；无Agent API或清理失败。A06两条件仍因冻结夹具歧义在调用前worker failure；A16 Rerank一次本地Cross-Encoder推理失败后按设计回退RRF且worker正常结束。r1+r2中间pass@1为Hybrid 9/40（22.5%）、Rerank 7/40（17.5%），合计16/80（20%）；正式用量686/686回合、目录价0.783773 CNY。当前按第二轮暂停点停止，r3未执行，因此不报告最终pass@3。

ROUTE-005 当前已验证：

```text
421 passed, 4 skipped
```

其中 RerankPolicy 定向52 passed，QueryFeatures/Plan/信号/Router/RAG/配置/工具相关回归274 passed；策略未接入 Retriever，延迟预算与成本估计均由调用方显式提供，未运行真实模型、正式评测或付费 API。400ms P95与 Rerank 调用比例仍待运行时接线和独立验证，不能由单元测试宣称达成。

ROUTE-004 当前已验证：

```text
369 passed, 4 skipped
```

其中规则路由器定向30 passed，QueryFeatures/Plan/信号/RAG/配置/工具相关回归222 passed；规则参数未使用 test-v1 或正式结果调参，路由器未接入运行时且所有计划保持 `rerank=false`。

ROUTE-003 当前已验证：

```text
339 passed, 4 skipped
```

其中检索置信信号定向40 passed，QueryFeatures/Plan/RAG/配置/工具相关回归192 passed；信号是纯本地排名计算，不是概率，没有执行真实检索、模型、冻结集正式评测或付费 API。

ROUTE-002 当前已验证：

```text
299 passed, 4 skipped
```

其中 RetrievalPlan 定向56 passed，QueryFeatures/RAG/配置/工具相关回归152 passed；计划模块未接入运行时，没有运行冻结集正式评测、模型或付费 API。

ROUTE-001 当前已验证：

```text
243 passed, 4 skipped
```

其中 QueryFeatures 定向32 passed，相关 RAG/配置/工具回归64 passed；全部使用纯本地确定性输入，未加载模型、未调用网络或付费 API，也未运行冻结集正式评测。

STATE-005～008 当前已验证：

```text
182 passed, 4 skipped
```

本轮使用 fake model、fake tool executor、临时目录和临时 Git 仓库；未调用真实模型、未运行付费 Agent 评测、未生成新的正式结果文件。新增覆盖结构化恢复、recovery budget、revision 证据失效、强制 Diff Review、多 tool call 拒绝配对、CLI/API/MCP 兼容、并发隔离和状态脱敏。

GitHub Actions 基线：

- 运行：`31776231906`（提交 `32e778e`）。
- Ubuntu / Python 3.11：测试与导入校验通过。
- Ubuntu / Python 3.12：测试与导入校验通过。
- Docker：镜像构建与 Compose 配置校验通过。
- 矩阵使用 `fail-fast: false`，确保不同 Python 版本均产生独立结果。

事务编辑功能分支验收：

- 提交：`fac8de0`；运行：`31778063773`。
- Ubuntu / Python 3.11：117 passed、3 skipped；`test_symlink_escape_is_rejected` 实际通过；导入校验通过。
- Ubuntu / Python 3.12：测试与导入校验通过。
- Docker：镜像构建与 Compose 配置校验通过。
- Windows 本地：116 passed、4 skipped；额外的一个 skip 是当前账户无符号链接创建权限，不是功能失败。

### Diff 检查

```powershell
git diff --check
```

Windows 下出现 LF/CRLF 提示不等于 diff 失败；必须根据退出码和实际 whitespace error 判断。

### 模型本地检查

```powershell
.\venv\Scripts\python.exe -m rag.reranker
```

不得在普通查询中使用 `--download`。首次准备模型时才允许显式下载，并确保缓存目标在 D 盘。

### 增量索引

```powershell
.\venv\Scripts\python.exe -c "from rag.indexer import index_project; print(index_project('.'))"
```

### 冻结集 SHA 检查

```powershell
.\venv\Scripts\python.exe -m rag.eval_dataset `
  .rag-eval\codepilot-test-v1.json --project . --check
```

### 开发集评测

```powershell
.\venv\Scripts\python.exe -m rag.evaluate `
  .rag-eval\codepilot-dev.json --project . --ks 5 10
```

开发集可以用于调试和参数选择，但结果不能当作最终独立指标。

### 冻结集正式评测

已经在 2026-08-14 运行一次：

```powershell
.\venv\Scripts\python.exe -m rag.evaluate `
  .rag-eval\codepilot-test-v1.json `
  --project . --ks 5 10 `
  --output .rag-eval\results\codepilot-test-v1-2026-08-14.json
```

不应为了日常开发重复运行 test-v1。开发过程使用 dev 集和针对性单元测试；完成一个大阶段后使用新的独立集验证。

### CodeSearchNet

```powershell
.\venv\Scripts\python.exe -m rag.codesearchnet prepare
.\venv\Scripts\python.exe -m rag.codesearchnet evaluate
```

外部原始数据放在：

```text
.rag-eval/external-data/codesearchnet
```

该目录被忽略，不应提交。

### Agent 任务：初始基线（agent-v1）

单任务调试：

```powershell
.\venv\Scripts\python.exe .rag-eval\run_agent_eval.py `
  --model deepseek-chat `
  --task A01 `
  --condition both `
  --confirm-cost
```

断点续跑：

```powershell
.\venv\Scripts\python.exe .rag-eval\run_agent_eval.py `
  --model deepseek-chat `
  --condition both `
  --confirm-cost --resume
```

真实 Agent 评测会产生 API 费用，必须由用户明确授权。

## 4. 冻结数据规则

### test-v1

- 路径：`.rag-eval/codepilot-test-v1.json`
- 数量：150。
- SHA：`c74dde28140e5d03bc2d0a5ffef323777b3174bf2a5111569c81cbc86600bd55`
- 状态：永久冻结。

禁止：

- 根据排名补删 required。
- 为新实现修改 query。
- 把容易失败的题删除。
- 根据 Rerank 结果调整答案。
- 把 test-v1 当作开发集反复调参。

如果标注确实有错误：

1. 在风险/决策文档记录证据。
2. 保留 v1 不变。
3. 创建 `codepilot-test-v2.json`。
4. 写清楚 v1 → v2 的变更原因。

## 5. 相关性定义

```json
{
  "required": ["完成问题必须找到的核心实现"],
  "supporting": ["有帮助但不是核心入口的实现或测试"]
}
```

原则：

- 核心实现进入 required。
- 测试代码通常进入 supporting。
- 跨模块题只把完成问题必须经过的节点列为 required。
- 如果存在多个等价入口，应在新数据版本中明确，而不是评测后临时修改。

## 6. 当前正式结果

### 内部冻结集

| 方法 | ReqR@5 | ReqR@10 | MRR@10 | nDCG@10 | P95 | Fallback |
|---|---:|---:|---:|---:|---:|---:|
| BM25 | 0.7321 | 0.8390 | 0.6794 | 0.6550 | 71.1ms | 0% |
| Vector | 0.5088 | 0.6112 | 0.4684 | 0.4584 | 83.0ms | 0% |
| Hybrid | 0.7477 | 0.8401 | 0.6818 | 0.6578 | 136.9ms | 0% |
| Rerank | 0.8777 | 0.9254 | 0.7810 | 0.7653 | 6854.7ms | 0% |

### 外部 CodeSearchNet judged-pool

| 方法 | nDCG@10 | nDCG@100 |
|---|---:|---:|
| BM25 | 0.4264 | 0.6338 |
| Vector | 0.5685 | 0.7408 |
| Hybrid | 0.4421 | 0.6496 |
| Rerank | 0.5263 | 0.6359 |

### Agent 任务

| 条件 | 原始成功 | 校正成功 | Agent Avg | Agent P95 |
|---|---:|---:|---:|---:|
| Hybrid | 7/20 | 8/20 | 22.9s | 29.9s |
| Rerank | 8/20 | 9/20 | 30.7s | 42.2s |

A03 的两个原始失败只来自 harness 注入的绝对 D 盘模型缓存路径触发无关测试断言；原始 JSON 未覆盖，报告同时保留原始和校正值。

### Agent 任务：事务式编辑复测（agent-v2-transactional）

冻结任务、缺陷和 Oracle 均未修改；任务 SHA-256 为 `71caa70e7b441380c79745c701bb02a77f8b4d0efcfb2d892b3a91f053d7ac09`，被测提交为 `daee3cc1f4c7c8226d173fd7b295c32d1b2d5c1f`。40份结果完整，旧 `agent-v1` 未覆盖。

| 条件 | 严格成功 | 目标已改 | 目标已改且测试通过、无 Agent 异常 | 语义检索任务数 | 语义检索调用 | 事务编辑任务 | 旧写入任务 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Hybrid | 11/20 | 14/20 | 14/20 | 11/20 | 13 | 14 | 0 |
| Rerank | 8/20 | 12/20 | 10/20 | 14/20 | 18 | 12 | 0 |

| 条件 | 可比 Agent Avg | Median | P95（nearest-rank） | 正常最大值 | 运行异常最大值 | Worker failure |
|---|---:|---:|---:|---:|---:|---:|
| Hybrid | 26.2s | 26.3s | 42.6s | 42.6s | 900.1s | 1/20 |
| Rerank | 31.0s | 27.1s | 43.5s | 72.4s | 72.4s | 0/20 |

“可比 Agent”延迟排除 A16-Hybrid 的 synthetic worker 报告：正常报告的 `elapsed_seconds` 只覆盖 `AgentSession.run`，A16 的900.1秒是整个 worker wall time，两种 timing scope 不能混算。正式 `summary.json` 保留了包含该异常值的原始聚合（Hybrid Avg 69.8s），解读时必须同时披露此限制和900.1秒最大值。

严格失败案例：

- Hybrid 未修改目标：A02、A06、A14、A16、A18、A19；已改且测试通过但未逐字恢复 Oracle：A07、A10、A15。
- Rerank 未修改目标：A03、A09、A10、A13、A14、A15、A17、A19；A07/A18 为测试通过但非逐字恢复，A08 修复与测试通过后发生 tool-message 400，A11 恢复缺陷但新增了错误测试并失败。
- A16-Hybrid 调用了系统 Python 的 `pip install sentence-transformers`，被900秒外层超时终止；没有选择性重跑，也没有从正式失败中删除。
- 测试文件被记录为范围外修改：Hybrid 2/20、Rerank 3/20。由于 A07 的提示明确允许补测试，v1 缺少 `allowed_files` 标注，这个比例不能直接解释为越权率。

与 agent-v1 的描述性对比：Hybrid 严格成功从7/20到11/20，目标修改从10/20到14/20；Rerank 严格成功仍为8/20，目标修改从13/20到12/20。由于每个条件只运行一次且 Agent 轨迹不确定，不能把变化全部归因于事务编辑器。可以确认的是：发生编辑的26个任务全部选择了事务工具，32次事务调用全部成功，旧 `write_file` 为0；没有端到端触发 SHA 冲突或回滚，相关保证仍主要由单元测试覆盖。

## 7. 评测解释纪律

可以说：

- 内部冻结集上 Rerank 明显提高检索指标。
- CPU 精排延迟很高。
- 端到端单次任务中 Rerank 只比 Hybrid 多成功1题。
- 外部自然语言 judged-pool 中纯 Vector 最好。
- 当前默认 RRF、可选 Rerank 是质量/延迟权衡。
- 事务编辑器在 Agent 选择编辑时稳定执行，但“是否及时编辑”仍需状态机解决。

不能说：

- Rerank 稳定提高 Agent 成功率。
- 当前权重是通用最优。
- CodeSearchNet 达到官方排行榜水平。
- 150条内部集是第三方独立标注。
- 40次 Agent 调用具有统计显著性。
- `55% vs 40%` 能单独证明 Rerank 降低成功率；两组实际使用语义检索的任务数不同，且没有重复运行。

## 8. 新功能测试准入

每个功能至少需要：

1. 正常路径测试。
2. 输入边界测试。
3. 失败路径测试。
4. 回退或回滚测试。
5. 并发相关功能的并发测试。
6. Windows 路径测试。
7. 不泄漏密钥、冻结集和缓存的测试。
8. 与现有行为的回归测试。

## 9. Qwen 3.7 Flash 付费 Pilot（2026-08-19）

- 用户明确授权真实 API 调用；凭据只写入被 Git 忽略的 `.env`，未写入报告或日志。
- 模型：`qwen3.7-flash`；冻结任务：`A01`；条件：Hybrid；未修改冻结任务或 Oracle。
- 第一次诊断运行 `agent-v3-qwen37flash-pilot-20260819`：Oracle 成功、8 tests passed，但修复回到原 Git 基线后 diff 为空，状态机正确拒绝 COMPLETE。
- 评测 worker 随后把注入后的缺陷提交为隔离工作树 review 基线；产品侧非空 diff 门槛未放宽。
- 第二次运行 `agent-v3-qwen37flash-pilot2-20260819`：Oracle 成功、测试通过、非空 `git_diff` 已执行，但模型在第 10 次响应才请求最后的 review，没有第 11 次响应生成最终答案，因此返回 max-iterations。
- 结论：API 兼容和客观修复能力已得到单任务证据；固定 10 步下的状态机终态收尾尚未得到真实模型成功证据。不得据此宣称完整评测或成功率提升。

## 10. M3 Trace 与失败分析验证（2026-08-19）

- 新评测报告自动保存 `execution_trace`、`agent_final_status`、`agent_completed`、唯一 `failure_stage`、次要原因和失败域。
- 条件汇总生成十级漏斗：任务、required 命中、正确文件读取、相关测试读取、编辑尝试、目标修改、Oracle、测试执行、测试通过、无范围外修改。
- 原 Oracle `success` 与 Agent `COMPLETE` 分开：代码恢复但状态机因硬上限失败时，Oracle 可保持成功，控制失败仍被计数。
- 环境失败优先于代码症状；worker timeout、Worktree 创建、模型/验证通道不可用不会进入代码能力失败计数。
- 40份 `agent-v2-transactional` 历史报告只读兼容汇总成功；历史报告没有逐轮 Trace，因此兼容漏斗的早期阶段可能为0，不能与新运行直接逐字段比较。
- 定向92 passed、3 skipped；全量211 passed、4 skipped；未调用真实模型。

## 11. 最终简历指标规则

- 只使用冻结或外部评测结果。
- 指标必须说明数据集和 K。
- 延迟必须说明环境和是否预热。
- LLM Agent 成功率需要多次重复后再写入简历。
- 如果存在校正结果，必须同时保留原始结果和校正原因。
