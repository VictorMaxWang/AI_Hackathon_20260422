# GuardedOps Process Docs

这个目录保存 GuardedOps 的**项目治理文档**：安全边界规约、任务板、决策记录，以及项目最初用来协调多个 AI 助手（ChatGPT 总控 / Codex 实现 / Gemini 审查）的流程约定。

它们从仓库根目录搬到这里，是为了让根目录看起来像一个软件项目而不是一个提示词工作区。所有文件内容原样保留，只是换了位置。

评委和第一次读这个仓库的人应该先看根目录的 [`README.md`](../../README.md)，这里的文档是给继续开发这个项目的人看的。

## 工程规约（对代码有约束力）

| 文件 | 作用 | 什么时候读 |
|---|---|---|
| [`architecture_constraints.md`](architecture_constraints.md) | 分层要求、必须做 / 暂不做 / 严禁做、工具白名单、风险等级、路径保护、用户名规则、执行器约束 | 改任何 policy / executor / tool 之前 |
| [`validation_matrix.md`](validation_matrix.md) | 每个能力 ↔ 可执行的 pytest node id ↔ demo 场景；第 6 节列出**未交付能力** | 写提交材料或 README 之前，确认没有夸大 |
| [`task_board.md`](task_board.md) | 全量任务表、状态、依赖、并行标记 | 领取或收尾一个任务时 |
| [`parallel_workstreams.md`](parallel_workstreams.md) | 哪些任务可以并行、哪些必须串行、哪些绝对不能并行 | 多人 / 多线程同时开工前 |

## 项目背景与决策

| 文件 | 作用 |
|---|---|
| [`project_context.md`](project_context.md) | 赛题要求摘要、方案取舍、demo 场景、已知限制 |
| [`decision_log.md`](decision_log.md) | 关键决策及其替代方案（DEC-INIT-01 ~ DEC-P36-01） |
| [`current_status.md`](current_status.md) | 当前阶段、阻塞点、待办 |

## AI 协作流程（历史流程约定）

| 文件 | 作用 |
|---|---|
| [`agent.md`](agent.md) | 项目定位、硬边界、各 AI 角色分工、阶段判断规则、线程分流与污染防护 |
| [`handoff_template.md`](handoff_template.md) | 线程之间的交接模板 |

配套的提示词模板在 [`../../prompts/`](../../prompts/)。

## 设计说明

阶段性设计说明不在这个目录，而在上一层：

- [`../evo_lite_design.md`](../evo_lite_design.md)：Phase 3.5 Evo-Lite 经验沉淀
- [`../phase_3_6_design.md`](../phase_3_6_design.md)：Phase 3.6 可信控制面与证据层
- [`../core_prompt.md`](../core_prompt.md)：实际发货的 LLM 系统提示词及其契约
- [`../llm_provider_qwen.md`](../llm_provider_qwen.md)：可选 Qwen3.6-Plus provider
- [`../sudo_wrapper_deployment.md`](../sudo_wrapper_deployment.md)：写操作 sudo wrapper 的部署模型

## 更新这些文档的规则

1. `validation_matrix.md` 的“审计证据”列只能填**真实存在且通过的 pytest node id**，不能填抽象产物名。
2. 一个能力如果没有实现，就写进 `validation_matrix.md` 第 6 节，状态标 `NOT_IMPLEMENTED`，不要在别处含糊带过。
3. `task_board.md` 的状态必须和 `current_status.md`、`validation_matrix.md` 一致；三者矛盾时以能跑通的测试为准。
4. 只有架构、安全边界、技术选型级别的变化才写 `decision_log.md`。
