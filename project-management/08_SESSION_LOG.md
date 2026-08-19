# CodePilot 开发会话日志

本文件按时间倒序记录关键开发过程。每次开发结束都应增加一条，不覆盖旧记录。

---

## 2026-08-19：STATE-005～STATE-008 状态机控制闭环

### 本轮任务

- 在 `feat/agent-state-machine` 的 `305d52e` 干净基线上完成结构化恢复、最新证据门槛、Diff Review 和外部接口兼容验证。
- 保持增量设计，不重写 Agent 主循环，不增加 LLM 分类器，不放宽 API/MCP 危险工具策略。

### 审查与实现

- 新增 `RecoveryAction`、`RecoveryDecision` 和纯函数错误映射：SHA/并发冲突重读，匹配失败重新定位，Python 语法失败修订编辑，测试失败分析，超时有限重试，拒绝验证请求批准，不可恢复写入/安全错误终止。
- recovery budget 在每次进入 RECOVER 前实际生效；恢复与缺证据 directive 使用固定模板、最长500字符，只临时注入下一次模型请求。
- 新增 `CompletionDecision`；模型提前回答若缺少编辑、最新测试或 review，不立即 FAILED，而是继续到 EDIT/VERIFY/REVIEW，最终只在迭代/预算耗尽、不可恢复错误或验证不可用时失败。
- 每次真实字节变化推进 `mutation_revision` 并作废旧测试/review；dry-run、no-op、失败、rollback 和预算拒绝不推进。旧 `write_file` 保持兼容并记录 legacy mutation。
- pytest 返回码0绑定当前 `verified_revision`；其后的实际非空 `git_diff` 才绑定 `reviewed_revision`，记录有界 reviewed paths 并检查明显路径不相交。
- 工具执行前启用阶段预算；同一 AIMessage 中每个调用无论执行、失败、超预算或终态拒绝都生成匹配 ToolMessage。
- `agent.run` 增加末尾可选 `task_mode`，不破坏旧位置参数；FastAPI `SubmitRequest` 增加默认 `auto` 的可选字段；冻结缺陷评测 worker 显式使用 `mutation_required`。
- API 继续自动拒绝 `run_shell`；显式 mutation 无验证通道时以 `verification_unavailable` failed 事件结束。MCP 危险工具默认仍为 `isError=true`。

### 测试

- 状态机定向：`tests/test_execution_state.py` + `tests/test_agent_state_machine.py`，35 passed。
- CLI/API/MCP、上下文、事件、执行器、工具、Worktree 和评测器兼容回归：通过。
- 全量：182 passed、4 skipped（前一基线151 passed、4 skipped，新增31个通过用例）。
- `git diff --check`：通过，仅有既有 Windows LF/CRLF 与用户级 ignore 权限警告。
- 冻结 `codepilot-test-v1` 与 `agent-tasks-v1` SHA 复核不变；`install.py` 未修改。

### 评测与保护

- 未调用 DeepSeek 或任何付费/真实模型 API，未下载模型，未运行冻结正式评测，未生成或覆盖正式结果。
- 未修改 `.rag-eval/codepilot-test-v1.json`、`.rag-eval/agent-tasks-v1.json`、冻结 Oracle、`install.py` 或 `.env`。
- 未 push、未触发 CI；本轮实现与确定性验证完成，真实重复评测待用户新授权。

### 风险与下一步

- `auto` 无法可靠识别“模型完全没有尝试编辑”的修改请求；已知 mutation 调用方应显式声明模式。
- API 缺少安全 TestRunner，不能通过放开通用 shell 解决。
- 当前没有统一 `allowed_files`，完整 diff 范围判定留给 M3 Trace/新版任务协议。
- 下一项：`TRACE-001`。

---

## 2026-08-17：STATE-001～STATE-004 执行状态基础

### 本轮任务

- 从未合并的 `feat/transactional-edit` 提交 `810cc26` 创建 `feat/agent-state-machine`，未合并或修改 `dev/master`。
- 审查 `AgentSession.run`、工具分发、TaskEvent、CLI/API/MCP 和评测错误传播；确认一轮模型响应可含多个顺序工具调用，`max_iterations` 按模型调用计数。
- 实现可靠、可测试、非侵入式的 M2 第一批状态基础，不重写整个 Agent 循环。

### 修改

- 新增 `execution_state.py`：定义 INIT、DISCOVER、INSPECT、PLAN、EDIT、VERIFY、RECOVER、REVIEW、COMPLETE、FAILED 和显式合法转移。
- 新增 `TaskExecutionState`、`TransitionRecord`、`PhaseBudgets`、`TaskMode` 与预算决策；每个 Agent 用户轮次状态隔离，转移历史最多500条。
- 真实 search/read/edit/test 工具结果驱动计数和阶段；事务编辑失败、测试失败进入 RECOVER，恢复后可重新 EDIT/VERIFY。
- `run_shell` 在截断输出之外稳定追加 `[returncode] N`；只有真实 pytest 命令返回码0才记录验证成功。
- `mutation_required` 必须有成功编辑和验证证据才能 COMPLETE；`read_only` 不强制编辑；默认 `auto` 保持既有调用方兼容。
- 多 tool call 仍保持一条 AIMessage 后跟全部对应 ToolMessage，不在消息链中插入状态对象。

### 测试

- 新增状态机测试：17 passed。
- 上下文、事件、执行器、工具回归：33 passed。
- 全量：151 passed、4 skipped（基线133 passed、4 skipped，新增18个通过用例）。
- `git diff --check`：通过，仅有既有 Windows LF/CRLF 提示。
- 使用 fake model、fake tool result 和确定性事件序列；未调用真实模型或付费 API。

### 兼容性与保护

- CLI、API、MCP 的既有调用方式保持兼容；API 创建状态时补充真实 task_id。
- 对外可见的唯一工具文本变化是 `run_shell` 末尾稳定返回码标记。
- 未修改 `.rag-eval/codepilot-test-v1.json`、`.rag-eval/agent-tasks-v1.json`、`install.py` 或 `.env`；未运行冻结评测，未下载模型。

### 风险与下一步

- 当前预算是统计和明确决策接口，尚未在执行前强制阻止超预算工具调用；不能据此宣称端到端成功率提高。
- 下一项：`STATE-005`，把 RECOVER 从可观察阶段推进为测试失败后的结构化恢复策略；随后 `STATE-006` 完成前强制 diff review。
- 完成 STATE-005～008 后再评估付费重复复测；任何真实 API 调用仍需新授权。

---

## 2026-08-17：EDIT-009 正式复测完成与超时进程树修复

### 正式运行

- 从 `agent-v2-transactional` 的31/40断点恢复，完成剩余9个 DeepSeek Agent 样本。
- 20个冻结任务 × Hybrid/Rerank 共40份报告齐全；manifest 状态为 `completed_with_worker_failures`。
- 冻结任务 SHA-256 仍为 `71caa70e7b441380c79745c701bb02a77f8b4d0efcfb2d892b3a91f053d7ac09`，被测提交仍为 `daee3cc1f4c7c8226d173fd7b295c32d1b2d5c1f`。
- A16-Hybrid 的900秒超时作为正式失败保留，没有选择性重跑；旧 `agent-v1` 未覆盖。

### 结果

- 严格成功：Hybrid 11/20（55%），Rerank 8/20（40%）。
- 目标文件修改：Hybrid 14/20（70%），Rerank 12/20（60%），未达到 M1 的80%目标。
- 事务编辑采用率：有编辑的26/26个任务均使用 `edit_file_transaction`；旧 `write_file` 为0。
- 事务调用结果：Hybrid 17/17、Rerank 15/15成功；没有前置失败、写入失败或回滚。
- 可比 Agent 延迟：Hybrid Avg/P95/Max 26.2/42.6/42.6秒；Rerank 31.0/43.5/72.4秒。A16 的900.1秒是不同 timing scope 的 worker 异常，单独报告。
- 成对严格结果：两边成功5、仅 Hybrid 6、仅 Rerank 3、两边失败6。

### 可信度限制

- 实际调用语义检索的任务只有 Hybrid 11/20、Rerank 14/20，所以这不是纯 RAG 排序 A/B。
- 每个条件单次运行，LLM 轨迹非确定，不能宣称15个百分点差异具有因果性或统计显著性。
- A07/A10/A18 出现功能等价但未逐字恢复 Oracle 的修复；严格结果保留，不修改冻结答案。
- v1 没有 `allowed_files`，部分测试文件修改被标为 unexpected，不能直接等同于越权。

### A16 故障与修复

- Agent 的 `python` 命中了缺少依赖的系统解释器，随后执行 `python -m pip install sentence-transformers`。
- 原 `run_shell` 超时只终止外层 shell，遗留 pip 子进程并持有 Chroma 文件；外层 runner 最终在900秒终止。
- 已确认系统 Python 没有成功安装该包，项目 venv 正常，评测/安装残留进程为0。
- 修复 `run_shell` 与 runner：独立进程组、超时终止整棵进程树、Windows 文件锁清理重试、worker failure synthetic 报告和可恢复 manifest。
- 未来 worker 强制项目 venv 位于 PATH 首位，并设置 PIP/Hugging Face 离线变量；本次原始结果不回写、不重跑。

### 验证与下一步

- 定向测试：28 passed。
- 全量回归：133 passed、4 skipped。
- 首次手动 CI 中 Python 3.12 与 Docker 通过，Python 3.11 暴露 `shutil.rmtree(onexc=...)` 版本兼容问题；改用兼容3.11/3.12的 `onerror` 后，CI run `31990221352` 的 Python 3.11、3.12 与 Docker 全部通过。
- `install.py` 未修改；密钥、模型缓存和临时目录不提交。
- `EDIT-009` 标记完成但 M1 记录为 `DONE_WITH_GAP`；下一项为 M2 状态机，重点解决定位后未编辑、平台命令浪费步数和测试恢复。

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

### Git 与 CI

- 功能提交：`fac8de0983f08ae53edad92acd2994e684828beb`。
- 远端分支：`origin/feat/transactional-edit`。
- GitHub Actions：`31778063773`，Python 3.11、Python 3.12、Docker 三个作业全部成功。
- Linux Python 3.11：117 passed、3 skipped；符号链接逃逸用例实际通过。
- Windows 本地：116 passed、4 skipped；差异仅为本地符号链接权限。

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

## 2026-08-14：EDIT-009 付费复测准备

### 本轮任务

- 用户已明确授权使用真实模型 API。
- 正式计划：20个冻结任务 × Hybrid/Rerank，共40次 DeepSeek Agent 调用。
- 新 run-id：`agent-v2-transactional`；旧 `agent-v1` 结果禁止覆盖。

### 评测器增强

- 增加安全 run-id 校验，结果目录相互隔离。
- 默认拒绝覆盖已有结果；`--resume` 支持中断恢复，`--overwrite` 必须显式提供且不能与 resume 共用。
- manifest 记录冻结任务 SHA、代码提交、分支、模型、条件、预期/完成数量和 worker failure。
- 每个任务完成后原子更新 manifest，全部结束后自动生成 condition 与 paired 汇总。
- 运行结束再次校验冻结任务 SHA；变化时拒绝生成正式汇总。

### 计费前验证

- 冻结任务：20条，SHA-256 `71caa70e7b441380c79745c701bb02a77f8b4d0efcfb2d892b3a91f053d7ac09`。
- DeepSeek Key：已配置，未输出内容。
- Cross-Encoder：D盘本地缓存加载成功，19.5秒，无下载。
- harness 定向测试：16 passed。
- 全量回归：127 passed、4 skipped。
- 计划模式：40 runs。

### 下一步

- 提交并推送 harness，随后执行 `agent-v2-transactional` 正式复测。

---

## 2026-08-19：Qwen 3.7 Flash 接入与付费 Pilot

### 本轮任务

- 用户授权配置阿里云百炼免费额度 Key，并执行真实模型测试。
- 新增 `qwen3.7-flash` OpenAI-compatible 路由，保留现有模型自动选择优先级。

### 修改与验证

- `.env` 配置 `DASHSCOPE_API_KEY` 与北京共享兼容端点；文件继续被 `.gitignore` 忽略，密钥未回显。
- 新增 ModelRouter 路由测试；最小连通调用成功，共 206 tokens。
- A01 Hybrid 第一次 pilot 暴露评测 Git 基线导致修复后空 diff；worker 改为提交注入缺陷作为隔离 review 基线，并增加确定性 Git 测试。
- 第二次 pilot 取得成功编辑、测试与非空 diff，但 Qwen 在第 10 次响应才执行 review，最终按 max-iterations 返回。
- 两次 Oracle 均成功，冻结任务与 Oracle 未修改；没有扩大到完整付费重复评测。

### Git 与下一步

- 分支：`feat/agent-state-machine`；未提交、未推送、未运行 CI。
- 下一项：`TRACE-001`，优先观测最后一次工具后的证据与 completion decision；解决 RISK-017 后再决定扩大付费评测。

---

## 2026-08-19：TRACE-001 任务级结构化执行轨迹

### 本轮任务

- 任务 ID：`TRACE-001`。
- 目标：定义并接入 Phase/Retrieval/Edit/Test Trace，解释真实 Agent 失败发生前的客观执行轨迹。

### 修改

- 新增 `task_trace.py`：`TaskTrace`、`PhaseEvent`、`RetrievalTrace`、`EditTrace`、`TestTrace` 和 schema version。
- 每轮模型调用记录 iteration；合法阶段转移、Diff Review、completion decision 均记录稳定事件。
- 检索只记录工具名与成功/error code；读取只记录安全路径；编辑记录 byte change、rollback、revision；测试记录 returncode 与 revision。
- `execution_state.snapshot()`、Server completed/failed 事件和评测 schema v2 可选携带独立 Trace；旧回调和响应字段不变。
- `failure_stage` 暂不推断，留给 TRACE-004；没有改变 max_iterations 或增加模型调用。

### 测试与安全

- 定向兼容：79 passed、3 skipped。
- 全量：191 passed、4 skipped；`git diff --check` 通过。
- Trace 不保存 query、源码、diff、shell 输出或模型上下文；`.env` 路径使用占位符。
- 冻结任务与 Oracle 未修改；本轮未调用付费 API、未生成新评测结果。

### Git 与下一步

- 分支：`feat/agent-state-machine`；未提交、未推送、未运行 CI。
- 下一项：`TRACE-002`，集中实现 Trace 脱敏和长度限制策略。

---

## 2026-08-19：完成 TRACE-002～TRACE-006 与 M3

### 本轮任务

- 完成 Trace 脱敏/长度、阶段漏斗、主失败分类、环境/代码区分及 Dashboard/metrics 聚合。
- 保持现有 Agent、API、MCP 和评测报告必填字段向后兼容；不调用真实模型。

### 修改

- `task_trace.py`：统一标识符/路径/字符串脱敏，检索只提取有界文件路径，所有列表和字符串有硬边界。
- 新增 `trace_analysis.py`：十级评测漏斗、十类唯一失败阶段、次要原因、失败域和在线 Trace 聚合。
- `execution_state.py`：终态时立即写入确定性失败分类；最后一轮 review 与 completion decision 继续可区分。
- Agent worker/runner：新报告写入 expected files、Agent 终态和失败分析；synthetic worker failure 也有 environment 分类。
- Server/TaskQueue：每个任务保存隔离 Trace；Worktree/模型/Server 失败生成环境 Trace；Prometheus 暴露漏斗和失败标签。
- Dashboard：新增 Trace 执行漏斗卡片，不读取原始工具输出或 Trace 内容。

### 验证

- 定向 Trace/评测/API：92 passed、3 skipped。
- 全量：211 passed、4 skipped。
- 旧40份正式报告只读兼容汇总成功，21个既有失败获得主分类；未覆盖历史结果文件。
- `git diff --check`、密钥扫描通过；冻结任务和 Oracle 零差异。

### Git 与下一步

- 分支：`feat/agent-state-machine`；未提交、未推送、未运行 CI。
- 本轮未调用付费 API，未生成新正式结果。
- M3 完成；下一任务 `ROUTE-001`。

---

## 2026-08-19：ROUTE-001 确定性查询特征层

### 本轮任务

- 任务 ID：`ROUTE-001`。
- 目标：实现确定性、可解释、可测试的 `QueryFeatures`，只回答“查询是什么样”，不实现检索路由或动态调权。
- 基线：从 `7c3f0265609983b0aa5b28a741c3297fc517ff3e` 创建本地 `feat/adaptive-retrieval`；开工时上游差异0/0，工作区仅有受保护的未跟踪 `resume-output/`。

### 修改

- 新增 `rag/query_features.py`：不可变、可序列化的 `QueryFeatures` 与纯函数 `extract_query_features`。
- 字段包括完整字符数、有界分析长度/长度桶、词元数、标识符/自然语言比例、中英文字符比例、混合语言，以及路径、配置键、错误、堆栈和跨模块启发式与固定 reason codes。
- 标识符由点号符号、文件/路径、函数调用、snake_case、camelCase、类名和大写常量确定；跨模块由显式短语、多文件引用或多个代码引用加关系词确定。
- 最多分析前16,384个 Unicode 码点；空分母为0.0，比例限制在 `[0.0, 1.0]`；结果不保存原始 query，超长输入仅保存精确长度和截断标记。
- 新模块只依赖标准库，未接入 `rag.retriever`，没有改变 BM25、Vector、Weighted RRF 或 Rerank 的运行时行为。

### 测试与安全

- QueryFeatures 定向：32 passed。
- Retriever/Reranker/Evaluate/Indexer/Config/Tools 相关回归：64 passed。
- 全量：243 passed、4 skipped；`git diff --check` 通过。
- 覆盖空白、标识符、点号键、POSIX/Windows 路径、文件行号、Python traceback、异常、纯中/英文、中英混合、代码命名、跨模块、超长、Unicode、比例边界、不可变/无原文、重复调用和禁止模型导入。
- 未读取 test-v1 结果设计阈值，未修改冻结数据或正式结果；未下载/加载模型，未联网，未调用付费 API。
- `resume-output/` 未读取、未修改、未暂存。

### 限制与下一步

- CJK/英文比例使用显式字符范围；路径、错误、堆栈和跨模块均为词法启发式，不是语义真值，可能存在误报或漏报。
- 本轮未实现 `RetrievalPlan`、动态权重、RerankPolicy、置信信号或评测调参。
- 下一任务：`ROUTE-002`。

---

## 2026-08-19：ROUTE-002 RetrievalPlan 数据契约

### 本轮任务

- 任务 ID：`ROUTE-002`。
- 目标：定义确定性、不可变、可解释、可序列化的检索计划，只建立计划契约，不提前实现置信信号、路由器或 RerankPolicy。
- 基线：`feat/adaptive-retrieval` 的 `e2b33a3aad655612fa45af2c7eda7edc3747615e`；开工时工作区仅有受保护的未跟踪 `resume-output/`。

### 修改

- 新增 `rag/retrieval_plan.py`：`RetrievalPlan`、schema version、安全上限、JSON-ready `to_dict()` 和固定 RRF 兼容基线计划。
- 权重只接受有限非负数且不能双零；`rrf_k` 限制1～10,000，候选数限制1～100；文档/Rerank 标志只接受真正的 bool。
- 原因必须非空、单行且不超过500字符；reason codes 最多16个、唯一并使用稳定小写标识符。
- 基线计划与 `config.DEFAULTS` 当前参数完全一致并由测试锁定，但 `rag.retriever` 未导入或消费计划，运行时行为不变。
- 新增 `tests/test_retrieval_plan.py`，覆盖正常计划、纯 BM25/Vector 权重边界、NaN/无穷、类型混淆、上限、不可变性、序列化、解释边界、默认同步和禁止运行时依赖。

### 测试与安全

- RetrievalPlan 定向：56 passed。
- QueryFeatures/Retriever/Reranker/Evaluate/Indexer/Config/Tools 相关回归：152 passed。
- 全量：299 passed、4 skipped；`git diff --check` 通过。
- 未加载模型、未联网、未调用付费 API；未运行正式 RAG 评测或读取 test-v1 结果调参。
- 冻结数据、Oracle、正式结果和 `install.py` 未修改；`resume-output/` 未读取、未修改、未暂存。

### 限制与下一步

- `reason` 的硬边界可以阻止无限文本，但无法判断调用方是否语义上复制 query；未来路由器必须只使用固定解释模板和 reason codes。
- 本轮没有计算两路排名重合、Top-1 一致性、标识符覆盖、向量 margin 或文件多样性，也没有改变检索选择。
- 下一任务：`ROUTE-003`。

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
