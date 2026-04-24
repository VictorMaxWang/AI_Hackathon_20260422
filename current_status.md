# GuardedOps Current Status

## 1. 当前阶段

- 当前 Phase：Phase 3.6
- 当前主线：可信控制面、证据层与鲁棒闭环
- 当前 Task ID：P3.6-T00
- 当前状态：P3.6-T00 已完成，总控体系已纳入 Phase 3.6、任务编号、约束边界、并行规则与目标验证项

---

## 2. 当前正在推进什么

当前项目已在 P0 ~ P3.5 之后切换到 Phase 3.6，优化重点从继续扩工具面转向可信控制闭环建设。

Phase 3.6 的定位是：增强解释卡与证据链、确认绑定与执行闭包、连续任务鲁棒性与断点续跑、失败恢复建议、经验治理、可重放安全回归，以及可视化可信控制面；同时不开放 arbitrary shell、不开放 raw command mode、不自动修改 policy/executor，也不做在线 RL 或高风险微调。

---

## 3. 下一步是什么

下一步建议从 Phase 3.6 的串行入口任务开始：

1. P3.6-T01：Evidence Layer Schema & Explanation Card Backend。

随后按依赖推进：

- P3.6-T02 与 P3.6-T04 可在 P3.6-T01 之后并行；
- P3.6-T03 依赖 P3.6-T01 / P3.6-T02；
- P3.6-T05 依赖 P3.6-T03 / P3.6-T04；
- P3.6-T06 与 P3.6-T07 可在 P3.6-T05 之后并行；
- P3.6-T08 最后执行。

---

## 4. 当前阻塞点

| 阻塞项 | 状态 | 解决方式 |
|---|---|---|
| P4/P5 | 暂缓 | Phase 3.6 完成后再恢复审计、演示材料和最终交付任务 |
| P0-T03 | 未完成，暂缓或低优先级 | 后续如需要统一 Codex 执行模板，再单独恢复；不标记 DONE |
| Phase 3.6 安全边界 | 已锁定 | 后续任务不得开放 arbitrary shell / raw command mode，不得绕过 policy，不得自动修改 policy/executor |
| 目标 Linux 发行版 | 待确认 | 建议 Ubuntu 22.04 / 24.04 或 openEuler |
| SSH 演示机器 | 待确认 | 先本地模式跑通，再接 SSH |
| 是否使用真实 LLM | 待确认 | Phase 3.6 不依赖真实 LLM，也不引入在线 RL 或高风险微调 |

---

## 5. 最新决策

| 日期 | 决策 | 原因 | 影响 |
|---|---|---|---|
| 2026-04-22 | Web 为主演示入口，CLI 为辅助入口 | Web 更适合展示风险、计划、确认和审计；CLI 便于调试 | 双入口共用核心引擎 |
| 2026-04-22 | 禁止 arbitrary shell | 降低安全风险，贴合风控评分点 | 只能调用白名单工具 |
| 2026-04-22 | Prompt 不作为最终风控边界 | Prompt 不稳定，安全必须代码化 | 必须实现 policy engine |
| 2026-04-22 | Phase 1 不依赖真实 LLM | 降低试错和环境依赖 | 先用规则 parser |
| 2026-04-23 | DEC-P35-01：新增 Evo-Lite 阶段，采用不改权重的经验沉淀路线 | 让系统从真实执行中沉淀可审计经验，同时不引入训练和自修改风险 | 新增 Phase 3.5 任务链；P4/P5 暂缓 |
| 2026-04-23 | DEC-P36-01：新增 Phase 3.6，优先建设可信控制面与证据层 | 当前 ROI 更高的是提升可信解释、确认绑定、鲁棒闭环与可重放回归，而不是继续扩大能力面 | 新增 Phase 3.6 任务链；P4/P5 继续暂缓；执行面边界保持不变 |

---

## 6. 最近完成内容

| Task ID | 完成内容 | 输出物 | 遗留问题 |
|---|---|---|---|
| P0-T01 | 初始化总控管理文件体系 | 11 个管理文件 | 业务仓库骨架已由 P0-T02 初始化 |
| P0-T02 | 初始化仓库骨架 | 基础目录、README、pyproject.toml、.gitignore、包占位文件 | 当前目录不是 Git 工作区，尚未提交或推送 |
| P3.5-T00 | 更新总控文件以加入 Evo-Lite 阶段 | task_board、current_status、architecture_constraints、decision_log、parallel_workstreams、validation_matrix、project_context、docs/evo_lite_design.md | 不含业务实现；Phase 3.5 任务板已按收口口径归档 |
| P3.6-T00 | 更新总控文件并加入 Phase 3.6 | task_board、current_status、architecture_constraints、parallel_workstreams、decision_log、validation_matrix、project_context、docs/phase_3_6_design.md | 不含业务实现；后续从 P3.6-T01 开始 |

---

## 7. 当前待办

| 优先级 | Task ID | 任务 | 备注 |
|---|---|---|---|
| P3.6 | P3.6-T01 | Evidence Layer Schema & Explanation Card Backend | Phase 3.6 串行入口任务 |
| P3.6 | P3.6-T02 | Guarded Confirmation Token & Scope Binding | 依赖 P3.6-T01；可与 P3.6-T04 并行 |
| P3.6 | P3.6-T04 | Experience Governance Guardrails | 依赖 P3.6-T01；可与 P3.6-T02 并行 |
| P3.6 | P3.6-T03 | Step Contracts, Drift Revalidation & Checkpoint Resume | 依赖 P3.6-T01 / T02 |
| P3.6 | P3.6-T05 | Failure Recovery Taxonomy & Suggestion Engine | 依赖 P3.6-T03 / T04 |
| P3.6 | P3.6-T06 | Replayable Safety Regression & Red-Team Harness | 依赖 P3.6-T05；可与 P3.6-T07 并行 |
| P3.6 | P3.6-T07 | Operator Control Panel UX I | 依赖 P3.6-T05；可与 P3.6-T06 并行 |
| P3.6 | P3.6-T08 | Operator Control Panel UX II | 依赖 P3.6-T06 / T07 |
| Low | P0-T03 | 建立任务执行规范 | 暂缓或低优先级，不标记 DONE |
| Deferred | P4/P5 | 审计、演示材料、交付文档 | Phase 3.6 完成后恢复 |

---

## 8. 风险提醒

Phase 3.6 最容易做歪的方向：

- 把 explanation / memory / workflow / reflection 做成绕过 policy 的旁路；
- 把 confirmation token 做成可脱离执行闭包复用的通用授权；
- 把 evidence 写成自由叙述，而不是基于 trace / state assertion / policy events；
- 把 failure recovery suggestion 做成脚本生成器或隐式 raw command mode；
- 让 experience 直接成为 allow / deny 决策来源，绕过隔离、去重和晋升门禁；
- 把 replay、blast radius、policy simulator 做成表层展示，却没有可重放与可审计基础。

---

## 9. 下一次接力摘要模板

```text
项目：GuardedOps
当前 Phase：Phase 3.6
当前 Task：P3.6-T00 已完成
已完成：总控体系已加入 Phase 3.6、P3.6-T00 至 P3.6-T08、Phase 3.6 约束、DEC-P36-01、并行规则、目标验证项和设计说明
正在做：等待进入 P3.6-T01
下一步：先做 Evidence Layer Schema & Explanation Card Backend，再按 T02/T04 -> T03 -> T05 -> T06/T07 -> T08 推进
当前阻塞：P4/P5 暂缓；P0-T03 暂缓或低优先级
关键约束：
- 不开放 arbitrary shell
- 不开放 raw command mode
- explanation / memory / workflow / reflection 不能绕过 policy
- confirmation 必须绑定执行闭包
- evidence 必须优先来自 trace / state assertion / policy events
- workflow 只能调用白名单工具
- experience 必须经过隔离、去重、晋升门禁
- 不自动修改 policy / executor / 风控边界
- 不自动生成可执行 shell 脚本
- benchmark 必须支持回归和重放
需要新线程重点关注：只按 Task ID 推进 Phase 3.6，不要扩大系统能力边界，也不要提前开始 P4/P5
```
