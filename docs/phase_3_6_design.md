# Phase 3.6 Design

## 1. 定位

Phase 3.6 是 GuardedOps 在 P0 ~ P3.5 之后的下一轮优化阶段：可信控制面、证据层与鲁棒闭环。

这一阶段的重点不是继续扩工具面，也不是扩大危险能力，而是提升 GuardedOps 在解释、确认、恢复、回归和控制面展示上的可信度、可审计性与可复现性。

当前聚焦方向：

- 安全解释与证据链；
- 确认绑定与执行闭包；
- 连续任务鲁棒性与断点续跑；
- 失败恢复建议；
- 经验治理；
- 可重放安全回归；
- 可视化可信控制面。

---

## 2. 硬约束

Phase 3.6 必须继续服从 GuardedOps 的既有安全边界：

- 不开放 arbitrary shell；
- 不开放 raw command mode；
- 不让 explanation / memory / workflow / reflection 绕过 policy；
- 不自动修改 policy / executor / 风控边界；
- 不自动生成可执行 shell 脚本；
- confirmation 必须绑定执行闭包；
- evidence 必须优先来自 trace / state assertion / policy events，而不是自由叙述；
- workflow 只能调用白名单工具；
- experience 必须有隔离、去重、晋升门禁；
- benchmark 必须支持回归和重放。

Phase 3.6 只增强可信控制与证据闭环，不扩大执行面，不改变系统能力边界。

---

## 3. 任务映射

| Task ID | 名称 | 设计目标 |
|---|---|---|
| P3.6-T00 | 更新总控文件并加入 Phase 3.6 | 把 Phase 3.6 正式写入总控体系 |
| P3.6-T01 | Evidence Layer Schema & Explanation Card Backend | 统一解释卡与证据层 schema，约束证据来源 |
| P3.6-T02 | Guarded Confirmation Token & Scope Binding | 让 confirmation token 绑定执行闭包、作用域与风险等级 |
| P3.6-T03 | Step Contracts, Drift Revalidation & Checkpoint Resume | 为连续任务建立 step contract、漂移重校验与断点续跑约束 |
| P3.6-T04 | Experience Governance Guardrails | 为 experience 建立隔离、去重、晋升门禁 |
| P3.6-T05 | Failure Recovery Taxonomy & Suggestion Engine | 归类失败并输出受控恢复建议 |
| P3.6-T06 | Replayable Safety Regression & Red-Team Harness | 建立可重放的安全回归与红队验证能力 |
| P3.6-T07 | Operator Control Panel UX I | 第一阶段控制面展示解释卡、证据、确认绑定与恢复建议 |
| P3.6-T08 | Operator Control Panel UX II | 第二阶段控制面展示 replay、blast radius 与 policy simulator |

---

## 4. 验证目标

Phase 3.6 后续实现必须能证明：

- explanation card 结构完整，且 evidence 引用可追溯；
- confirmation token 绑定执行闭包，不能跨范围复用；
- 连续任务在 drift 后会重新校验，而不是沿用过期上下文；
- checkpoint resume 保留 step contract、审计链和风险上下文；
- failure recovery suggestion 是受控建议，不是脚本生成器；
- replayable regression 能稳定重放关键安全路径；
- 控制面 UX 展示有真实证据支撑，不是表层叙述；
- blast radius 与 policy simulator 展示建立在真实 policy trace 基础上。

---

## 5. 与 P4/P5 的阶段关系

P4/P5 当前继续暂缓。

原因不是取消，而是当前更高 ROI 的工作仍在 Phase 3.6：

- 先补齐可信控制面、证据层和鲁棒闭环，后续 P4 的审计与演示材料才更有说服力；
- 先补齐 replayable regression 与失败恢复路径，后续 P5 的最终交付和答辩材料才更稳定；
- 若现在直接进入 P4/P5，会把解释链、确认绑定和回归能力的短板带入最终展示。

因此，Phase 3.6 是当前通向 P4/P5 的前置强化阶段，而不是新的能力扩张阶段。
