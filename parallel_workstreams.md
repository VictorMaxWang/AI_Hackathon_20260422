# GuardedOps Parallel Workstreams

## 1. 总原则

GuardedOps 可以并行推进，但必须避免多个任务同时修改同一核心模块。

并行开发必须遵守：

- 每个任务绑定 Task ID；
- 每个任务限定文件范围；
- 每个任务开始前读取 `current_status.md`；
- 每个任务完成后更新 `task_board.md`；
- 不允许两个 Codex 线程同时修改同一个核心文件；
- 不允许并行修改 policy engine 和 user tools 的边界规则；
- 不允许在实现线程中临时改架构。

---

## 2. 并行组总览

| 并行组 | 阶段 | 说明 |
|---|---|---|
| G0 | Phase 0 | 总控与仓库初始化 |
| G1-CORE | Phase 1 | 核心模型与执行器 |
| G1-TOOLS | Phase 1 | 只读工具 |
| G1-UI | Phase 1 | CLI/Web 入口 |
| G1-QA | Phase 1 | 测试 |
| G2-POLICY | Phase 2 | 风控策略 |
| G2-WRITE | Phase 2 | 用户管理写操作 |
| G2-CONFIRM | Phase 2 | 确认机制 |
| G2-UI | Phase 2 | 风险与确认界面 |
| G2-QA | Phase 2 | 风控测试 |
| G3-MEMORY | Phase 3 | 上下文记忆 |
| G3-PLAN | Phase 3 | 多步计划 |
| G3-ORCH | Phase 3 | 连续任务编排 |
| G3-DOC | Phase 3 | LLM Stub 与 Prompt 文档 |
| G3.5-CONTROL | Phase 3.5 | Evo-Lite 总控文件 |
| G3.5-EVAL | Phase 3.5 | 执行结果评估 |
| G3.5-STORE | Phase 3.5 | 经验存储 |
| G3.5-REFLECT | Phase 3.5 | 安全反思 |
| G3.5-WORKFLOW | Phase 3.5 | 安全 workflow 模板 |
| G3.5-PLAN | Phase 3.5 | Planner 检索建议 |
| G3.5-ORCH | Phase 3.5 | Orchestrator 挂接 |
| G3.5-QA | Phase 3.5 | 安全回归 |
| G4-AUDIT | Phase 4 | 审计 |
| G4-DEMO | Phase 4 | 演示场景 |
| G4-DOC | Phase 4 | 验证矩阵 |
| G5-DOC | Phase 5 | 交付文档 |
| G5-REVIEW | Phase 5 | 安全审查 |
| G5-FINAL | Phase 5 | 最终冻结 |

---

## 3. 可独立并行任务

### Phase 1

在 P1-T02 Executor 底座完成后，可以并行：

- P1-T03 环境探测工具；
- P1-T04 只读工具实现。

注意：

- 两者共享 Executor 和 CommandResult；
- 不得修改 Executor 接口；
- 如果发现接口不够，必须先暂停并请求总控决策。

### Phase 2

在 P2-T01 风控引擎完成后，可以并行：

- P2-T03 确认状态机；
- P2-T04 高风险拒绝闭环。

注意：

- 两者都可能涉及 PolicyDecision；
- 不得擅自更改风险等级字段；
- UI 不得绕过确认状态机。

### Phase 3.5

在 P3.5-T00 总控更新完成后，可以并行：

- P3.5-T01 Execution Evaluator；
- P3.5-T02 Experience Store；
- P3.5-T04 Safe Workflow Templates。

注意：

- 三者都不得修改模型权重、policy、executor 或工具白名单；
- P3.5-T01 只定义评估输入输出与结果判定，不触发新执行能力；
- P3.5-T02 只存储可审计经验，不作为 allow / deny 来源；
- P3.5-T04 只沉淀白名单工具 workflow，不生成可执行脚本。

### Phase 5

文档任务可以并行：

- P5-T01 Agent 配置说明；
- P5-T02 工具能力定义文档；
- P5-T03 架构与安全说明。

注意：

- 文档必须基于实际实现；
- 不得写未实现能力；
- 不得夸大风控范围。

---

## 4. 可条件并行任务

### P1-T06 CLI 与 P1-T07 Web/API

条件：

- P1-T05 只读编排已经稳定；
- CLI 和 Web 不修改 orchestrator 核心逻辑；
- 只调用统一 API 或统一 service。

冲突风险：

- 同时修改 orchestrator；
- 同时修改 response schema。

避免方式：

- CLI 只增加入口文件；
- Web/API 只增加路由和静态页面；
- response schema 若需修改，先开独立小任务。

### P2-T05 Web 风险与确认界面

条件：

- P2-T03 确认状态机已完成；
- P2-T04 高风险拒绝已完成；
- 不重新定义风险等级。

---

## 5. 必须串行任务

以下任务必须串行：

1. P1-T01 → P1-T02  
   原因：Executor 依赖核心模型。

2. P1-T02 → P1-T03/P1-T04  
   原因：工具依赖执行器接口。

3. P1-T03/P1-T04 → P1-T05  
   原因：编排器需要调用具体工具。

4. P2-T01 → P2-T02  
   原因：用户管理工具必须先遵守风控约束。

5. P2-T01 → P2-T03  
   原因：确认状态机需要风险等级定义。

6. P3-T01 → P3-T02 → P3-T03  
   原因：连续任务依赖上下文和计划能力。

7. P3.5-T01 → P3.5-T03  
   原因：Reflection Generator 依赖 Execution Evaluator 的评估结果。

8. P3.5-T04 → P3.5-T05  
   原因：Planner 检索建议依赖 Safe Workflow Templates。

9. P3.5-T01/P3.5-T02/P3.5-T03/P3.5-T05 → P3.5-T06  
   原因：Evo-Lite Orchestrator Hook 必须在评估、经验、反思和 workflow 建议边界明确后再接入。

10. P3.5-T06 → P3.5-T07  
   原因：Safety Regression Benchmark 必须最后验证 hook 不绕过 policy、确认和白名单工具。

11. P4-T01 → P4-T02  
   原因：审计查询依赖审计存储结构。

12. P5-T04 → P5-T05 → P5-T06  
   原因：最终脚本、安全审查、冻结发布必须顺序进行。

---

## 6. 绝对不能并行的任务组合

| 任务组合 | 原因 |
|---|---|
| P2-T01 和 P2-T02 | 风控规则未稳定前不能实现用户删除 |
| P2-T03 和 P2-T05 | UI 不能先于确认状态机定义确认逻辑 |
| P3-T02 和 P3-T03 | Orchestrator 依赖 Planner 输出结构 |
| P3.5-T03 和 P3.5-T06 | reflection 未稳定前不得接入 orchestrator |
| P3.5-T05 和 P3.5-T06 | workflow retrieval 未稳定前不得接入 orchestrator |
| P3.5-T06 和 P3.5-T07 | hook 未完成前不能执行最终安全回归 |
| 任一 P3.5 任务和 policy/executor 边界修改 | Evo-Lite 只能沉淀经验，不得修改安全边界或执行能力 |
| P4-T01 和 P4-T02 | 查询 UI 依赖审计存储结构 |
| P5-T05 和 P5-T06 | 未完成安全审查不能冻结 |

---

## 7. 并行任务交接规则

每个并行任务完成后必须输出：

```text
Task ID：
修改文件：
新增文件：
未修改但依赖的文件：
测试结果：
可能影响的任务：
需要其他线程注意：
```

如果发现冲突，优先保留：

1. `architecture_constraints.md` 中的边界；
2. policy engine 的安全规则；
3. executor 的安全执行方式；
4. audit 的可追踪性；
5. UI 的便利性。

安全优先于功能便利。
