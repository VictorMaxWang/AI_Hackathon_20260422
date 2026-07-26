# GuardedOps Project Context

## 1. 给新线程 / 新 AI 的快速说明

你正在参与 GuardedOps 项目。

GuardedOps 是 AI Hackathon 2026《操作系统智能代理》的参赛项目，目标是做一个真实可运行、可演示、可审计的“安全对话式 Linux/SSH 运维代理”。

它不是一个万能 Shell Chatbot。它不会把用户自然语言直接变成 bash 执行。它的核心价值是：

- 理解自然语言系统管理需求；
- 将需求转成结构化意图；
- 基于真实 Linux 环境做安全判断；
- 只调用白名单工具；
- 对高风险操作进行拒绝、解释、确认或范围限制；
- 执行后返回可解释、可审计、可复现的结果。

---

## 2. 赛题核心要求摘要

赛题要求构建操作系统智能代理，能够在真实 Linux 环境中运行，可本地部署，也可通过 SSH 远程代理操作。

基础能力包括：

- 磁盘使用情况监测；
- 文件或目录检索；
- 进程及端口状态查询；
- 普通用户的创建与删除。

进阶能力强调：

- 高风险或敏感操作识别；
- 风险预警；
- 二次确认；
- 操作范围限制或截断；
- 拒绝不合理或非法高风险指令；
- 行为可解释。

探索方向包括：

- 多轮对话；
- 多步连续任务；
- 去命令行化体验；
- 多模态交互。

GuardedOps 当前不优先做多模态。

---

## 3. 当前方案

采用混合形态：

- Web 为主演示入口；
- CLI 为调试入口；
- LocalExecutor 支持本地执行；
- SSHExecutor 支持远程执行（**目前只是库级能力**：两个入口都硬编码 `LocalExecutor`，没有地方能构造 `SSHConnectionConfig`）；
- 规则风控优先；
- LLM 只用于辅助理解和解释，不作为安全边界；
- 执行层只允许白名单工具；
- 所有敏感操作必须经过风险评估和确认；
- 所有操作必须记录审计日志（**目标**；当前只有响应内的证据链，没有落盘审计）。

---

## 4. 当前核心架构

GuardedOps 分为五层：

1. 交互层
   - Web Chat Console
   - CLI Debug Tool

2. Agent 层
   - Rule-based Parser
   - Optional LLM Parser Stub
   - Planner
   - Orchestrator
   - Session Memory
   - Summarizer

3. 安全风控层
   - Risk Engine
   - Validators
   - Protected Path Rules
   - Username Rules
   - Confirmation Policy
   - Scope Limiter

4. 执行层
   - BaseExecutor
   - LocalExecutor
   - SSHExecutor
   - Tool wrappers

5. 审计层（**目标架构，尚未实现**）
   - SQLite Audit Store
   - JSONL Audit Log
   - Audit Viewer / Exporter

   `app/audit/` 目前是空包。当前可审计信息只体现为单次响应内的证据链（`evidence_chain`，`app/models/evidence.py`），不落盘、不可跨请求查询、进程重启即丢失。这一层属于 P4-T01 / P4-T02，任何提交材料都不得把它描述为已交付。

---

## 5. Phase 3.5：Evo-Lite 安全经验沉淀

Phase 3.5 插入在 Phase 3 之后、P4/P5 之前，用于把 GuardedOps 的真实执行结果沉淀为可控经验。

Evo-Lite 的定位：

- 不改模型权重；
- 不训练 LoRA / SFT / DPO / RL；
- 不接在线训练；
- 不自动修改 policy 或 executor；
- 不自动生成可执行脚本；
- 只生成评估、反思、经验记录和安全 workflow 模板；
- workflow 只能建议 planner，最终执行仍必须经过 policy 和白名单工具。

Phase 3.5 后续任务从 P3.5-T01 / P3.5-T02 / P3.5-T04 并行启动。

### Phase 3.6：可信控制面、证据层与鲁棒闭环

Phase 3.6 在当前阶段不继续扩工具面，而是把优化重点转向：

- 安全解释与证据链；
- 确认绑定与执行闭包；
- 连续任务鲁棒性与断点续跑；
- 失败恢复建议、经验治理与可重放安全回归；
- 可视化可信控制面。

Phase 3.6 仍然坚持既有安全边界：不开放 arbitrary shell，不开放 raw command mode，不做在线 RL 或高风险微调，不自动修改 policy / executor，也不自动生成可执行 shell 脚本。

---

## 6. 当前必须覆盖的工具能力

白名单工具包括：

- env_probe_tool（已实现）
- disk_usage_tool（已实现）
- memory_usage_tool（已实现）
- file_search_tool（已实现）
- process_query_tool（已实现）
- port_query_tool（已实现）
- create_user_tool（已实现）
- delete_user_tool（已实现）
- audit_query_tool（**未实现**：意图名在 `app/policy/rules.py` 的白名单里，但 `app/tools/` 下没有对应模块，依赖尚未实现的审计层）

禁止实现 arbitrary command tool。

---

## 7. 当前主要 Demo 场景

### 场景 1：磁盘查询

用户说：

> 帮我查看当前磁盘使用情况，指出哪个挂载点最紧张。

系统展示：

- 意图识别；
- S0 风险；
- df 结果；
- 自然语言总结。

### 场景 2：文件检索

用户说：

> 在 /var/log 里找最近 3 天修改过、文件名包含 nginx 的文件，最多返回 20 条。

系统展示：

- 搜索范围；
- 最大深度；
- 最大返回数量；
- 截断说明。

### 场景 3：端口查询

用户说：

> 8080 端口现在是谁在占用？告诉我 PID、进程名和所属用户。

系统展示：

- 端口监听状态；
- PID；
- 进程；
- 用户。

### 场景 4：创建普通用户

用户说：

> 创建一个普通用户 demo_guest，创建 home 目录，不要给 sudo 权限。

系统展示：

- S1 风险；
- 操作计划；
- 确认语；
- 执行后验证。

### 场景 5：拒绝高风险操作

用户说：

> 把 /etc 下面没用的配置删掉，越快越好。

系统必须拒绝并解释。

### 场景 6：多轮连续任务

用户先查询环境，再创建 `demo_temp`，再删除刚才那个用户，并解释为什么删除更敏感。

系统展示：

- 环境感知；
- 上下文引用；
- 条件执行；
- 创建后验证；
- 删除前强确认；
- 删除后验证。

---

## 8. 已知限制

当前不做：

- 语音输入；
- 图像理解；
- 复杂多 Agent；
- Kubernetes 管理；
- Docker 管理；
- 防火墙修改；
- sudoers 修改；
- sshd_config 修改；
- 通用 shell 模式；
- 自动修复系统配置。

---

## 9. 技术选型初稿

实际使用：

- Python 3.11 / 3.12 / 3.13（CI 三个版本都跑）
- FastAPI + Uvicorn
- Pydantic v2
- Paramiko（`SSHExecutor`，目前无入口可达）
- pytest（`[test]` extra，连同 `httpx`）
- 标准库 `argparse` 做 CLI，未引入 Typer
- setuptools + `pyproject.toml` 管理依赖，未引入 uv / poetry / pip-tools
- HTML/CSS/JS 单页 Web UI，无前端构建步骤
- 可选：OpenAI SDK（`[llm]` extra）对接 DashScope Qwen3.6-Plus

已确认：

- **是否使用 Typer**：否，用标准库 `argparse`，避免为一个调试入口增加依赖。
- **依赖管理工具**：`pyproject.toml` + pip，`[test]` / `[llm]` 两个 extra。
- **是否启用真实 LLM API**：启用但默认关闭，只作为意图候选来源。

仍待确认：

- SSH 认证优先使用 key 还是 password（`SSHConnectionConfig` 两者都支持，但没有入口能配置）；
- 演示系统发行版。

未使用：

- SQLite 目前只被 `app/evolution/experience_store.py` 用于经验存储，**没有**用于审计层——审计层尚未实现。

---

## 10. 当前成功标准

项目成功不是“功能很多”，而是：

- 真实 Linux 可运行；
- Web 可演示；
- CLI 可辅助验证；
- 基础四类能力可执行；
- 创建/删除普通用户可确认、可验证；
- 高风险操作能拒绝并解释；
- 多轮连续任务可闭环；
- 审计日志可查看（**尚未达成**，审计层未实现）；
- 提交文档完整，且不声称未实现的能力。

---

## 11. 新线程开始前必须读取

这些文件都在 `docs/process/`（本文件所在目录）。新 AI 线程开始前，请先读取：

1. `agent.md`
2. `project_context.md`
3. `architecture_constraints.md`
4. `current_status.md`
5. `validation_matrix.md`（特别是第 6 节“未交付能力”）
6. 当前 Task 的 Codex Prompt（在仓库根的 `prompts/`）
7. 如涉及并行开发，读取 `parallel_workstreams.md`

不要基于记忆或猜测继续开发。
