# CodePilot 风险、技术债与候选需求

更新时间：2026-09-02

## 1. 风险分级

| 级别 | 定义 |
|---|---|
| P0 | 可能导致数据、密钥、仓库或正式评测不可恢复地损坏 |
| P1 | 直接影响 Agent 正确性、产品默认策略或简历可信度 |
| P2 | 影响性能、可维护性和部分场景稳定性 |
| P3 | 优化项或尚未证明必要的增强 |

## 2. 当前风险

### RISK-001：API Key 已在历史对话中暴露

- 级别：P0
- 状态：`OPEN`
- 影响：第三方可能使用额度。
- 当前措施：Key 只写在被忽略的 `.env`，文档和提交不保存明文。
- 建议：尽快在供应商控制台轮换，并更新本地 `.env`。

### RISK-002：当前核心改动尚未提交

- 级别：P1
- 状态：`OPEN`
- 影响：本地工作区损坏可能丢失 RAG、评测器、数据和报告。
- 措施：完成 BASE-001 审查后，由用户确认提交到 `dev`。
- 禁止：用 reset、checkout 或清理命令“恢复干净”。

### RISK-003：Agent 修改成功率偏低

- 级别：P1
- 状态：`OPEN`
- 证据：事务式复测严格成功 Hybrid 11/20、Rerank 8/20；目标文件修改率70%/60%，仍低于80%目标。
- 主因：目标文件未修改、已定位后未及时编辑、错误平台命令消耗步数、测试失败后恢复不足。
- 进展：M2 的 STATE-001～008 已完成实例级状态、客观 revision 证据、结构化恢复、执行前预算门控和 Diff Review；仅完成确定性验证，端到端付费成功率尚未复测。
- 计划：进入 M3 Trace；真实重复评测仍需用户新授权，在此之前不宣称端到端成功率已提高。

### RISK-004：Rerank CPU 延迟过高

- 级别：P1
- 状态：`MITIGATED`
- 证据：内部 P95 6854.7ms，最大7306.8ms。
- 当前措施：默认关闭、失败回退。
- 后续：条件式路由、有界队列、超时和熔断。
- 进展：MODEL-003～007已实现有界单Worker、deadline、熔断/单探测、预热、观测及fake-inference压力验收；默认Rerank仍关闭。控制面持续场景完成吞吐380.208 req/s、P95 23.676ms且无死锁，但真实Cross-Encoder P95仍以既有慢速证据为准，风险维持`MITIGATED`而非关闭。

### RISK-005：固定融合权重泛化不足

- 级别：P1
- 状态：`OPEN`
- 证据：内部 Hybrid 接近最佳，CodeSearchNet 外部纯 Vector 明显更优。
- 进展：ROUTE-001～008 已完成。50条冻结内部验证上，自适应相对固定 RRF 的 Recall@10/MRR@10 点差为 `+0.020000/+0.016952`；Recall仅1条改善，MRR成对95% CI跨0，尚未证明稳定优势。
- 计划：Router 已通过默认关闭的特性开关完成接线并具备同批回退；保持默认固定 RRF，后续只在有灰度、回退指标和线上观测时启用，不根据冻结结果回调参数或答案。
- 约束：不能用 test-v1 调参。

### RISK-006：内部冻结集由项目方自建

- 级别：P1
- 状态：`OPEN`
- 影响：可能存在标注者偏差、跨模块 required 过严或漏标等价入口。
- 计划：第三方抽审20%以上；修订必须创建 v2。

### RISK-007：CodeSearchNet 不是完整全语料评测

- 级别：P2
- 状态：`ACCEPTED`
- 影响：不能与官方 leaderboard 直接比较。
- 措施：报告中始终使用 “human-judged URL pool”，披露95.76%覆盖率。

### RISK-008：Agent 评测只运行单次

- 级别：P2
- 状态：`OPEN`
- 影响：Hybrid/Rerank 单题翻转可能来自 LLM 随机性。
- 新证据：v2 严格结果中仅 Hybrid 6题、仅 Rerank 3题；但实际调用语义检索的任务分别只有11题和14题，不能做纯排序因果推断。
- 计划：重大功能完成后每个条件重复3次，报告 pass@1/pass@3。

### RISK-009：Reranker predict 全局串行

- 级别：P2
- 状态：`ACCEPTED_FOR_NOW`
- 影响：并发服务吞吐受限。
- 原因：保护 PyTorch 模型在不同后端上的线程安全。
- 计划：M6 使用单模型 Worker 和有界队列，不简单移除锁。
- 进展：MODEL-002已完成独立的线程安全有界FIFO和非阻塞背压契约，但尚未接入单模型Worker；推理回退、压力测试和移除旧全局串行路径前，本风险状态保持不变。
- 进展：MODEL-003已将显式Rerank接入单模型Worker，队列满和30秒deadline会回退RRF；底层运行中推理仍不可强杀，熔断、压力测试与吞吐/P95证据未完成，因此风险继续保持`ACCEPTED_FOR_NOW`。
- 进展：MODEL-004加入连续失败熔断和单恢复探测，但保持单线程是有意的模型安全边界；在MODEL-007获得吞吐、P95和死锁压力证据前，本风险状态不变。
- 进展：MODEL-007的fake-inference持续、过载和deadline压力均无死锁，队列未越界且关闭完成；单Worker设计风险已有控制面证据，但真实模型吞吐仍受串行上限约束，状态继续`ACCEPTED_FOR_NOW`。

### RISK-010：冻结工具遍历性能较慢

- 级别：P3
- 状态：`OPEN`
- 原因：`rglob` 会先遍历大型排除目录，再逐文件过滤。
- 计划：使用 `os.walk` 目录剪枝。
- 注意：优化工具不能修改已冻结数据 SHA。

### RISK-011：Windows Git 全局 ignore 权限警告

- 级别：P3
- 状态：`OPEN`
- 现象：Git 尝试读取 `C:\Users\26211\.config\git\ignore` 时权限不足。
- 影响：主要是日志噪声，尚未影响 Git 命令结果。
- 计划：单独排查用户级 Git 配置，不在业务改动中混修。

### RISK-012：Shell 超时遗留子进程

- 级别：P1
- 状态：`MITIGATED`
- 证据：A16-Hybrid 的 `python -m pip install sentence-transformers` 在内层30秒超时后仍存活，持有 Chroma 文件并使 worker 达到900秒外层超时。
- 修复：`run_shell` 和评测 runner 均创建独立进程组，超时时终止完整进程树；清理 Windows 文件锁时有界重试。
- 验证：新增 timeout/失败报告/清理重试测试；CI run `31990221352` 的 Python 3.11、3.12 与 Docker 全部通过。

### RISK-013：Agent 评测解释器与平台提示偏差

- 级别：P1
- 状态：`MITIGATED_FOR_FUTURE_RUNS`
- 证据：worker 由项目 venv 启动，但 Agent 的 `python` 命令曾解析到系统 Python；多个轨迹还尝试 `pwd`、`ls`、`head` 等 POSIX 命令，消耗10步预算。
- 修复：未来 runner 将项目 venv 放到 PATH 首位，设置 `VIRTUAL_ENV`，并强制 Hugging Face/PIP 离线；当前40份结果保持原样，不事后重跑。
- 遗留：M2 应向 Agent 暴露结构化平台能力，避免依赖模型猜测 shell 方言。

### RISK-014：状态预算尚未接管工具调度

- 级别：P1
- 状态：`MITIGATED`
- 发现日期：2026-08-17
- 证据：STATE-001～004 已能统计和判定 discovery/inspect/edit/verify/recovery 预算耗尽，但现有 Agent 循环仍会执行模型已生成的工具调用，不会在执行前强制拒绝或重新规划。
- 修复：Agent 在工具执行前检查 discovery/inspect/edit/verify 预算；超预算调用不执行但返回稳定拒绝 ToolMessage，并以稳定 terminal reason 终止。recovery budget 在再次进入恢复前检查。
- 协议保证：同一 AIMessage 中后续调用即使已进入终态，仍逐个获得 `task_already_terminal` ToolMessage。
- 验证方法：fake model 多 tool call、预算耗尽和上下文裁剪测试通过；真实重复评测仍待单独付费授权。
- 关闭限制：这里只证明调度门控与协议正确，不代表端到端任务成功率已提升。

### RISK-015：auto 模式无法识别完全未尝试编辑的修改请求

- 级别：P1
- 状态：`ACCEPTED`
- 发现日期：2026-08-19
- 影响：为保持旧调用方兼容，`auto` 在没有编辑尝试时按 read-only 完成；如果模型对修改请求只给文字且从未调用编辑工具，状态机无法确定用户意图。
- 措施：已知缺陷修复评测显式使用 `mutation_required`；`agent.run` 和 FastAPI 均提供向后兼容的可选 `task_mode`。
- 后续：未来任务协议/CLI 可让调用方显式声明模式；禁止增加第二个 LLM 分类器。

### RISK-016：API mutation 缺少默认安全测试执行通道

- 级别：P1
- 状态：`OPEN`
- 发现日期：2026-08-19
- 影响：API 无交互策略默认拒绝危险 `run_shell`，显式 mutation 因而可能无法取得最新 pytest 证据。
- 当前措施：保持拒绝策略，任务以稳定 `verification_unavailable` 失败并暴露脱敏状态摘要；不把拒绝或模型文字当作测试通过。
- 根本方案：未来增加受限命令、资源和路径白名单的安全 TestRunner，而不是放开通用 shell。

## 3. 技术债

### DEBT-001：Agent 循环职责过多

当前 `AgentSession.run` 同时负责历史、上下文、模型、工具循环和最终保存。STATE-001～004 已把执行状态、转移、证据和预算抽到 `execution_state.py`，但调度仍在 `run` 中；后续继续增量抽取，保持外部接口兼容，避免一次性重写。

### DEBT-002：工具返回以文本为主

LLM 需要文本，但内部评测和状态机需要结构化结果。事务编辑已有 JSON 结构，STATE-003 又让 `run_shell` 始终携带稳定返回码；其他工具仍以文本为主，后续继续采用“内部 dataclass/typed result + 外部格式化”的双层设计。

### DEBT-003：模型缓存配置存在 sentence-transformers 弃用警告

当前 `cache_folder` 在本环境可用，但依赖库提示将来应通过 `model_kwargs`、`processor_kwargs`、`config_kwargs` 传递。升级前必须查官方版本文档并补离线加载测试。

### DEBT-004：端到端进程总耗时最初只打印未结构化保存

正常进程总耗时仍只由 runner 输出，Agent 报告保存的是 `AgentSession.run` 时间；A16 synthetic 报告保存了 `worker_elapsed_seconds`。后续应为每个正常样本也结构化保存 `worker_elapsed_seconds`，并禁止混合 timing scope 聚合。

### DEBT-005：Agent 任务 Oracle 以精确恢复基线为主

优点是可自动判定，缺点是可能拒绝功能等价修复。未来可增加语义 Oracle：测试、静态属性和安全不变量，但不能放宽现有 v1 结果。

v2 实例包括 A10 的等价 sequence 实现、A18 的集合内等价插入，以及 A07 的更严格清洗实现。它们继续按冻结 v1 严格失败报告；如需改进，只能创建新版本并预先定义 `allowed_files` 与语义不变量。

## 4. 候选需求池

这些需求没有进入当前主线，实施前必须补充证据。

| ID | 需求 | 状态 | 进入条件 |
|---|---|---|---|
| IDEA-001 | Multi-Query | `REJECTED_FOR_NOW` | 新独立集证明召回覆盖是主要瓶颈 |
| IDEA-002 | 完整 Graph RAG | `IDEA` | 轻量 AST 图不能满足跨模块任务 |
| IDEA-003 | Tree-sitter 多语言图 | `IDEA` | Python 图验证有效，且有真实 Java/JS 项目需求 |
| IDEA-004 | 自训练 Reranker | `IDEA` | 有足够独立标注和训练/验证隔离 |
| IDEA-005 | GPU Rerank 服务 | `IDEA` | 有部署环境和明确吞吐需求 |
| IDEA-006 | 自动生成测试 | `IDEA` | 事务编辑和状态机稳定后 |
| IDEA-007 | 长期记忆/项目知识库 | `IDEA` | 当前会话记忆不足成为已测量瓶颈 |

### RISK-017：便宜模型在最后一次工具调用后缺少终态收尾迭代

- 级别：P1
- 状态：OPEN
- 发现日期：2026-08-19
- 证据：`qwen3.7-flash` 的 A01 Hybrid pilot 在第 10 次模型响应执行了非空 `git_diff`，Oracle 成功且测试通过，但固定 `max_iterations=10` 已无下一次响应用于最终回答。
- 影响：状态证据可能在硬上限的最后一次工具调用后齐全，但 `AgentSession.run()` 仍按 max-iterations 返回；便宜模型的额外检查步骤会放大该问题。
- 临时措施：停止扩大付费评测；保留 10 步协议和 STATE-006 证据门槛，不把 Oracle 成功误报为 Agent COMPLETE。
- 后续：TRACE-001 应记录每轮状态 snapshot 和终态决策，区分“证据已齐但缺少收尾响应”与“证据不足”；取得证据后再评审受限 finalization call 或新的评测协议版本。
- 进展：`TRACE-001` 已完成该可观测性缺口；确定性集成测试能够明确显示“最后一轮 review 成功、没有 completion decision、随后 max_iterations_exhausted”。风险仍为 OPEN，是否增加受限收尾调用必须另立决策和兼容测试，不能在 Trace 任务中顺带改变硬上限语义。

### RISK-018：历史报告和环境文本模式的失败分类精度有限

- 级别：P2
- 状态：ACCEPTED
- 发现日期：2026-08-19
- 证据：`agent-v1`/`agent-v2-transactional` 生成时没有逐轮 execution trace；旧报告只能依靠编辑指标、最终测试、worker returncode 和有限错误文本分类。
- 影响：历史报告的检索/读取漏斗不可恢复；`ModuleNotFoundError` 等同时可能来自环境或错误代码，不能只凭任意异常文本宣称根因。
- 措施：强环境信号（worker timeout、Worktree、验证通道、权限/命令/资源错误）优先归环境；其余保持代码或控制分类并保留次要原因。新旧报告不得直接比较缺失的漏斗字段。
- 后续：M7 新评测统一使用 execution trace；若出现高频 ambiguous 分类，再新增显式工具错误码，不扩大自由文本猜测。

### RISK-019：图扩展性能和无关上下文未达上线门槛

- 级别：P1
- 状态：`OPEN`
- 发现日期：2026-08-29
- 证据：GRAPH-007内部专项集Recall@10点差`+0.089167`，但95% CI跨0；图阶段P95额外耗时`32.754ms`，超过10ms门槛。88个图新增Chunk仅9个命中标注相关项，无关新增单题P95为5，超过门槛3。
- 正向边界：测试和文档新增均为0；图路径尚未接入Retriever，当前产品默认行为与普通查询延迟未变化。
- 后续：预构建按节点索引的邻接与Chunk映射，加入查询意图相关边/目标评分并收紧候选；在新版本冻结策略和新评测集上复测，禁止复用GRAPH-007 v1结果调参后重报。

### RISK-020：M7内部标注尚缺独立开发者复核

- 级别：P1
- 状态：OPEN
- 发现日期：2026-09-02
- 证据：M7-003的12题标签由本次实现过程创建；固定3/12（25%）盲审包尚无另一名开发者姓名、日期和签字。
- 影响：在独立抽审完成前，不能把M7-004或M7整体标记DONE，也不能把外部集结果描述为已完成人工独立验证。
- 临时措施：冻结v1数据、仓库commit、corpus SHA和结果；报告明确标记`DRAFT_AWAITING_INDEPENDENT_LABEL_AUDIT`。
- 关闭条件：没有创建标签的开发者完成盲审包；如需修订则创建v2并重跑，保留v1全部证据。

## 5. 风险处理模板

```markdown
### RISK-XXX：标题

- 级别：P0/P1/P2/P3
- 状态：OPEN/MITIGATED/ACCEPTED/CLOSED
- 发现日期：
- 证据：
- 影响：
- 临时措施：
- 根本解决方案：
- 验证方法：
- 关闭日期：
```
