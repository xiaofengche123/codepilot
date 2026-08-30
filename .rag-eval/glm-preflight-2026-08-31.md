# M7-v2 GLM 4.7 Flash 免费预跑

- 时间：2026-08-30 23:56～2026-08-31 00:24（Asia/Shanghai）
- run-id：`m7-agent-repeat-v2-glm-preflight`
- 模型：`glm-4.7-flash`
- 代码提交：`39a5170b483820e41eeb1ffbaf46c17e00b367d2`
- 冻结任务SHA-256：`71caa70e7b441380c79745c701bb02a77f8b4d0efcfb2d892b3a91f053d7ac09`
- 范围：A01/A02 × Hybrid/Rerank，共4项；结果不进入M7正式统计。

## 结果

| 任务 | 条件 | Oracle成功 | Agent耗时 | 模型轮次 | 输入Token | 输出Token | 主要结论 |
|---|---|---:|---:|---:|---:|---:|---|
| A01 | Hybrid | 否 | 246.151s | 5 | 16,820 | 386 | 第6次请求触发`429/1305`访问量过大，未编辑 |
| A01 | Rerank | 是 | 293.930s | 10 | 48,528 | 931 | 修复和测试成功，但第10轮后无完成响应，最终状态failed |
| A02 | Hybrid | 否 | 187.445s | 3 | 6,883 | 174 | 第4次请求触发`400/1214 messages参数非法` |
| A02 | Rerank | 否 | 328.635s | 10 | 40,247 | 1,181 | 未编辑，耗尽10轮迭代预算 |

汇总：1/4 Oracle成功；Hybrid 0/2，Rerank 1/2。Agent阶段合计1056.161秒；runner墙钟约27分47秒。28/28模型轮次均获得provider usage，输入112,478、输出2,672、未计量轮次0。四个Worker均正常退出，manifest为`completed`，没有Worker或清理失败。

## 判定

- 通过：API Key和端点可用；流式文本、工具调用及Token usage能够返回；Rerank条件至少完成一次真实修复。
- 未通过：免费端点出现429限流；A02出现OpenAI兼容消息格式400；10轮内稳定完成和收尾不足；单项runner墙钟约5.6～7.9分钟。
- 用途边界：保留`glm-4.7-flash`作为免费兼容探针和故障排查模型，不替代`qwen3.7-flash`正式M7，不复用本次结果为正式样本。
- 费用边界：本报告只记录provider返回的Token usage，不读取云端账单；免费资格和最终账单以智谱控制台为准。
