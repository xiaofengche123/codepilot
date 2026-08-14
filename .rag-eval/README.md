# CodePilot RAG 评测协议

## 数据集角色

- `codepilot-dev.json`：30 条开发集，只用于诊断和调参，不对外作为最终指标。
- `codepilot-test-v1.json`：150 条内部冻结集，五类各 30 条。每条分别标注必须命中的 `required` 和仅提供帮助的 `supporting`；测试代码原则上只属于 supporting。
- CodeSearchNet：外部可比基准，使用官方 99 条自然语言查询和人工 0–3 级相关性判断。
- `agent-tasks-v1.json`：20 个隔离的缺陷修复任务，用于比较 Hybrid 与 Rerank 对实际 Agent 成功率的影响。

内部集与外部集必须分别报告，不合并为单一分数。内部集适配 CodePilot 当前代码库，CodeSearchNet 衡量跨仓库泛化，两者回答的问题不同。

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
