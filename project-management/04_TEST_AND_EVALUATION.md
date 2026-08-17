# CodePilot 测试与评测规范

更新时间：2026-08-17

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

当前已验证：

```text
127 passed, 4 skipped
```

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

## 9. 最终简历指标规则

- 只使用冻结或外部评测结果。
- 指标必须说明数据集和 K。
- 延迟必须说明环境和是否预热。
- LLM Agent 成功率需要多次重复后再写入简历。
- 如果存在校正结果，必须同时保留原始结果和校正原因。
