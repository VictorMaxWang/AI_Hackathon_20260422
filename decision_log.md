# GuardedOps Decision Log

## 使用说明

此文件记录项目中的关键决策。

需要记录的决策包括：

- 架构方向；
- 安全边界；
- 技术选型；
- 任务范围变更；
- 风险处理策略；
- 演示策略；
- 提交策略。

不需要记录的小事：

- 普通文案调整；
- 小 bug 修复；
- CSS 样式调整；
- 测试名称调整。

---

## 决策记录模板

```markdown
## DEC-YYYYMMDD-XX：决策标题

- 日期：
- 决策人：
- 当前 Phase：
- 关联 Task：
- 决策内容：
- 决策原因：
- 替代方案：
- 为什么不选替代方案：
- 影响范围：
- 风险：
- 后续动作：
```

---

## 已确认决策

## DEC-INIT-01：采用 Web 主入口 + CLI 辅助入口

- 日期：2026-04-22
- 决策人：项目总控
- 当前 Phase：Phase 0
- 关联 Task：P0-T01
- 决策内容：
  - GuardedOps 采用 Web 为主演示入口；
  - CLI 作为开发和调试入口；
  - 两者共用同一套核心引擎。
- 决策原因：
  - Web 更适合展示风险等级、执行计划、二次确认、审计日志；
  - CLI 更适合快速调试和自动化验证。
- 替代方案：
  - 纯 CLI；
  - 纯 Web。
- 为什么不选替代方案：
  - 纯 CLI 不利于去命令行化展示；
  - 纯 Web 不利于开发调试。
- 影响范围：
  - API 层；
  - CLI 入口；
  - Web UI；
  - Orchestrator 需要统一输出结构。
- 风险：
  - 双入口可能增加维护成本。
- 后续动作：
  - 保持核心引擎单一，入口层不得复制业务逻辑。

---

## DEC-INIT-02：禁止 arbitrary shell / raw command mode

- 日期：2026-04-22
- 决策人：项目总控
- 当前 Phase：Phase 0
- 关联 Task：P0-T01
- 决策内容：
  - 不实现任意 shell 执行；
  - 不实现 raw command mode；
  - 不允许用户自然语言直接转换为 bash 并执行。
- 决策原因：
  - 安全边界不可控；
  - 易出现命令注入；
  - 与高风险识别和风控目标冲突。
- 替代方案：
  - 允许用户输入任意命令；
  - 允许 LLM 生成命令并执行。
- 为什么不选替代方案：
  - 演示风险高；
  - 难以审计；
  - 不利于评分中的风控项。
- 影响范围：
  - Executor；
  - Tools；
  - Parser；
  - Policy Engine。
- 风险：
  - 能力范围较窄。
- 后续动作：
  - 用白名单工具覆盖赛题要求能力。

---

## DEC-INIT-03：规则风控优先，Prompt 不做最终安全边界

- 日期：2026-04-22
- 决策人：项目总控
- 当前 Phase：Phase 0
- 关联 Task：P0-T01
- 决策内容：
  - 所有 allow/deny 决策由 policy engine 完成；
  - Prompt 只用于解释、摘要和辅助理解；
  - LLM 输出必须经过 validators。
- 决策原因：
  - Prompt 不稳定；
  - 安全判断需要可测试和可复现；
  - 评委可能关注边界清晰度。
- 替代方案：
  - 让 LLM 判断风险等级。
- 为什么不选替代方案：
  - 难以保证一致性；
  - 难以写自动化测试。
- 影响范围：
  - Agent 层；
  - Policy 层；
  - 文档说明。
- 风险：
  - 解析能力初期较弱。
- 后续动作：
  - 先用规则 parser 覆盖 demo 场景，再预留 LLM fallback。

---

## DEC-INIT-04：Phase 1 不依赖真实 LLM

- 日期：2026-04-22
- 决策人：项目总控
- 当前 Phase：Phase 0
- 关联 Task：P0-T01
- 决策内容：
  - Phase 1 使用规则 parser；
  - 只预留 LLM parser 接口；
  - 不要求真实 API key。
- 决策原因：
  - 降低开发环境复杂度；
  - 优先保证真实 Linux 执行能力；
  - 避免因为模型调用不稳定影响演示。
- 替代方案：
  - 一开始就接 LLM。
- 为什么不选替代方案：
  - 容易拖慢核心闭环实现。
- 影响范围：
  - Parser；
  - Prompt 文档；
  - Demo 场景。
- 风险：
  - 自然语言泛化初期有限。
- 后续动作：
  - Phase 3 预留 LLM JSON parser stub。

---

## DEC-P35-01：新增 Evo-Lite 阶段，采用不改权重的经验沉淀路线

- 日期：2026-04-23
- 决策人：项目总控
- 当前 Phase：Phase 3.5
- 关联 Task：P3.5-T00
- 决策内容：
  - 在 Phase 3 后插入 Phase 3.5：Evo-Lite 安全经验沉淀与自评估闭环；
  - 不改模型权重，不接在线训练，不自动修改代码；
  - 将真实执行结果转化为可审计的评估、反思、经验记录和安全 workflow 模板；
  - workflow 和经验只能建议 planner，最终执行仍必须经过 policy engine、confirmation policy、validators 和白名单工具。
- 决策原因：
  - GuardedOps 已具备安全执行闭环后，需要把真实执行结果沉淀为可复用经验；
  - 经验沉淀有助于提升 planner 的稳定性和解释质量；
  - 采用不改权重路线可以避免训练、自动自修改和安全边界漂移风险。
- 替代方案：
  - LoRA / SFT / DPO；
  - 在线 RL 或自动强化学习；
  - 让系统根据 reflection 自动修改 policy、executor 或 workflow 执行逻辑。
- 为什么不选替代方案：
  - LoRA / SFT / DPO 需要训练数据、评估闭环和额外依赖，不适合当前 hackathon 安全演示节奏；
  - 在线 RL 会引入不可控探索和边界漂移，不符合“安全、可审计、可演示”的目标；
  - 自动修改 policy 或 executor 会破坏已锁定的安全边界，增加 raw shell 或绕过确认的风险。
- 影响范围：
  - 新增 Phase 3.5 任务链；
  - 更新 task_board、current_status、architecture_constraints、parallel_workstreams、validation_matrix 和 project_context；
  - 后续 planner 可读取经验和 workflow 建议，但执行层能力不扩大。
- 风险：
  - 经验或 workflow 被误用为最终安全决策来源；
  - 后续实现任务可能越界生成脚本或修改 policy/executor。
- 后续动作：
  - P3.5-T01：Execution Evaluator；
  - P3.5-T02：Experience Store；
  - P3.5-T03：Reflection Generator；
  - P3.5-T04：Safe Workflow Templates；
  - P3.5-T05：Workflow Retrieval in Planner；
  - P3.5-T06：Evo-Lite Orchestrator Hook；
  - P3.5-T07：Safety Regression Benchmark。
