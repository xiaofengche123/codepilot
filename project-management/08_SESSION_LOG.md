# CodePilot 开发会话日志

本文件按时间倒序记录关键开发过程。每次开发结束都应增加一条，不覆盖旧记录。

---

## 2026-08-14：项目管理文档中心

### 本轮目标

建立一个让用户和后续 AI 都能直接接手的项目管理目录，跟踪当前功能、测试进度、路线图、风险和下一项任务。

### 完成内容

- 创建 `project-management/00_START_HERE.md`。
- 记录当前架构、真实评测指标和主要瓶颈。
- 制定 M0–M7 路线图。
- 将任务拆成稳定 ID 和状态。
- 写入测试与冻结评测纪律。
- 建立 ADR 决策记录。
- 建立 AI 接手、密钥和工作区保护协议。
- 建立风险、技术债和候选需求列表。

### 工作区状态

- 分支：`dev`
- HEAD：`8944846`
- RAG、评测、测试和本目录仍未提交。
- `install.py` 未修改。

### 验证

- 9个 Markdown 文档创建完成。
- 内部 Markdown 链接检查：0个缺失。
- 密钥格式扫描：通过，未写入 API Key。
- Git 状态：仅新增 `project-management/`，原有未提交代码保持不变。
- 本轮只增加文档，没有修改业务实现，因此未重复运行 pytest。

### 下一步

1. 完成 `BASE-001`：审查当前未提交文件。
2. 完成 `BASE-003`：用户确认后提交到 `dev`。
3. 开始 `EDIT-001`：定义事务式编辑数据结构。

---

## 2026-08-14：BASE-001 基线提交前审查

### 本轮任务

- 任务 ID：BASE-001
- 目标：确认当前 RAG、评测、结果、测试和管理文档适合形成 dev 基线。

### 审查结果

- 待提交评测结果：43个文件，约1.34MB。
- `.rag-eval` 范围 JSON：47个，全部可解析。
- 密钥扫描：通过。
- `install.py`：无差异。
- `.env`、模型缓存、外部原始数据和 worktree：已忽略。
- 冻结 test-v1 SHA：一致。

### 测试

- `python -m py_compile`：通过。
- `python -m pytest -q`：89 passed，3 skipped。
- `git diff --check`：通过，仅有 Windows 换行提示。

### 下一步

- BASE-003：提交当前阶段到 dev。
- BASE-004：检查 GitHub Actions。

---

## 2026-08-14：端到端 Agent 评测

### 目标

比较 Hybrid 和 Rerank 是否真正提高 CodePilot 完成代码修改任务的能力。

### 完成内容

- 创建20个缺陷注入任务。
- 每个任务在 D 盘独立副本运行。
- Hybrid/Rerank 各运行20次 `deepseek-chat`。
- 修复 pilot 阶段发现的任务泄漏、运行时配置污染、无 Git 基线、临时目录清理和测试环境覆盖问题。
- 原始结果完整保留。

### 结果

- 原始：Hybrid 7/20，Rerank 8/20。
- 校正已确认 A03 harness 假阴性后：Hybrid 8/20，Rerank 9/20。
- Rerank Agent 平均阶段耗时增加约34%。
- 主要失败是没有修改目标文件，而不是 API 错误。

### 结论

- Rerank 不应 CPU 默认开启。
- 下一阶段应优先提高编辑和执行闭环。
- 单次5个百分点差异不具有稳定统计结论。

---

## 2026-08-14：内部冻结集与外部检索评测

### 完成内容

- 建立150条内部冻结集，五类各30条。
- 拆分 required/supporting 标签。
- 生成 manifest、数据 SHA 和语料 SHA。
- 扩展评测器，增加 Recall@5/10、MRR、graded nDCG、分类、延迟、失败和 bootstrap CI。
- 完成一次正式内部评测。
- 完成 CodeSearchNet 99查询 judged-pool 外部评测。

### 核心结果

- Hybrid Required Recall@10：0.8401。
- Rerank Required Recall@10：0.9254。
- Rerank P95：6854.7ms。
- 外部 CodeSearchNet 中 Vector nDCG@10：0.5685，为四种方法最高。

### 结论

- 当前内部权重适合 CodePilot，但不能泛化成通用最优。
- 保持默认 RRF、可选 Rerank。
- test-v1 永久冻结，后续不能用于调参。

---

## 2026-08-14：RAG 评测基线提交 dev

### 本轮任务

- 任务 ID：`BASE-001`、`BASE-003`、`BASE-004`。
- 目标：复核有意的未提交改动，把可复现基线安全提交到 `dev` 并验证 CI。

### 审查与复现

- 47 个 `.rag-eval` JSON 文件均可解析，结果文件约 1.34 MB。
- 冻结集 SHA 与 manifest 一致，密钥扫描通过。
- `.env`、模型缓存、外部数据、临时 worktree 均未纳入提交。
- `install.py` 未修改、未暂存。
- 全量测试：89 passed、3 skipped；`git diff --check` 通过。

### 修改

- 将 RAG 实现、冻结集、正式评测结果和项目管理文档提交到 `dev`。
- 发现 `.github/workflows/test.yml` 仅监听 `master/main`，因此 `dev` 推送不会触发 CI；补充 `dev` push/PR 触发条件。
- 首次 dev CI 暴露一个跨平台测试缺陷：测试在 Linux 上使用 Windows `D:/...` 路径并按 Windows 语义断言。改为使用 pytest 的绝对临时目录，继续验证环境覆盖不被重写。
- CI 矩阵设置 `fail-fast: false`，确保一个 Python 版本失败时仍能得到另一个版本的独立结果。

### Git

- 分支：`dev`。
- 基线提交：`f80a8fd35a2ed1d3a572e010e2c6797719c8391c`。
- CI 触发修复提交：`83c5011b5411f0ed7942c39b92de81c05ecaf63f`。
- 跨平台测试修复提交：`32e778e18a0c167e7c1bb709aa033d61494e6355`。
- 是否推送：是，已推送到 `origin/dev`。

### CI 结果

- 首次运行 `31775936026`：Docker 成功；Python 3.11 因非跨平台路径断言失败；Python 3.12 被 fail-fast 取消。
- 修复后运行 `31776231906`：Python 3.11、Python 3.12、Docker 全部成功；两个 Python 作业的测试和导入校验均通过。

### 下一步

- `BASE-004` 已完成；创建 `feat/transactional-edit`，从 `EDIT-001` 开始。

---

## 2026-08-14：事务式代码编辑核心实现

### 本轮任务

- 任务 ID：`EDIT-001` 至 `EDIT-007`。
- 分支：`feat/transactional-edit`。
- 目标：解决 Agent “检索命中但没有安全修改目标文件”的端到端瓶颈。

### 审查与复现

- 现有 `write_file` 只支持完整覆盖，缺少局部匹配次数、SHA 前置条件、语法校验和原子提交。
- 工具注册表支持通过函数、JSON Schema 和风险等级扩展，无需修改 Agent/MCP 核心循环。
- 新增工具后同步发现 MCP 数量测试、CI 导入校验和 Dashboard 的15工具静态值需要更新。

### 修改

- 新增 `EditOperation`、`EditRequest`、`EditResult` 和稳定错误码。
- 实现 workdir 边界、符号链接逃逸、文件类型、UTF-8 和大小检查。
- 实现 expected_count、重叠 edit、可选 SHA 和写入前原字节二次校验。
- 保留 UTF-8 BOM 与 CRLF/LF；Python 文件落盘前执行 `ast.parse`。
- 使用同目录临时文件、`fsync`、`os.replace` 和回读校验；校验失败尝试恢复原始字节。
- 同一进程使用路径分片锁串行同文件事务，避免死锁和状态污染。
- 注册第16个工具并通过 MCP 自动暴露；保留现有 `write_file` 兼容性。
- CI 增加 `workflow_dispatch`，允许功能分支在不提前合并或创建 PR 的情况下手动运行同一测试矩阵。

### 测试

- 定向：42 passed、1 skipped（事务编辑 + 工具注册）。
- MCP 定向：46 passed、1 skipped。
- 全量：116 passed、4 skipped。
- `git diff --check`：通过。
- Windows skip：创建符号链接需要额外权限；Ubuntu CI 应实际运行该用例。

### 风险与遗留

- 单文件事务不等于跨文件事务；多文件任务由未来 Agent 状态机编排。
- SHA 二次校验不能消除原子替换前最后一个极小的跨进程竞态窗口。
- `EDIT-008` 已完成：端到端报告 schema v2 可记录编辑尝试、事务/旧写入、前置失败、写入失败、回滚、错误码、目标文件和实际修改交集。
- 指标目标路径会相对评测 workdir 归一化，避免 `./path` 或绝对路径造成假阴性。
- `EDIT-009` 是付费 DeepSeek 复测，执行前需用户明确授权，且不得修改冻结任务或 Oracle。

### 下一步

- 提交并推送功能分支，等待 CI。
- `EDIT-009` 保持等待用户付费授权；未授权时可进入 M2 设计，但不能宣称 M1 的80%目标已验证。

---

## 新会话日志模板

```markdown
## YYYY-MM-DD：会话标题

### 本轮任务

- 任务 ID：
- 目标：
- 开工假设：

### 审查与复现

- 当前行为：
- 根因：
- 相关文件：

### 修改

- 修改文件：
- 设计选择：
- 回退/兼容策略：

### 测试

- 命令：
- 结果：
- 未运行项及原因：

### 指标

- 修改前：
- 修改后：
- 数据集/环境：

### 风险与遗留

- 风险：
- 遗留任务：

### Git

- 分支：
- 提交 SHA：
- 是否推送：

### 下一步

- 推荐任务 ID：
```
