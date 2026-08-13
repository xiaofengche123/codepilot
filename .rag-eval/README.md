# CodePilot RAG 开发评测集

`codepilot-dev.json` 是第一版 30 条人工标注开发集，用于发现检索问题和调整参数，不作为最终测试集或简历指标的唯一依据。

## 分布

| 类别 | 数量 | 目的 |
|---|---:|---|
| `identifier` | 6 | 精确类名、方法名、配置名和标识符 |
| `natural_language` | 8 | 自然语言功能描述 |
| `bug_symptom` | 6 | 症状与源码表述不完全一致的异常定位 |
| `cross_module` | 6 | 需要多个模块共同回答的调用链 |
| `mixed_language` | 4 | 中英文和技术术语混合查询 |

合计 30 条。`relevant` 使用当前索引器实际生成的 Chunk ID：`相对路径:起始行-结束行`。

## 防止数据泄漏

评测目录命名为 `.rag-eval`，并已加入 `rag/indexer.py` 的 `SKIP_DIRS`。强制重建索引后，评测问题及答案不会进入 ChromaDB。修改相关源码导致 AST 行号变化时，必须同步更新 Chunk ID。

## 运行

```powershell
.\venv\Scripts\python.exe -c "from rag.indexer import index_project; print(index_project(r'D:\codepilot', force=True))"
.\venv\Scripts\python.exe -m rag.evaluate .rag-eval\codepilot-dev.json --project . -k 5
.\venv\Scripts\python.exe -m rag.evaluate .rag-eval\codepilot-dev.json --project . -k 10
```

## 使用原则

1. 先根据源码判断相关 Chunk，不能根据某种检索方法的返回结果反向标答案。
2. 调参阶段可以查看本开发集；确定参数后，另建独立测试集并冻结。
3. 必须按类别查看失败案例，不能只看总体平均值。
4. 当前评测程序支持 BM25/Vector/Hybrid、二元相关性、Recall@K、MRR 和预热后延迟；后续可增加 Precision@K 与 nDCG。
5. 简历只能写实际冻结测试集的结果，并记录 Git commit、配置和评测日期。

当前评测程序已经输出 BM25、Vector、Hybrid 三条基线，以及模型预热后的平均/P95 查询延迟。详细误差归因见 `error-analysis-2026-08-13.md`。
