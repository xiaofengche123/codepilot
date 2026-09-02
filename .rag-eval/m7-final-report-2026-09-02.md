# M7 repeated and independent evaluation report

Status: `DRAFT_AWAITING_INDEPENDENT_LABEL_AUDIT`

## Conclusion

The three-run Qwen Agent evaluation does not establish a stable Hybrid-versus-Rerank winner. On the new 12-query, three-repository frozen retrieval set, both modes reach required Recall@10 of 1.0, while Rerank has lower MRR@10 and much higher latency. The paired confidence intervals cross zero, so no Rerank quality gain is claimed.

M7-003 is complete. M7-004 remains open only for the acceptance criterion requiring another developer to audit at least 20% of the internally created labels. A fixed 25% blind packet is ready in `external-repo-v1-audit.md`.

## Repeated Agent evaluation

Model and configuration: `qwen3.7-flash`, temperature 0.3, 20 frozen tasks, Hybrid and Rerank, three runs per condition. The protocol and full configuration hashes are recorded in `agent-repeat-v2.protocol.json` and its manifest.

| Condition | pass@1 | pass@3 |
|---|---:|---:|
| Hybrid | 13/60 (21.67%) | 6/20 (30%) |
| Rerank | 10/60 (16.67%) | 7/20 (35%) |

All 1,039 actual model turns have provider usage. Catalog-price cost was 1.207183 CNY. The pass@1 and pass@3 differences point in opposite directions. Each run also contains two pre-model A06 worker failures caused by an ambiguous frozen mutation fixture; these are environment/harness failures, not retrieval failures or API failures.

## Independent repository retrieval evaluation

Dataset SHA-256: `752c15d724abd825b996c827d5adc6209146a77ba681e0cafbb0795115e878e8`. The manifest pins repository commits, corpus hashes, evaluator source hash, code HEAD/tree, cutoffs, and offline execution. The evaluation used local indexes and cached embeddings, with no network, downloads, paid APIs, or label tuning after results.

| Mode | Recall@5 | Recall@10 | MRR@10 | graded nDCG@10 | Avg latency | P95 latency |
|---|---:|---:|---:|---:|---:|---:|
| Hybrid | 1.0000 | 1.0000 | 0.8083 | 0.7660 | 159.0 ms | 245.9 ms |
| Rerank | 0.9167 | 1.0000 | 0.7333 | 0.7400 | 6783.0 ms | 7759.1 ms |

Paired Rerank minus Hybrid differences over 12 queries:

| Metric | Mean difference | 95% bootstrap CI | Improved / degraded / tied |
|---|---:|---:|---:|
| Recall@10 | 0.0000 | [0.0000, 0.0000] | 0 / 0 / 12 |
| MRR@10 | -0.0750 | [-0.3250, 0.1500] | 3 / 3 / 6 |
| graded nDCG@10 | -0.0260 | [-0.1911, 0.1241] | 4 / 3 / 5 |

Per-repository Recall@10 is 1.0 for both modes. Hybrid/Rerank MRR@10 is 0.875/0.750 for itsdangerous, 0.750/0.575 for markupsafe, and 0.800/0.875 for click.

## Interpretation and limitations

- Twelve queries are enough for a small independent smoke validation, not a broad generalization claim.
- The evaluator and labels were created in the same implementation session; the frozen hashes prevent post-result rewriting but do not replace independent label review.
- Evaluation was run from a dirty worktree because the evaluator and dataset were not committed yet. The manifest therefore records both the base Git HEAD/tree and exact evaluator source SHA.
- Rerank has no Recall@10 advantage here, its MRR and nDCG point estimates are lower, and its latency is about 42.7 times Hybrid on average.
- Only the repeated pass rates and the explicitly limited external-set observations are suitable for project reporting. No causal or universal superiority claim is supported.

## Remaining acceptance action

An independent developer must complete and sign `external-repo-v1-audit.md`. If corrections are required, create v2 instead of modifying v1, rerun the frozen evaluation, and update this report. Until then M7-004 and M7 overall remain `IN_PROGRESS_AWAITING_EXTERNAL_REVIEW`.
