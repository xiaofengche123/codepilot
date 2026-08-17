# CodePilot 风险、技术债与候选需求

更新时间：2026-08-17

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
- 计划：M1 事务式编辑、M2 状态机、M3 Trace。

### RISK-004：Rerank CPU 延迟过高

- 级别：P1
- 状态：`MITIGATED`
- 证据：内部 P95 6854.7ms，最大7306.8ms。
- 当前措施：默认关闭、失败回退。
- 后续：条件式路由、有界队列、超时和熔断。

### RISK-005：固定融合权重泛化不足

- 级别：P1
- 状态：`OPEN`
- 证据：内部 Hybrid 接近最佳，CodeSearchNet 外部纯 Vector 明显更优。
- 计划：M4 可解释自适应路由。
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

## 3. 技术债

### DEBT-001：Agent 循环职责过多

当前 `AgentSession.run` 同时负责历史、上下文、模型、工具循环和最终保存。M2 状态机实施时应逐步抽取执行状态，但保持外部接口兼容，避免一次性重写。

### DEBT-002：工具返回以文本为主

LLM 需要文本，但内部评测和状态机需要结构化结果。建议逐步采用“内部 dataclass/typed result + 外部格式化”的双层设计。

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
