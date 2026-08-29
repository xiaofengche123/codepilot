# CodePilot RAG 评测协议

## 数据集角色

- `codepilot-dev.json`：30 条开发集，只用于诊断和调参，不对外作为最终指标。
- `codepilot-validation-v1.json`：50 条 ROUTE-007 独立验证集，五类各10条；在运行策略比较前冻结，同时封存 ROUTE-006 路由参数。它独立于参数选择，但仍是同一项目、同一标注流程的内部数据，不等同外部泛化基准。
- `codepilot-graph-cross-module-v1.json`：20条GRAPH-007跨模块专项集；每题包含一个检索种子Chunk和由真实跨文件一跳`calls`边可达的required目标。它在比较前同时冻结完整5+5合并、边方向、评分、token预算和验收阈值，仍是同仓库内部构造集。
- `codepilot-test-v1.json`：150 条内部冻结集，五类各 30 条。每条分别标注必须命中的 `required` 和仅提供帮助的 `supporting`；测试代码原则上只属于 supporting。
- CodeSearchNet：外部可比基准，使用官方 99 条自然语言查询和人工 0–3 级相关性判断。
- `agent-tasks-v1.json`：20 个隔离的缺陷修复任务，用于比较 Hybrid 与 Rerank 对实际 Agent 成功率的影响。

内部集与外部集必须分别报告，不合并为单一分数。内部集适配 CodePilot 当前代码库，CodeSearchNet 衡量跨仓库泛化，两者回答的问题不同。

ROUTE-007 只冻结未评分验证集，不执行检索：

```powershell
.\venv\Scripts\python.exe -m rag.retrieval_validation `
  .rag-eval\codepilot-validation-v1.json --project .

.\venv\Scripts\python.exe -m rag.retrieval_validation `
  .rag-eval\codepilot-validation-v1.json --project . --check
```

检查同时验证数据集 SHA-256 与 ROUTE-006 路由参数画像；任何策略对比均留待 ROUTE-008，结果不得反向修改 query、required 或 supporting。

ROUTE-008 已在强制离线、增量更新后的本地索引上完成唯一一次比较，完整结果见 `adaptive-routing-validation-2026-08-28.json`，可读摘要见同名 Markdown。固定 RRF、纯 Vector、冻结自适应的 Recall@10/MRR@10 分别为 `0.466667/0.248905`、`0.380000/0.196333`、`0.486667/0.265857`。自适应相对固定方案为 `+0.020000/+0.016952`，但 MRR 成对95%区间跨0，Recall 也只有1条改善；不得描述为稳定或外部泛化收益。

GRAPH-007 已完成唯一一次离线比较：固定Hybrid与固定Hybrid+图的Recall@10为`0.567500/0.656667`，点差`+0.089167`，但成对95% CI为`[-0.045833,+0.222500]`。图阶段P95额外耗时`32.754ms`，88个新增Chunk仅9个命中标注相关项，无关新增单题P95为5；测试和文档新增均为0。召回点估计达到5pp门槛，但性能与无关上下文门槛失败，因此M5为`DONE_WITH_GAP`，不得宣称整体验收通过或外部泛化收益。完整结果见`graph-cross-module-validation-2026-08-29.json`及同名Markdown。

## 冻结规则

冻结前运行：

```powershell
.\venv\Scripts\python.exe -m rag.eval_dataset .rag-eval\codepilot-test-v1.json --project .
```

命令会校验数量、分类、重复问题、与开发集的精确重复、标注是否能映射到当前 Chunk，并把数据集 SHA-256、语料 SHA-256 与 Git HEAD 写入 manifest。冻结后只允许修订到新版本（例如 test-v2），不得根据 test-v1 结果修改问题或答案。

正式内部评测只运行一次：

```powershell
.\venv\Scripts\python.exe -m rag.evaluate .rag-eval\codepilot-test-v1.json --project . --ks 5 10 --output .rag-eval\results\codepilot-test-v1-2026-08-14.json
```

报告包括 Required Recall@5/@10、MRR、graded nDCG、分类指标、P95/最大延迟、失败案例、fallback 率、相对 Hybrid 的成对差异和 bootstrap 置信区间。

## CodeSearchNet 外部基准

```powershell
.\venv\Scripts\python.exe -m rag.codesearchnet prepare
.\venv\Scripts\python.exe -m rag.codesearchnet evaluate
```

当前协议在官方人工判断 URL 的并集上重排，将未判断的 query-url 对视为 0。它不是 CodeSearchNet 归档排行榜的全语料检索分数，报告必须保留这一限定。由于部分固定 commit 的原始文件已返回 404，结果还会披露 URL 覆盖率、缺失标注等级分布及受影响查询数。

## 端到端 Agent 对比

执行器只在 `D:\codepilot\.rag-eval\worktrees` 创建隔离副本，并将 `TEMP`、`TMP`、`HF_HOME` 和 `SENTENCE_TRANSFORMERS_HOME` 指向 D 盘。每个任务注入一个已知缺陷，然后记录任务成功、总耗时、工具/语义检索调用次数、是否修改目标文件、意外修改文件和测试结果。

真实运行会产生模型 API 费用，因此执行器要求显式确认：

```powershell
.\venv\Scripts\python.exe .rag-eval\run_agent_eval.py --model deepseek-chat --condition both --confirm-cost
```

不带 `--confirm-cost` 时不会发起任何 Agent API 调用。
