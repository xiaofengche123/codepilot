# CodePilot RAG 独立评测记录（2026-08-14）

## 结论

CodePilot 当前最合适的产品形态仍是：默认 Weighted RRF，可选高质量 Rerank，CPU 服务不默认开启。内部冻结集上 Rerank 的质量提升有统计支撑，但平均 3.42 秒、P95 6.85 秒、最大 7.31 秒；外部 CodeSearchNet judged pool 上纯 Vector 又优于 Rerank，证明当前权重和精排器不是跨语料普适最优。

## 内部冻结集

- 数据：150 条，identifier / natural_language / bug_symptom / cross_module / mixed_language 各 30 条。
- 标注：209 个 required，106 个 supporting；测试代码默认只作 supporting。
- Dataset SHA-256：`c74dde28140e5d03bc2d0a5ffef323777b3174bf2a5111569c81cbc86600bd55`
- Corpus SHA-256：`3fd97b330a8ceeba1c2c7b88a72f157613f8153389d122d6a34bf3f65410428b`
- 冻结时 Git HEAD：`89448462a77f7e5eb3c0eaf5484e9e2eedf9873b`，工作区有意保留未提交改动。

| 方法 | Required Recall@5 | MRR@5 | nDCG@5 | Required Recall@10 | MRR@10 | nDCG@10 | Avg | P95 | Max |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BM25 | 0.7321 | 0.6669 | 0.6138 | 0.8390 | 0.6794 | 0.6550 | 41.5 ms | 71.1 ms | 78.5 ms |
| Vector | 0.5088 | 0.4560 | 0.4179 | 0.6112 | 0.4684 | 0.4584 | 45.5 ms | 83.0 ms | 111.2 ms |
| Weighted RRF | 0.7477 | 0.6716 | 0.6184 | 0.8401 | 0.6818 | 0.6578 | 78.8 ms | 136.9 ms | 201.9 ms |
| Rerank | 0.8777 | 0.7762 | 0.7421 | 0.9254 | 0.7810 | 0.7653 | 3421.8 ms | 6854.7 ms | 7306.8 ms |

Rerank 相对 Weighted RRF 的 Required Recall@10 平均差为 +0.0853，95% bootstrap CI `[+0.0333, +0.1387]`；23 题改善、6 题退化、121 题持平。MRR@10 平均差 +0.0992，95% CI `[+0.0360, +0.1641]`。两者 fallback 均为 0%。

Rerank 仍有 17 题未完整命中 required。明确的 Recall@10 退化包括 T-B12、T-M29、T-M30（均从命中降到 0），以及 T-N29、T-C04、T-C15。跨模块题的 Rerank Recall@10 为 0.8939；mixed-language 从 Hybrid 0.9833 降到 0.9167，说明精排并非所有类别都稳定获益。

## CodeSearchNet 外部 judged-pool

协议使用官方 99 个自然语言查询和人工 0–3 级判断，在被判断 URL 的并集上排序；未判断 query-url 对按 0 处理。它不是约 200 万函数全语料上的归档排行榜成绩。

| 方法 | nDCG@10 | nDCG@100 |
|---|---:|---:|
| BM25 | 0.4264 | 0.6338 |
| Vector | **0.5685** | **0.7408** |
| Weighted RRF | 0.4421 | 0.6496 |
| Rerank (Top-30) | 0.5263 | 0.6359 |

Rerank 相对 Hybrid 在 nDCG@10 上改善 71/99 个查询、退化 28/99，但仍低于纯 Vector。`bm25_weight=2.0`、`vector_weight=0.25` 显然带有 CodePilot 内部语料偏好，不应表述为普适参数。Rerank 只重排 Top-30，因此 nDCG@100 不与返回 100 条的其他模式完全同等，主要看 nDCG@10。

覆盖限制：官方 CSV 有 4,006 条标注、2,874 个唯一 URL；成功恢复 2,752 个（95.76%）。缺失 156 条标注，等级 0/1/2/3 分别缺 57/35/29/35，影响 69 个查询；但 99 个查询均至少保留一个正相关结果。缺失不是随机的，成绩只能在此覆盖条件下解释。

## 端到端 Agent 评测

已使用同一 `deepseek-chat` 模型、固定 10 步上限完成 20 个缺陷注入任务，每个任务分别运行 Hybrid / Rerank，共 40 次真实 Agent 调用。成功条件要求目标文件被修改、注入缺陷恢复、指定测试通过且 Agent 无异常。

原始严格结果为 Hybrid 7/20、Rerank 8/20。A03 两个条件都恢复了精确 mutation，唯一失败是 harness 注入的 D 盘绝对模型缓存覆盖触发了与任务无关的路径断言；原始 JSON 保留不改。剔除这个已确认的 harness 假阴性后：

| 条件 | 成功率 | Agent Avg | Agent P95 | 隔离进程 Avg | 隔离进程 P95 | 语义搜索调用 | 总工具调用 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Hybrid | 8/20（40%） | 22.9s | 29.9s | 62.6s | 75.0s | 15 | 260 |
| Rerank | 9/20（45%） | 30.7s | 42.2s | 70.5s | 107.4s | 16 | 282 |

成对结果为：5 题两边成功、3 题仅 Hybrid、4 题仅 Rerank、8 题都失败。Rerank 只多成功 1/20，却使 Agent 平均阶段耗时增加约 34%，隔离进程平均耗时增加约 13%；单次运行不支持“成功率稳定提升”的结论。

分类上，三个 indexing 任务两边全部失败；两个 retrieval 任务 Hybrid 成功 1 个、Rerank 0 个；两个 reranker 实现任务也是 Hybrid 1 个、Rerank 0 个。Rerank 的优势主要来自 configuration/context/tools 中的少数任务。A06-rerank 额外修改 `rag/reranker.py`，A11-rerank 新建 `_verify_events.py`，虽满足核心成功条件但存在范围外修改。

所有 40 次均无 API 错误。原始结果位于 `.rag-eval/results/agent-v1/`，汇总见 `agent-v1-summary.json`。该实验每个条件只运行一次，LLM 随机性足以造成单题翻转；若要对 5 个百分点差异做统计结论，需要多随机种子重复，而不是继续调这 20 个任务。

## 可信度边界

内部集已冻结且正式结果只运行一次，但问题与标注仍由项目开发者/评测实现者创建，不等同于第三方双盲标注。建议后续请另一位开发者抽审至少 20% 条目，重点检查 required 是否过严、跨模块题是否遗漏等价入口。任何修订都应创建 `codepilot-test-v2.json`，不能改 v1。

CodeSearchNet 提供外部可比性，但当前是 judged-pool 协议和 95.76% URL 覆盖，不应冒充官方完整语料 leaderboard。Agent 任务已控制相同模型、temperature 与任务定义，但每个条件只运行一次；40 次对比只能作为初步产品证据。
