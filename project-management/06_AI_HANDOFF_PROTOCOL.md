# CodePilot AI 接手与执行协议

更新时间：2026-08-14

## 1. 目的

本文件用于让新的 AI 在缺少历史对话时，仍能安全、准确地接手 CodePilot，不重复已完成工作，不破坏冻结数据，不清理用户工作区，并能从任务表直接继续开发。

## 2. 必读顺序

AI 开工前必须阅读：

1. `project-management/00_START_HERE.md`
2. `project-management/01_CURRENT_STATE.md`
3. `project-management/03_PROGRESS_TRACKER.md`
4. `project-management/08_SESSION_LOG.md` 最近一条记录
5. 当前任务涉及的路线图和架构决策章节

如果任务涉及评测，还必须阅读：

- `project-management/04_TEST_AND_EVALUATION.md`
- `.rag-eval/README.md`

## 3. 开工检查

先运行只读命令：

```powershell
git branch --show-current
git status --short
git diff --check
```

然后确认：

- 当前分支是否为 `dev` 或任务指定分支。
- 是否有用户未提交改动。
- `install.py` 是否出现在 diff 中。
- 任务是否会读取冻结集答案。
- 是否会调用付费 API。
- 是否会下载模型或数据。
- 下载、缓存和临时目录是否位于 D 盘。

## 4. 当前保护对象

以下内容不得擅自修改、删除或回退：

- 用户已有的 `install.py`。
- `.rag-eval/codepilot-test-v1.json`。
- `.rag-eval/codepilot-test-v1.manifest.json`。
- `.rag-eval/agent-repeat-v1.protocol.json`与对应manifest是历史冻结证据；不得修改。
- `.rag-eval/agent-repeat-v2.protocol.json`与对应manifest是当前千问正式评测协议；变更必须创建新版本。
- `.rag-eval/results` 中的原始正式结果。
- 用户当前未提交的实现。
- `.env` 中的配置和密钥。

禁止执行：

- `git reset --hard`
- 无用户授权的 `git checkout -- file`
- 清理整个工作区
- 删除 `.git`
- 把模型缓存或评测原始数据加入提交
- 在输出中打印 API Key

## 5. 领取任务

AI 应从 `03_PROGRESS_TRACKER.md` 选择：

1. 状态为 `NEXT` 的最高优先级任务。
2. 或用户明确指定的任务 ID。
3. 如果任务依赖未完成，先报告依赖，不得绕开依赖大规模重写。

开工后：

- 将任务状态改为 `IN_PROGRESS`。
- 在开发日志记录开始时间和假设。
- 如果会改变架构，先补 ADR 或至少记录 proposed decision。

## 6. 实施流程

```text
阅读任务与验收条件
→ 检查相关实现和测试
→ 复现当前问题
→ 设计最小完整改动
→ 实现
→ 运行目标测试
→ 运行相关回归
→ 必要时全量测试
→ 检查 diff 和生成物
→ 更新任务与日志
```

“最小完整改动”不是追求代码最少，而是：

- 不重写无关模块。
- 但必须包含错误处理、回退、测试和必要的数据结构。
- 不为了赶进度留下明显的半实现状态。

## 7. 测试纪律

- 先运行与修改最相关的测试。
- 核心模块、共享工具和配置变更需要全量测试。
- 必须运行 `git diff --check`。
- 测试失败时先判断代码错误、测试错误还是环境错误。
- 不得为了通过测试降低断言强度或修改冻结答案。
- 如果修复测试本身，必须说明为什么旧测试错误。

## 8. RAG 特殊规则

- `codepilot-dev.json` 可以用于调参。
- `codepilot-test-v1.json` 只能验证 SHA 和查看既有正式报告，不用于继续调参。
- 外部 CodeSearchNet 结果单独报告，不与内部集求综合分数。
- 新检索策略必须保留 BM25、Vector、Hybrid 的基线对比。
- Rerank 必须记录候选数、回退和延迟。
- 任何新路由先在开发集实现，再建立新的独立测试集。

## 9. Agent 评测特殊规则

- 任务定义和 mutation 不得复制到 Agent 可见工作区。
- Agent 副本不得包含 `.env`。
- 模型 Key 只通过父进程环境传递。
- 每个条件使用独立工作区。
- 成功至少要求：目标文件修改、Oracle 满足、测试通过、无 Agent 错误。
- 范围外修改必须单独报告，即使核心任务成功。
- harness 假阴性必须保留原始值，并单独给出校正结果和证据。
- 真实调用前必须获得费用授权。

## 10. 密钥与外部操作

- 不在文档、代码、测试、日志或命令输出中记录密钥。
- `.env` 必须保持被 Git 忽略。
- 用户曾在聊天中暴露的 Key 应提醒轮换，但不能把 Key 写入本目录。
- Git push、发布、付费 API、外部消息等操作按用户授权范围执行。

## 11. 收尾检查

完成任务后必须：

1. 查看 `git status --short`。
2. 确认没有意外文件。
3. 运行 `git diff --check`。
4. 记录实际测试命令和结果。
5. 将任务状态改为 `DONE` 或记录 `BLOCKED` 原因。
6. 更新 `08_SESSION_LOG.md`。
7. 如果指标变化，更新测试文档，但不得覆盖原始正式结果。
8. 告诉用户是否提交、是否推送、是否仍有未确认事项。

## 12. AI 交付格式

最终回复至少包含：

- 完成了什么。
- 修改了哪些文件。
- 测试命令和结果。
- 是否存在行为或兼容性变化。
- 是否修改评测数据。
- 是否有遗留风险。
- 下一项推荐任务 ID。

## 13. 新会话可直接使用的提示词

```text
请接手 D:\codepilot。先完整阅读 project-management/00_START_HERE.md、
01_CURRENT_STATE.md、03_PROGRESS_TRACKER.md、06_AI_HANDOFF_PROTOCOL.md，
以及 08_SESSION_LOG.md 最近一条记录。把当前未提交工作区视为有意改动，
不要清理、reset、覆盖或修改 install.py。查看 git status 后，继续执行
状态为 NEXT 的最高优先级任务；先复现和审查，再实现、测试并更新项目管理文档。
```
