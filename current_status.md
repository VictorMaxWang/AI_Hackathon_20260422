# GuardedOps Current Status

## 1. 当前阶段

- 当前 Phase：Phase 3.5
- 当前主线：Evo-Lite 安全经验沉淀与自评估闭环
- 当前 Task ID：P3.5-T00
- 当前状态：P3.5-T00 已完成，总控体系已加入 Evo-Lite 阶段、任务编号、边界约束和并行规则

---

## 2. 当前正在推进什么

当前项目在 Phase 3 完成后插入 Phase 3.5，用于沉淀 Evo-Lite 安全经验。

Phase 3.5 的定位是：不改模型权重、不接在线训练、不自动改代码、不自动生成可执行脚本，只把 GuardedOps 的真实执行结果转化为可控的评估、反思、经验记录和安全工作流模板。

---

## 3. 下一步是什么

下一步建议从可并行的 Phase 3.5 任务开始：

1. P3.5-T01：Execution Evaluator；
2. P3.5-T02：Experience Store；
3. P3.5-T04：Safe Workflow Templates。

随后按依赖推进：

- P3.5-T03 依赖 P3.5-T01；
- P3.5-T05 依赖 P3.5-T04；
- P3.5-T06 依赖 P3.5-T01 / P3.5-T02 / P3.5-T03 / P3.5-T05；
- P3.5-T07 最后执行。

---

## 4. 当前阻塞点

| 阻塞项 | 状态 | 解决方式 |
|---|---|---|
| P4/P5 | 暂缓 | Phase 3.5 完成后再恢复审计、演示材料和最终交付任务 |
| P0-T03 | 未完成，暂缓或低优先级 | 后续如需要统一 Codex 执行模板，再单独恢复 |
| Evo-Lite 实现边界 | 已锁定 | 后续任务不得训练模型、不得改 policy/executor、不得扩大执行能力 |
| 目标 Linux 发行版 | 待确认 | 建议 Ubuntu 22.04 / 24.04 或 openEuler |
| SSH 演示机器 | 待确认 | 先本地模式跑通，再接 SSH |
| 是否使用真实 LLM | 待确认 | Phase 3.5 不依赖真实 LLM，也不接在线训练 |

---

## 5. 最新决策

| 日期 | 决策 | 原因 | 影响 |
|---|---|---|---|
| 2026-04-22 | Web 为主演示入口，CLI 为辅助入口 | Web 更适合展示风险、计划、确认和审计；CLI 便于调试 | 双入口共用核心引擎 |
| 2026-04-22 | 禁止 arbitrary shell | 降低安全风险，贴合风控评分点 | 只能调用白名单工具 |
| 2026-04-22 | Prompt 不作为最终风控边界 | Prompt 不稳定，安全必须代码化 | 必须实现 policy engine |
| 2026-04-22 | Phase 1 不依赖真实 LLM | 降低试错和环境依赖 | 先用规则 parser |
| 2026-04-23 | DEC-P35-01：新增 Evo-Lite 阶段，采用不改权重的经验沉淀路线 | 让系统从真实执行中沉淀可审计经验，同时不引入训练和自修改风险 | 新增 Phase 3.5 任务链；P4/P5 暂缓 |

---

## 6. 最近完成内容

| Task ID | 完成内容 | 输出物 | 遗留问题 |
|---|---|---|---|
| P0-T01 | 初始化总控管理文件体系 | 11 个管理文件 | 业务仓库骨架已由 P0-T02 初始化 |
| P0-T02 | 初始化仓库骨架 | 基础目录、README、pyproject.toml、.gitignore、包占位文件 | 当前目录不是 Git 工作区，尚未提交或推送 |
| P3.5-T00 | 更新总控文件以加入 Evo-Lite 阶段 | task_board、current_status、architecture_constraints、decision_log、parallel_workstreams、validation_matrix、project_context、docs/evo_lite_design.md | 不含业务实现；后续从 P3.5-T01 / T02 / T04 开始 |

---

## 7. 当前待办

| 优先级 | Task ID | 任务 | 备注 |
|---|---|---|---|
| P3.5 | P3.5-T01 | Execution Evaluator | 可与 P3.5-T02 / T04 并行 |
| P3.5 | P3.5-T02 | Experience Store | 可与 P3.5-T01 / T04 并行 |
| P3.5 | P3.5-T04 | Safe Workflow Templates | 可与 P3.5-T01 / T02 并行 |
| Low | P0-T03 | 建立任务执行规范 | 暂缓或低优先级，不标记 DONE |
| Deferred | P4/P5 | 审计、演示材料、交付文档 | Phase 3.5 完成后恢复 |

---

## 8. 风险提醒

Phase 3.5 最容易做歪的方向：

- 把 Evo-Lite 做成 LoRA / SFT / DPO / RL 训练；
- 接在线训练或自动改模型权重；
- 让 reflection 自动修改 policy 或 executor；
- 生成可执行脚本或通用 shell 工具；
- 用 workflow 绕过 policy engine、confirmation policy 或工具白名单；
- 把经验记录当作最终 allow / deny 决策来源。

---

## 9. 下次接力摘要模板

```text
项目：GuardedOps
当前 Phase：Phase 3.5
当前 Task：P3.5-T00 已完成
已完成：总控体系已加入 Evo-Lite 阶段、P3.5-T00 至 P3.5-T07、Evo-Lite 约束、DEC-P35-01、并行规则和设计说明
正在做：等待进入 P3.5-T01 / P3.5-T02 / P3.5-T04
下一步：优先并行推进 Execution Evaluator、Experience Store、Safe Workflow Templates
当前阻塞：P4/P5 暂缓；P0-T03 暂缓或低优先级
关键约束：
- 不改模型权重
- 不训练 LoRA / SFT / DPO / RL
- 不自动修改 policy 或 executor
- 不自动生成可执行脚本
- 不创建 run_shell_tool
- workflow 只能建议 planner，且只能调用白名单工具
- reflection 只能写入经验，不得更改系统边界
需要新线程重点关注：只按 Task ID 推进 Phase 3.5，不要扩大执行能力
```
